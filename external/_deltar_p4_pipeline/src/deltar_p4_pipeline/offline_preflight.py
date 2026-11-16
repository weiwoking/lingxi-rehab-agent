from __future__ import annotations

import json
import os
import socket
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import torch

from .deltar_bridge import DeltarBridge, sha256_file
from .scan_profiles import load_profile_bundle
from .formal_geometry_authority import load_formal_geometry_authority


PREFLIGHT_SCHEMA = "deltar_local_runtime_preflight.v1"
URL_PREFIXES = ("http://", "https://", "hf://", "s3://", "gs://")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def require_local_file(path_value: str | Path, label: str) -> Path:
    raw = str(path_value).strip()
    if raw.lower().startswith(URL_PREFIXES):
        raise ValueError(f"{label} must be an absolute local file, not a URL")
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute local file")
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} is missing")
    return resolved


@contextmanager
def deny_external_network(attempts: list[dict[str, Any]]) -> Iterator[None]:
    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo

    def blocked_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        attempts.append({"operation": "create_connection", "target": str(address)[:160]})
        raise OSError("external network is disabled for DELTAR offline preflight")

    def blocked_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        attempts.append({"operation": "getaddrinfo", "target": str(host)[:160]})
        raise OSError("DNS is disabled for DELTAR offline preflight")

    socket.create_connection = blocked_create_connection
    socket.getaddrinfo = blocked_getaddrinfo
    try:
        yield
    finally:
        socket.create_connection = original_create_connection
        socket.getaddrinfo = original_getaddrinfo


def _asset(path_value: str | Path, label: str, expected_sha256: str | None = None) -> dict[str, Any]:
    path = require_local_file(path_value, label)
    digest = sha256_file(path)
    if expected_sha256 is not None and digest != expected_sha256.lower():
        raise ValueError(f"{label} SHA-256 mismatch")
    return {
        "label": label,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": digest,
        "local": True,
    }


def run_preflight(
    *,
    pipeline_root: Path,
    profile_bundle: Path,
    worktree: Path,
    checkpoint: Path,
    device: str = "cuda:0",
    deny_network: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter()
    checked_at = utc_now()
    attempts: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "schema_version": PREFLIGHT_SCHEMA,
        "status": "failed",
        "ready": False,
        "checked_at": checked_at,
        "processing_mode": "local_only",
        "runtime_network_required": False,
        "network_deny_active": deny_network,
        "network_attempts": attempts,
        "assets": [],
        "checkpoint_key_audit": None,
        "runtime_import_paths": {},
        "timing_seconds": {},
        "error": None,
    }
    try:
        root = pipeline_root.resolve()
        if not root.is_dir():
            raise FileNotFoundError("pipeline root is missing")
        bundle = load_profile_bundle(profile_bundle, validate_model_hashes=True)
        authority = load_formal_geometry_authority(
            profile_bundle / "formal_geometry_v2"
        )
        models = {item["task"]: item for item in bundle.model_manifest["models"]}
        if set(models) != {"depth", "detect", "segment"}:
            raise ValueError("model manifest must contain depth/detect/segment exactly once")
        assets = [
            _asset(models["depth"]["path"], "DELTAR checkpoint", models["depth"]["sha256"]),
            _asset(models["detect"]["path"], "YOLO detect", models["detect"]["sha256"]),
            _asset(models["segment"]["path"], "YOLO segment", models["segment"]["sha256"]),
        ]
        for name, item in authority.manifest["payloads"].items():
            assets.append(
                _asset(
                    authority.root / item["path"],
                    f"formal geometry authority {name}",
                    item["promoted_sha256"],
                )
            )
        result["assets"] = assets
        result["cuda"] = {
            "required": device.startswith("cuda"),
            "available": bool(torch.cuda.is_available()),
            "device": device,
            "name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        }
        guard = deny_external_network(attempts) if deny_network else nullcontext()
        with guard:
            bridge = DeltarBridge(worktree, checkpoint, device=device)
            bridge._load_model()
        provenance = bridge.provenance()
        audit = provenance.get("checkpoint_key_audit") or {}
        if audit.get("missing_keys") or audit.get("unexpected_keys") or audit.get("shape_mismatches"):
            raise ValueError("checkpoint strict coverage audit failed")
        result["checkpoint_key_audit"] = audit
        result["runtime_import_paths"] = {
            key: provenance.get(key)
            for key in (
                "worktree_path",
                "encoder_source_path",
                "model_module_path",
                "model_io_module_path",
                "dataloader_utils_module_path",
            )
        }
        result["source"] = {
            "worktree_commit": provenance["worktree_commit"],
            "checkpoint_sha256": provenance["checkpoint_sha256"],
            "encoder_pretrained": False,
            "formal_geometry": authority.provenance(),
        }
        result["timing_seconds"] = {
            "model_construct": bridge.model_construct_seconds,
            "checkpoint_load": bridge.checkpoint_load_seconds,
            "preflight": time.perf_counter() - started,
        }
        result["status"] = "ready"
        result["ready"] = True
    except Exception as exc:
        result["error"] = {
            "error_type": type(exc).__name__,
            "message": " ".join(str(exc).split())[:500],
        }
        result["timing_seconds"]["preflight"] = time.perf_counter() - started
    result["network_attempts"] = attempts
    return result


def atomic_write_result(path: Path, result: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
