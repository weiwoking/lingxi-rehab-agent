from __future__ import annotations

import hashlib
import json
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch

from .canvas import apply_canvas
from .deltar_bridge import DeltarBridge, EXPECTED_CHECKPOINT_SHA256
from .frame_pipeline import FramePipeline
from .frozen_runtime_contracts import (
    DEFAULT_DELTAR_CHECKPOINT,
    DEFAULT_DELTAR_WORKTREE,
    ScanJobCancelled,
    ScanJobRequest,
    identity_hints_unchanged,
)
from .scan_contracts import sha256_file
from .scan_profiles import PROFILE_FILES, ProfileBundle, load_profile_bundle
from .tof_adapter import adapt_inputs


@dataclass(frozen=True)
class SharedRuntimeResources:
    bundle: ProfileBundle
    bridge: DeltarBridge
    identity: dict[str, Any]
    identity_hints: dict[str, dict[str, Any]]
    startup_timing: dict[str, float]
    generation: int


def _file_identity(path: Path, *, include_sha256: bool) -> dict[str, Any]:
    resolved = path.resolve()
    stat = resolved.stat()
    value: dict[str, Any] = {
        "path": str(resolved),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    if include_sha256:
        value["sha256"] = sha256_file(resolved)
    return value


def _directory_identity(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    identities: dict[str, Any] = {}
    hints: dict[str, dict[str, Any]] = {}
    for name in sorted(path.name for path in root.iterdir() if path.is_file() and path.suffix == ".json"):
        identity = _file_identity(root / name, include_sha256=True)
        identities[name] = identity
        hints[str((root / name).resolve())] = {key: identity[key] for key in ("size", "mtime_ns")}
    return identities, hints


def build_runtime_identity(
    profile_dir: Path,
    bridge: DeltarBridge,
    calibration_paths: list[Path],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    profiles, hints = _directory_identity(profile_dir.resolve())
    calibrations: dict[str, Any] = {}
    for path in calibration_paths:
        identity = _file_identity(path, include_sha256=True)
        calibrations[str(path.resolve())] = identity
        hints[str(path.resolve())] = {key: identity[key] for key in ("size", "mtime_ns")}
    checkpoint = _file_identity(bridge.checkpoint, include_sha256=False)
    checkpoint["sha256"] = EXPECTED_CHECKPOINT_SHA256
    hints[str(bridge.checkpoint)] = {key: checkpoint[key] for key in ("size", "mtime_ns")}
    executable = _file_identity(Path(sys.executable), include_sha256=False)
    hints[str(Path(sys.executable).resolve())] = {key: executable[key] for key in ("size", "mtime_ns")}
    canonical = {
        "python": {**executable, "version": platform.python_version()},
        "deltar_worktree": {"path": str(bridge.worktree), "commit": bridge.worktree_commit},
        "checkpoint": checkpoint,
        "profiles": profiles,
        "calibrations": calibrations,
        "device": bridge.device,
        "torch": str(torch.__version__),
        "cuda": str(torch.version.cuda),
    }
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {**canonical, "runtime_sha256": digest}, hints


def _warmup(bundle: ProfileBundle, bridge: DeltarBridge) -> None:
    # Exercise the two exact production canvas shapes without writing artifacts.
    rgb = np.zeros((360, 640, 3), dtype=np.uint8)
    tof = {
        "distance_mm": [2000.0] * 64,
        "range_sigma_mm": [50.0] * 64,
        "target_status": [5] * 64,
        "nb_target_detected": [1] * 64,
    }
    pipeline = FramePipeline(bundle, Path.cwd(), device=bridge.device, shared_bridge=bridge)
    for strategy in ("direct_640x360", "letterbox_640x480"):
        profile = pipeline._adapter_profile(strategy)
        canvas = apply_canvas(rgb, strategy, [0, 0, 0])
        inputs = adapt_inputs(
            canvas.rgb, canvas, tof, profile, bridge.worktree,
            pipeline.formal_authority, device=bridge.device
        )
        bridge.infer_twice(inputs, profile["seed"])


def create_shared_runtime_resources(
    profile_dir: Path,
    *,
    device: str,
    generation: int,
    calibration_paths: list[Path] | None = None,
    worktree: Path = DEFAULT_DELTAR_WORKTREE,
    checkpoint: Path = DEFAULT_DELTAR_CHECKPOINT,
    process_start: float | None = None,
    imports_ms: float = 0.0,
) -> SharedRuntimeResources:
    process_started = process_start if process_start is not None else time.perf_counter()
    static_started = time.perf_counter()
    bundle = load_profile_bundle(profile_dir, validate_model_hashes=True)
    static_config_load_ms = (time.perf_counter() - static_started) * 1000.0

    identity_started = time.perf_counter()
    bridge = DeltarBridge(worktree, checkpoint, device=device)
    checkpoint_identity_ms = (time.perf_counter() - identity_started) * 1000.0

    bridge.preload()
    warmup_started = time.perf_counter()
    _warmup(bundle, bridge)
    warmup_ms = (time.perf_counter() - warmup_started) * 1000.0

    identity, hints = build_runtime_identity(profile_dir, bridge, calibration_paths or [])
    timing = {
        "process_start_ms": 0.0,
        "imports_ms": float(imports_ms),
        "static_config_load_ms": static_config_load_ms,
        "checkpoint_identity_ms": checkpoint_identity_ms,
        "model_construct_ms": bridge.model_construct_seconds * 1000.0,
        "checkpoint_load_ms": bridge.checkpoint_load_seconds * 1000.0,
        "cuda_init_ms": bridge.cuda_init_seconds * 1000.0,
        "warmup_ms": warmup_ms,
        "runtime_ready_total_ms": (time.perf_counter() - process_started) * 1000.0,
    }
    return SharedRuntimeResources(bundle, bridge, identity, hints, timing, generation)
