from __future__ import annotations

import ast
import hashlib
import importlib
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import numpy as np
import torch
import torch.nn.functional as F

from .errors import ErrorCode, PipelineError
from .formal_deltar_d import activate_formal_d, fusion_audits

if TYPE_CHECKING:
    from .tof_adapter import DeltarInputs

EXPECTED_CHECKPOINT_SHA256 = "a402ffbffade70f0a0ef77a28b465d8fb625dd2fcdef0115b65c98193ce1e3ef"
EXPECTED_WORKTREE_COMMIT = "f3c517562bcb2dfb9e4bbf74965f2ee76a2dd096"
RELATED_OFFICIAL_MODULES = (
    "src",
    "src.models",
    "src.models.deltar",
    "src.utils",
    "src.utils.model_io",
    "src.utils.dataloader",
    "src.dataloader",
)


@dataclass(frozen=True)
class InferenceResult:
    depth_first: np.ndarray
    depth_second: np.ndarray
    timings: dict[str, float]
    repeatability: dict[str, Any]
    raw_shapes: dict[str, list[int]]
    gpu_peak_bytes: int
    fusion_audits: dict[str, Any]


@dataclass(frozen=True)
class CheckpointKeyAudit:
    model_state_keys: int
    checkpoint_state_keys: int
    missing_keys: tuple[str, ...]
    unexpected_keys: tuple[str, ...]
    shape_mismatches: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_state_keys": self.model_state_keys,
            "checkpoint_state_keys": self.checkpoint_state_keys,
            "missing_keys": list(self.missing_keys),
            "unexpected_keys": list(self.unexpected_keys),
            "shape_mismatches": list(self.shape_mismatches),
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _module_path(module: ModuleType) -> Path | None:
    value = getattr(module, "__file__", None)
    return Path(value).resolve() if value else None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def ensure_module_source(name: str, module: ModuleType, worktree: Path) -> Path:
    path = _module_path(module)
    namespace_paths = [Path(value).resolve() for value in getattr(module, "__path__", ())]
    namespace_valid = path is None and bool(namespace_paths) and all(_inside(value, worktree.resolve()) for value in namespace_paths)
    if (path is None and not namespace_valid) or (path is not None and not _inside(path, worktree.resolve())):
        raise PipelineError(
            ErrorCode.DELTAR_MODULE_SOURCE,
            f"official module {name!r} is not loaded from the configured DELTAR worktree",
            name,
            {
                "expected_root": str(worktree.resolve()),
                "actual": str(path) if path else None,
                "namespace_paths": [str(value) for value in namespace_paths],
            },
        )
    return path if path is not None else namespace_paths[0]


def validate_cached_module_sources(worktree: Path) -> None:
    for name, module in tuple(sys.modules.items()):
        if module is not None and (name == "src" or name.startswith("src.")):
            ensure_module_source(name, module, worktree)


def validate_offline_encoder_source(worktree: Path) -> Path:
    encoder = (worktree / "src" / "models" / "encoder.py").resolve()
    try:
        source = encoder.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(encoder))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise PipelineError(
            ErrorCode.DELTAR_OFFLINE_ASSET_MISSING,
            f"cannot inspect DELTAR encoder source: {type(exc).__name__}: {exc}",
            "encoder_source",
        ) from exc
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if (
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "timm"
            and node.func.attr == "create_model"
        ):
            calls.append(node)
    if len(calls) != 1:
        raise PipelineError(
            ErrorCode.DELTAR_MODEL_CONSTRUCT,
            "DELTAR encoder must contain exactly one auditable timm.create_model call",
            "encoder_source",
            {"call_count": len(calls)},
        )
    pretrained = next(
        (keyword.value for keyword in calls[0].keywords if keyword.arg == "pretrained"),
        None,
    )
    if not isinstance(pretrained, ast.Constant) or pretrained.value is not False:
        raise PipelineError(
            ErrorCode.DELTAR_MODEL_CONSTRUCT,
            "DELTAR encoder is not offline-safe: pretrained must be the literal False",
            "encoder_source",
        )
    return encoder


