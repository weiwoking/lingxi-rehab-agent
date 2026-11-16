from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .scan_contracts import (
    PROFILE_IDS,
    ScanContractError,
    canonical_sha256,
    load_strict_json,
    sha256_file,
)
from .calibration import validate_calibration
from .errors import ErrorCode, PipelineError

PROFILE_FILES = (
    "model_manifest.json",
    "algorithm_profile.json",
    "scan_strategy_profile.json",
    "formal_stage2_frame_profile.json",
)


@dataclass(frozen=True)
class ProfileBundle:
    root: Path
    model_manifest: dict[str, Any]
    algorithm: dict[str, Any]
    scan_strategy: dict[str, Any]
    calibration: dict[str, Any]
    hashes: dict[str, str]

    @property
    def ids(self) -> dict[str, str]:
        # These are frozen P4 capture/input identities.  Internal promoted
        # Stage2/Stage3A/Geometry authority IDs and hashes are reported via
        # provenance and must not replace device protocol fields.
        return dict(PROFILE_IDS)

    def public_provenance(self) -> dict[str, Any]:
        return {
            "ids": self.ids,
            "sha256": self.hashes,
            "calibration_status": self.calibration["calibration_status"],
        }


def _schema(document: dict[str, Any], expected: str, filename: str) -> None:
    if document.get("schema_version") != expected:
        raise ScanContractError("E_PROFILE_BUNDLE", f"expected {expected}", f"{filename}.schema_version")


def load_profile_bundle(root: Path, *, validate_model_hashes: bool = True) -> ProfileBundle:
    root = root.resolve()
    if not root.is_dir():
        raise ScanContractError("E_PROFILE_BUNDLE", "profile bundle directory is missing", "profile_bundle")
    missing = [name for name in PROFILE_FILES if not (root / name).is_file()]
    if missing:
        raise ScanContractError("E_PROFILE_BUNDLE", "required profile files are missing", "profile_bundle", missing)
    documents = {name: load_strict_json(root / name, code="E_PROFILE_BUNDLE") for name in PROFILE_FILES}
    _schema(documents["model_manifest.json"], "deltar_pc_model_manifest.v1", "model_manifest.json")
    _schema(documents["algorithm_profile.json"], "deltar_algorithm_profile.v1", "algorithm_profile.json")
    _schema(documents["scan_strategy_profile.json"], "deltar_scan_strategy.v1", "scan_strategy_profile.json")
    _schema(documents["formal_stage2_frame_profile.json"], "deltar_geometry_calibration.v1", "formal_stage2_frame_profile.json")
    calibration = documents["formal_stage2_frame_profile.json"]
    try:
        validate_calibration(calibration)
    except PipelineError as exc:
        code = "E_PROFILE_SCHEMA" if exc.code == str(ErrorCode.CALIBRATION_SCHEMA) else "E_PROFILE_SEMANTIC"
        raise ScanContractError(code, exc.message, exc.field, exc.details) from exc
    algorithm = documents["algorithm_profile.json"]
    if algorithm.get("segmentation_primary") != {"region_method": "segment_eroded_3px", "statistic_method": "mad_median"}:
        raise ScanContractError("E_PROFILE_BUNDLE", "Stage 3.1 segmentation method is not frozen", "segmentation_primary")
    if algorithm.get("detection_fallback") != {"region_method": "bbox_full", "statistic_method": "median"}:
        raise ScanContractError("E_PROFILE_BUNDLE", "Stage 3.1 detection method is not frozen", "detection_fallback")
    model_manifest = documents["model_manifest.json"]
    for model in model_manifest.get("models", []):
        if model.get("task") not in {"depth", "detect", "segment"} or type(model.get("path")) is not str or type(model.get("sha256")) is not str:
            raise ScanContractError("E_PROFILE_BUNDLE", "invalid model manifest entry", "models")
        path = Path(model["path"])
        if not path.is_absolute():
            path = root / path
        model["path"] = str(path.resolve())
        if validate_model_hashes:
            path = Path(model["path"])
            if not path.is_file():
                raise ScanContractError("E_PROFILE_HASH", "model file is missing", f"models.{model.get('task')}.path")
            actual = sha256_file(path)
            if actual != model["sha256"].lower():
                raise ScanContractError("E_PROFILE_HASH", "model SHA-256 mismatch", f"models.{model.get('task')}.sha256", {"expected": model["sha256"], "actual": actual})
    hashes = {name: sha256_file(root / name) for name in PROFILE_FILES}
    bundle = ProfileBundle(root, model_manifest, algorithm, documents["scan_strategy_profile.json"], calibration, hashes)
    if len(set(bundle.ids.values())) != len(bundle.ids):
        raise ScanContractError("E_PROFILE_BUNDLE", "profile identifiers must be distinct", "profile_ids")
    return bundle


def runtime_summary() -> dict[str, str]:
    result = {"python": platform.python_version(), "platform": platform.platform()}
    try:
        import torch

        result["pytorch"] = str(torch.__version__)
        result["compute"] = str(torch.cuda.get_device_name(0)) if torch.cuda.is_available() else "cpu"
    except Exception:
        result["pytorch"] = "unavailable"
        result["compute"] = "unknown"
    try:
        import ultralytics

        result["ultralytics"] = str(ultralytics.__version__)
    except Exception:
        result["ultralytics"] = "worker_process"
    return result