def audit_checkpoint_state(
    model: torch.nn.Module, state_dict: dict[str, Any]
) -> CheckpointKeyAudit:
    model_state = model.state_dict()
    model_keys = set(model_state)
    checkpoint_keys = set(state_dict)
    missing = tuple(sorted(model_keys - checkpoint_keys))
    unexpected = tuple(sorted(checkpoint_keys - model_keys))
    mismatches: list[dict[str, Any]] = []
    for key in sorted(model_keys & checkpoint_keys):
        expected = model_state[key]
        actual = state_dict[key]
        expected_shape = tuple(getattr(expected, "shape", ()))
        actual_shape = tuple(getattr(actual, "shape", ()))
        if expected_shape != actual_shape:
            mismatches.append(
                {
                    "key": key,
                    "model_shape": list(expected_shape),
                    "checkpoint_shape": list(actual_shape),
                }
            )
    return CheckpointKeyAudit(
        model_state_keys=len(model_keys),
        checkpoint_state_keys=len(checkpoint_keys),
        missing_keys=missing,
        unexpected_keys=unexpected,
        shape_mismatches=tuple(mismatches),
    )


def import_official_module(name: str, worktree: Path) -> ModuleType:
    root = worktree.resolve()
    validate_cached_module_sources(root)
    root_text = str(root)
    filtered: list[str] = []
    for entry in sys.path:
        resolved_entry = Path(entry or ".").resolve()
        if resolved_entry == root:
            continue
        if (resolved_entry / "src").is_dir():
            continue
        filtered.append(entry)
    sys.path[:] = filtered
    sys.path.insert(0, root_text)
    module = importlib.import_module(name)
    ensure_module_source(name, module, root)
    validate_cached_module_sources(root)
    return module


class DeltarBridge:
    def __init__(self, worktree: Path, checkpoint: Path, device: str = "cuda:0"):
        self.worktree = worktree.resolve()
        self.checkpoint = checkpoint.resolve()
        self.device = device
        self._model = None
        self.model_load_seconds = 0.0
        self.model_construct_seconds = 0.0
        self.checkpoint_load_seconds = 0.0
        self.cuda_init_seconds = 0.0
        self.worktree_commit = ""
        self.encoder_source: Path | None = None
        self.checkpoint_audit: CheckpointKeyAudit | None = None
        self.formal_d_activation: dict[str, Any] | None = None
        self._validate_assets()

    def _validate_assets(self) -> None:
        if not (self.worktree / "src" / "models" / "deltar.py").is_file():
            raise PipelineError(ErrorCode.DELTAR_ASSET, "official DELTAR worktree is missing", str(self.worktree))
        try:
            completed = subprocess.run(
                ["git", "-C", str(self.worktree), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise PipelineError(ErrorCode.DELTAR_WORKTREE_COMMIT, f"failed to inspect DELTAR worktree commit: {exc}", str(self.worktree)) from exc
        commit = completed.stdout.strip().lower()
        if completed.returncode != 0 or commit != EXPECTED_WORKTREE_COMMIT:
            raise PipelineError(
                ErrorCode.DELTAR_WORKTREE_COMMIT,
                "DELTAR worktree commit does not match the frozen contract",
                str(self.worktree),
                {"expected": EXPECTED_WORKTREE_COMMIT, "actual": commit or None, "git_stderr": completed.stderr.strip()},
            )
        self.worktree_commit = commit
        self.encoder_source = validate_offline_encoder_source(self.worktree)
        validate_cached_module_sources(self.worktree)
        if not self.checkpoint.is_file():
            raise PipelineError(
                ErrorCode.DELTAR_OFFLINE_ASSET_MISSING,
                "local DELTAR checkpoint is missing",
                "checkpoint",
            )
        try:
            actual = sha256_file(self.checkpoint)
        except OSError as exc:
            raise PipelineError(ErrorCode.CHECKPOINT, f"failed to read checkpoint: {exc}", str(self.checkpoint)) from exc
        if actual != EXPECTED_CHECKPOINT_SHA256:
            raise PipelineError(
                ErrorCode.DELTAR_CHECKPOINT_MISMATCH,
                "local DELTAR checkpoint SHA-256 mismatch",
                "checkpoint",
                {"expected": EXPECTED_CHECKPOINT_SHA256, "actual": actual},
            )
        if self.device.startswith("cuda") and not torch.cuda.is_available():
            raise PipelineError(ErrorCode.CUDA_UNAVAILABLE, "CUDA is not available", "device")

    def _read_checkpoint_state(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            state = torch.load(
                self.checkpoint,
                map_location="cpu",
                weights_only=True,
            )
        except Exception as exc:
            raise PipelineError(
                ErrorCode.DELTAR_CHECKPOINT_MISMATCH,
                f"failed to read local DELTAR checkpoint: {type(exc).__name__}: {exc}",
                "checkpoint",
            ) from exc
        finally:
            self.checkpoint_load_seconds = time.perf_counter() - started
        if not isinstance(state, dict) or not state:
            raise PipelineError(
                ErrorCode.DELTAR_CHECKPOINT_MISMATCH,
                "local DELTAR checkpoint is not a non-empty state_dict",
                "checkpoint",
            )
        return state

    def _load_model(self):
        if self._model is not None:
            return self._model
        original_argv = sys.argv[:]
        try:
            # Upstream src.config parses argv during import; isolate it from this PoC CLI.
            sys.argv = ["deltar-inference"]
            module = import_official_module("src.models.deltar", self.worktree)
            import_official_module("src.utils.model_io", self.worktree)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(ErrorCode.DELTAR_ASSET, f"failed to import official DELTAR core: {exc}", str(self.worktree)) from exc
        finally:
            sys.argv = original_argv
        started = time.perf_counter()
        try:
            construct_started = time.perf_counter()
            model = module.Deltar(n_bins=256, min_val=1e-3, max_val=10.0, norm="linear")
            self.model_construct_seconds = time.perf_counter() - construct_started
            state = self._read_checkpoint_state()
            audit = audit_checkpoint_state(model, state)
            self.checkpoint_audit = audit
            if audit.missing_keys or audit.unexpected_keys or audit.shape_mismatches:
                raise PipelineError(
                    ErrorCode.DELTAR_CHECKPOINT_MISMATCH,
                    "local DELTAR checkpoint does not completely cover the model",
                    "checkpoint",
                    audit.as_dict(),
                )
            model.load_state_dict(state, strict=True)
            self.formal_d_activation = activate_formal_d(model)
            cuda_started = time.perf_counter()
            model = model.to(self.device).eval()
            if self.device.startswith("cuda"):
                torch.cuda.synchronize()
            self.cuda_init_seconds = time.perf_counter() - cuda_started
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                ErrorCode.DELTAR_MODEL_CONSTRUCT,
                f"failed to construct the local DELTAR model: {type(exc).__name__}: {exc}",
                "deltar_model",
            ) from exc
        self.model_load_seconds = time.perf_counter() - started
        self._model = model
        return model

    def preload(self) -> None:
        """Load the frozen authority model without performing formal inference."""
        self._load_model()

    def provenance(self) -> dict[str, Any]:
        aliases = {
            "model_module_path": "src.models.deltar",
            "model_io_module_path": "src.utils.model_io",
            "dataloader_utils_module_path": "src.utils.dataloader",
        }
        paths: dict[str, Any] = {}
        for output_key, module_name in aliases.items():
            module = sys.modules.get(module_name)
            paths[output_key] = str(ensure_module_source(module_name, module, self.worktree)) if module is not None else None
        return {
            "worktree_path": str(self.worktree),
            "worktree_commit": self.worktree_commit,
            "checkpoint_path": str(self.checkpoint),
            "checkpoint_sha256": EXPECTED_CHECKPOINT_SHA256,
            "encoder_source_path": str(self.encoder_source) if self.encoder_source else None,
            "processing_mode": "local_only",
            "runtime_network_required": False,
            "checkpoint_key_audit": (
                self.checkpoint_audit.as_dict() if self.checkpoint_audit else None
            ),
            "formal_fusion": self.formal_d_activation,
            **paths,
        }

    @staticmethod
    def _input_dict(inputs: DeltarInputs) -> dict[str, Any]:
        return {
            "rgb": inputs.rgb,
            "additional": {
                "hist_data": inputs.hypotheses,
                "rect_data": inputs.fr,
                "mask": inputs.mask,
                "patch_info": inputs.patch_info,
            },
        }

    @staticmethod
    def _postprocess(pred: torch.Tensor, shape: tuple[int, int]) -> np.ndarray:
        clipped = torch.clamp(pred.detach().cpu(), min=1e-3, max=10.0)
        dense = F.interpolate(clipped, size=shape, mode="bilinear", align_corners=True)
        return dense.squeeze().numpy().astype(np.float32, copy=False)

    def infer_twice(self, inputs: DeltarInputs, seed: int) -> InferenceResult:
        from .tof_adapter import seed_everything

        validate_cached_module_sources(self.worktree)
        model = self._load_model()
        payload = self._input_dict(inputs)
        if self.device.startswith("cuda"):
            torch.cuda.reset_peak_memory_stats()
        outputs: list[np.ndarray] = []
        forward_times: list[float] = []
        total_times: list[float] = []
        raw_shapes: dict[str, list[int]] = {}
        try:
            with torch.inference_mode():
                for _ in range(2):
                    seed_everything(seed)
                    if self.device.startswith("cuda"):
                        torch.cuda.synchronize()
                    total_start = time.perf_counter()
                    forward_start = total_start
                    bins, pred = model(payload)
                    if self.device.startswith("cuda"):
                        torch.cuda.synchronize()
                    forward_times.append((time.perf_counter() - forward_start) * 1000.0)
                    outputs.append(self._postprocess(pred, tuple(inputs.rgb.shape[-2:])))
                    total_times.append((time.perf_counter() - total_start) * 1000.0)
                    raw_shapes = {"bin_edges": list(bins.shape), "prediction": list(pred.shape)}
        except Exception as exc:
            raise PipelineError(
                ErrorCode.DELTAR_INFERENCE,
                f"official DELTAR inference failed: {type(exc).__name__}: {exc}",
                "deltar",
            ) from exc
        first, second = outputs
        raw_first = hashlib.sha256(first.tobytes(order="C")).hexdigest()
        raw_second = hashlib.sha256(second.tobytes(order="C")).hexdigest()
        delta = np.abs(first.astype(np.float64) - second.astype(np.float64))
        repeatability = {
            "float_exact_equal": bool(np.array_equal(first, second)),
            "max_absolute_difference_m": float(delta.max()),
            "mean_absolute_difference_m": float(delta.mean()),
            "raw_sha256_first": raw_first,
            "raw_sha256_second": raw_second,
        }
        peak = int(torch.cuda.max_memory_allocated()) if self.device.startswith("cuda") else 0
        return InferenceResult(
            first,
            second,
            {
                "model_load_ms": self.model_load_seconds * 1000.0,
                "forward_first_ms": forward_times[0],
                "forward_second_ms": forward_times[1],
                "model_plus_postprocess_first_ms": total_times[0],
                "model_plus_postprocess_second_ms": total_times[1],
            },
            repeatability,
            raw_shapes,
            peak,
            fusion_audits(model),
        )
