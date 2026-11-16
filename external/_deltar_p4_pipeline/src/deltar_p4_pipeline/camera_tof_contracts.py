from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

CAPTURE_SCHEMA = "camera_tof_capture.v1"
PROFILE_SCHEMA = "camera_tof_extrinsics.v1"
REPORT_SCHEMA = "camera_tof_calibration_report.v1"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")

CAPTURE_KEYS = {
    "schema_version", "capture_id", "created_at", "camera_profile",
    "camera_profile_sha256", "board_manifest", "board_manifest_sha256",
    "backer", "poses",
}
BACKER_KEYS = {"width_mm", "height_mm", "material", "matte", "opaque"}
POSE_KEYS = {
    "pose_name", "split", "scan_dir", "stationary_confirmed",
    "estimated_distance_mm", "tilt_hint", "image_region_hint", "notes",
}
PROFILE_KEYS = {
    "schema_version", "profile_id", "created_at", "camera_profile_id",
    "camera_resolution", "tof_model", "tof_resolution", "tof_frequency_hz",
    "transform_direction", "matrix_convention", "T_camera_from_tof",
    "rotation_matrix", "translation", "translation_unit", "orientation",
    "orientation_resolution_status", "zone_index_contract", "ray_model",
    "distance_semantics", "zone_rays", "zone_valid_mask",
    "camera_tof_overlap_mask", "solver_method", "training_pose_count",
    "heldout_pose_count", "training_point_count", "heldout_point_count",
    "residual_summary", "candidate_margin", "gauge_equivalence",
    "mechanical_prior", "mechanical_prior_is_measured", "validation_status",
    "camera_tof_extrinsics", "absolute_geometry_valid", "world_pose_resolved",
    "source_data_hashes", "warnings", "unresolved_fields",
}
ORIENTATION_KEYS = {"name", "raw_index_for_canonical_index"}
RAY_MODEL_KEYS = {"name", "hfov_deg", "vfov_deg", "provenance", "distance_semantics"}
SUMMARY_KEYS = {"count", "mean_mm", "median_mm", "rms_mm", "p95_mm", "max_mm"}
MARGIN_KEYS = {"absolute_mm", "relative", "next_group", "gauge_candidate_ids"}
GAUGE_KEYS = {"present", "candidate_ids", "canonicalization"}
MECHANICAL_KEYS = {
    "tof_is_camera_right", "expected_tx_mm", "expected_abs_ty_mm",
    "expected_abs_tz_mm", "usage",
}
REPORT_KEYS = {
    "schema_version", "created_at", "capture_id", "status",
    "validation_status", "selected_candidate", "training_pose_count",
    "heldout_pose_count", "gates", "warnings", "formal_profile_generated",
    "camera_tof_extrinsics_status", "absolute_geometry_valid",
    "world_pose_resolved", "tool_version",
}


@dataclass(frozen=True)
class CameraTofError(Exception):
    code: str
    message: str
    field: str | None = None
    details: Any = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" [{self.field}]" if self.field else "")

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code, "message": self.message,
            "field": self.field, "details": self.details,
        }


def fail(code: str, message: str, field: str | None = None, details: Any = None) -> None:
    raise CameraTofError(code, message, field, details)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def _assert_finite(value: Any, field: str = "$") -> None:
    if type(value) is float and not math.isfinite(value):
        fail("E_CAMERA_TOF_JSON", "JSON contains a non-finite number", field)
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_finite(item, f"{field}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_finite(item, f"{field}[{index}]")


def load_strict_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        fail("E_CAMERA_TOF_JSON", f"cannot read strict UTF-8 JSON: {exc}", str(path))
    if type(value) is not dict:
        fail("E_CAMERA_TOF_JSON", "JSON root must be an object", str(path))
    _assert_finite(value)
    return value


def write_json(path: Path, value: Any) -> None:
    _assert_finite(value)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _exact_keys(value: dict[str, Any], expected: set[str], field: str) -> None:
    actual = set(value)
    if actual != expected:
        fail(
            "E_CAMERA_TOF_SCHEMA", "object fields do not match the closed-world schema", field,
            {"missing": sorted(expected - actual), "unknown": sorted(actual - expected)},
        )


def _positive_number(value: Any, field: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(float(value)) or float(value) <= 0:
        fail("E_CAMERA_TOF_SCHEMA", "must be a positive finite number", field)
    return float(value)


def _require_rfc3339(value: Any, field: str) -> None:
    if type(value) is not str:
        fail("E_CAMERA_TOF_SCHEMA", "must be an RFC3339 timestamp", field)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        fail("E_CAMERA_TOF_SCHEMA", "must be an RFC3339 timestamp", field, str(exc))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        fail("E_CAMERA_TOF_SCHEMA", "timestamp must include Z or an explicit UTC offset", field)


def _path_from_manifest(value: str, root: Path, field: str) -> Path:
    if type(value) is not str or not value:
        fail("E_CAMERA_TOF_SCHEMA", "must be a non-empty path string", field)
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        fail("E_CAMERA_TOF_PATH", f"path does not exist: {exc}", field, str(path))
    return resolved


def validate_capture_manifest(
    value: dict[str, Any], *, manifest_path: Path,
    camera_profile_path: Path | None = None, board_manifest_path: Path | None = None,
) -> dict[str, Any]:
    _exact_keys(value, CAPTURE_KEYS, "$")
    if value["schema_version"] != CAPTURE_SCHEMA:
        fail("E_CAMERA_TOF_SCHEMA", f"schema_version must be {CAPTURE_SCHEMA}", "schema_version")
    if type(value["capture_id"]) is not str or ID_RE.fullmatch(value["capture_id"]) is None:
        fail("E_CAMERA_TOF_SCHEMA", "capture_id must be a safe identifier", "capture_id")
    _require_rfc3339(value["created_at"], "created_at")
    if type(value["backer"]) is not dict:
        fail("E_CAMERA_TOF_SCHEMA", "backer must be an object", "backer")
    _exact_keys(value["backer"], BACKER_KEYS, "backer")
    _positive_number(value["backer"]["width_mm"], "backer.width_mm")
    _positive_number(value["backer"]["height_mm"], "backer.height_mm")
    if type(value["backer"]["material"]) is not str or not value["backer"]["material"]:
        fail("E_CAMERA_TOF_SCHEMA", "material must be a non-empty string", "backer.material")
    if value["backer"]["matte"] is not True or value["backer"]["opaque"] is not True:
        fail("E_CAMERA_TOF_SCHEMA", "backer must be explicitly matte and opaque", "backer")
    root = manifest_path.resolve().parent
    declared_camera = _path_from_manifest(value["camera_profile"], root, "camera_profile")
    declared_board = _path_from_manifest(value["board_manifest"], root, "board_manifest")
    for path, declared, field in (
        (declared_camera, value["camera_profile_sha256"], "camera_profile_sha256"),
        (declared_board, value["board_manifest_sha256"], "board_manifest_sha256"),
    ):
        if type(declared) is not str or SHA256_RE.fullmatch(declared) is None:
            fail("E_CAMERA_TOF_SCHEMA", "must be a 64-character SHA256", field)
        actual = sha256_file(path)
        if actual.lower() != declared.lower():
            fail("E_CAMERA_TOF_HASH", "source hash mismatch", field, {"expected": declared, "actual": actual})
    if camera_profile_path is not None and declared_camera != camera_profile_path.resolve():
        fail("E_CAMERA_TOF_PATH", "CLI camera profile differs from capture manifest", "camera_profile")
    if board_manifest_path is not None and declared_board != board_manifest_path.resolve():
        fail("E_CAMERA_TOF_PATH", "CLI board manifest differs from capture manifest", "board_manifest")
    poses = value["poses"]
    if type(poses) is not list or len(poses) < 2:
        fail("E_CAMERA_TOF_SCHEMA", "poses must contain at least two entries", "poses")
    names: set[str] = set()
    splits: set[str] = set()
    resolved_poses: list[dict[str, Any]] = []
    for index, pose in enumerate(poses):
        field = f"poses[{index}]"
        if type(pose) is not dict:
            fail("E_CAMERA_TOF_SCHEMA", "pose must be an object", field)
        _exact_keys(pose, POSE_KEYS, field)
        name = pose["pose_name"]
        if type(name) is not str or ID_RE.fullmatch(name) is None or name in names:
            fail("E_CAMERA_TOF_SCHEMA", "pose_name must be a unique safe identifier", f"{field}.pose_name")
        names.add(name)
        if pose["split"] not in {"train", "heldout"}:
            fail("E_CAMERA_TOF_SCHEMA", "split must be train or heldout", f"{field}.split")
        splits.add(pose["split"])
        if pose["stationary_confirmed"] is not True:
            fail("E_CAMERA_TOF_SCHEMA", "stationary_confirmed must be true", f"{field}.stationary_confirmed")
        _positive_number(pose["estimated_distance_mm"], f"{field}.estimated_distance_mm")
        for key in ("tilt_hint", "image_region_hint", "notes"):
            if type(pose[key]) is not str:
                fail("E_CAMERA_TOF_SCHEMA", "must be a string", f"{field}.{key}")
        scan_values = pose["scan_dir"] if type(pose["scan_dir"]) is list else [pose["scan_dir"]]
        if not scan_values:
            fail("E_CAMERA_TOF_SCHEMA", "scan_dir must not be empty", f"{field}.scan_dir")
        scan_dirs = [_path_from_manifest(item, root, f"{field}.scan_dir") for item in scan_values]
        if any(not item.is_dir() for item in scan_dirs):
            fail("E_CAMERA_TOF_PATH", "scan_dir must resolve to directories", f"{field}.scan_dir")
        resolved_poses.append({**pose, "_scan_dirs": scan_dirs})
    if splits != {"train", "heldout"}:
        fail("E_CAMERA_TOF_SPLIT", "capture must contain independent train and heldout poses", "poses")
    return {
        **value, "_camera_profile_path": declared_camera,
        "_board_manifest_path": declared_board, "_resolved_poses": resolved_poses,
    }


def prepare_output_dir(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.exists():
        if not resolved.is_dir() or any(resolved.iterdir()):
            fail("E_CAMERA_TOF_OUTPUT", "output directory must not exist or must be empty", str(resolved))
    else:
        resolved.mkdir(parents=True)
    return resolved


def validate_transform(rotation: Any, translation: Any, matrix: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r = np.asarray(rotation, dtype=np.float64)
    t = np.asarray(translation, dtype=np.float64)
    transform = np.asarray(matrix, dtype=np.float64)
    if r.shape != (3, 3) or t.shape != (3,) or transform.shape != (4, 4):
        fail("E_CAMERA_TOF_TRANSFORM", "R, t and T shapes must be 3x3, 3 and 4x4", "T_camera_from_tof")
    if not np.isfinite(r).all() or not np.isfinite(t).all() or not np.isfinite(transform).all():
        fail("E_CAMERA_TOF_TRANSFORM", "transform must be finite", "T_camera_from_tof")
    if not np.allclose(r.T @ r, np.eye(3), atol=1e-6) or abs(float(np.linalg.det(r)) - 1.0) > 1e-6:
        fail("E_CAMERA_TOF_TRANSFORM", "rotation must be right-handed and orthonormal", "rotation_matrix")
    expected = np.eye(4)
    expected[:3, :3] = r
    expected[:3, 3] = t
    if not np.allclose(transform, expected, atol=1e-9):
        fail("E_CAMERA_TOF_TRANSFORM", "T_camera_from_tof does not match rotation/translation", "T_camera_from_tof")
    if not np.allclose(transform @ np.linalg.inv(transform), np.eye(4), atol=1e-9):
        fail("E_CAMERA_TOF_TRANSFORM", "transform inverse roundtrip failed", "T_camera_from_tof")
    return r, t, transform


def validate_profile(value: dict[str, Any], *, require_pass: bool = False) -> dict[str, Any]:
    _exact_keys(value, PROFILE_KEYS, "$")
    if value["schema_version"] != PROFILE_SCHEMA:
        fail("E_CAMERA_TOF_SCHEMA", f"schema_version must be {PROFILE_SCHEMA}", "schema_version")
    _require_rfc3339(value["created_at"], "created_at")
    if type(value["profile_id"]) is not str or ID_RE.fullmatch(value["profile_id"]) is None:
        fail("E_CAMERA_TOF_SCHEMA", "profile_id must be a safe identifier", "profile_id")
    if type(value["camera_profile_id"]) is not str or not value["camera_profile_id"]:
        fail("E_CAMERA_TOF_SCHEMA", "camera_profile_id must be non-empty", "camera_profile_id")
    if value["transform_direction"] != "T_camera_from_tof":
        fail("E_CAMERA_TOF_TRANSFORM", "only T_camera_from_tof is supported", "transform_direction")
    if value["matrix_convention"] != "row-major, column-vector":
        fail("E_CAMERA_TOF_TRANSFORM", "matrix convention mismatch", "matrix_convention")
    if value["translation_unit"] != "mm":
        fail("E_CAMERA_TOF_TRANSFORM", "translation unit must be mm", "translation_unit")
    validate_transform(value["rotation_matrix"], value["translation"], value["T_camera_from_tof"])
    if value["camera_resolution"] != [640, 360] or value["tof_resolution"] != [8, 8]:
        fail("E_CAMERA_TOF_SCHEMA", "Camera/ToF resolution mismatch", "resolution")
    if value["tof_model"] != "VL53L5CX" or value["tof_frequency_hz"] != 15:
        fail("E_CAMERA_TOF_SCHEMA", "ToF model/frequency must match the frozen 15 Hz baseline", "tof_model")
    if type(value["orientation"]) is not dict:
        fail("E_CAMERA_TOF_SCHEMA", "orientation must be an object", "orientation")
    _exact_keys(value["orientation"], ORIENTATION_KEYS, "orientation")
    from .zone_orientation import D4_STRATEGIES, raw_index_for_canonical_index
    if value["orientation"]["name"] not in D4_STRATEGIES:
        fail("E_CAMERA_TOF_SCHEMA", "orientation must be one of the eight D4 candidates", "orientation.name")
    if value["orientation"]["raw_index_for_canonical_index"] != raw_index_for_canonical_index(value["orientation"]["name"]).tolist():
        fail("E_CAMERA_TOF_SCHEMA", "orientation mapping does not match its name", "orientation.raw_index_for_canonical_index")
    if type(value["ray_model"]) is not dict:
        fail("E_CAMERA_TOF_SCHEMA", "ray_model must be an object", "ray_model")
    _exact_keys(value["ray_model"], RAY_MODEL_KEYS, "ray_model")
    rays = np.asarray(value["zone_rays"], dtype=np.float64)
    if rays.shape != (64, 3) or not np.isfinite(rays).all():
        fail("E_CAMERA_TOF_SCHEMA", "zone_rays must be finite 64x3", "zone_rays")
    if not np.allclose(np.linalg.norm(rays, axis=1), 1.0, atol=1e-6) or np.any(rays[:, 2] <= 0):
        fail("E_CAMERA_TOF_SCHEMA", "zone rays must be unit length with positive z", "zone_rays")
    for key in ("zone_valid_mask", "camera_tof_overlap_mask"):
        if type(value[key]) is not list or len(value[key]) != 64 or any(type(item) is not bool for item in value[key]):
            fail("E_CAMERA_TOF_SCHEMA", "mask must contain 64 booleans", key)
    if value["distance_semantics"] not in {"axial_z", "radial_range"}:
        fail("E_CAMERA_TOF_SCHEMA", "invalid distance semantics", "distance_semantics")
    if value["ray_model"]["distance_semantics"] != value["distance_semantics"]:
        fail("E_CAMERA_TOF_SCHEMA", "ray model distance semantics mismatch", "ray_model.distance_semantics")
    _positive_number(value["ray_model"]["hfov_deg"], "ray_model.hfov_deg")
    _positive_number(value["ray_model"]["vfov_deg"], "ray_model.vfov_deg")
    if type(value["residual_summary"]) is not dict or set(value["residual_summary"]) != {"train", "heldout"}:
        fail("E_CAMERA_TOF_SCHEMA", "residual_summary must contain train and heldout only", "residual_summary")
    for split in ("train", "heldout"):
        if type(value["residual_summary"][split]) is not dict:
            fail("E_CAMERA_TOF_SCHEMA", "residual split must be an object", f"residual_summary.{split}")
        _exact_keys(value["residual_summary"][split], SUMMARY_KEYS, f"residual_summary.{split}")
    for key in ("training_pose_count", "heldout_pose_count", "training_point_count", "heldout_point_count"):
        if type(value[key]) is not int or value[key] <= 0:
            fail("E_CAMERA_TOF_SCHEMA", "profile counts must be positive integers", key)
    if value["training_point_count"] != value["residual_summary"]["train"]["count"] or value["heldout_point_count"] != value["residual_summary"]["heldout"]["count"]:
        fail("E_CAMERA_TOF_SCHEMA", "profile point counts must match residual summaries", "training_point_count")
    for key, keys in (
        ("candidate_margin", MARGIN_KEYS),
        ("gauge_equivalence", GAUGE_KEYS),
        ("mechanical_prior", MECHANICAL_KEYS),
    ):
        if type(value[key]) is not dict:
            fail("E_CAMERA_TOF_SCHEMA", f"{key} must be an object", key)
        _exact_keys(value[key], keys, key)
    if value["mechanical_prior_is_measured"] is not False:
        fail("E_CAMERA_TOF_SCHEMA", "mechanical prior is not measured", "mechanical_prior_is_measured")
    if (
        value["mechanical_prior"]["tof_is_camera_right"] is not True
        or value["mechanical_prior"]["expected_tx_mm"] != -20.0
    ):
        fail(
            "E_CAMERA_TOF_SCHEMA",
            "ToF on observer-right must map to expected Camera-X translation -20 mm",
            "mechanical_prior",
        )
    if value["absolute_geometry_valid"] is not False or value["world_pose_resolved"] is not False:
        fail("E_CAMERA_TOF_SCHEMA", "Stage 7B cannot resolve absolute/world geometry", "absolute_geometry_valid")
    if value["camera_tof_extrinsics"] != {"status": "measured"}:
        fail("E_CAMERA_TOF_SCHEMA", "formal profile must mark Camera-ToF extrinsics measured", "camera_tof_extrinsics")
    if value["validation_status"] != "PASS":
        fail("E_CAMERA_TOF_SCHEMA", "profile is not a PASS profile", "validation_status")
    if type(value["source_data_hashes"]) is not dict or any(
        type(key) is not str or type(item) is not str or SHA256_RE.fullmatch(item) is None
        for key, item in value["source_data_hashes"].items()
    ):
        fail("E_CAMERA_TOF_SCHEMA", "source_data_hashes must map strings to SHA256", "source_data_hashes")
    for key in ("warnings", "unresolved_fields"):
        if type(value[key]) is not list or any(type(item) is not str for item in value[key]):
            fail("E_CAMERA_TOF_SCHEMA", f"{key} must contain strings", key)
    return value


def validate_report(value: dict[str, Any]) -> dict[str, Any]:
    _exact_keys(value, REPORT_KEYS, "$")
    if value["schema_version"] != REPORT_SCHEMA:
        fail("E_CAMERA_TOF_SCHEMA", f"schema_version must be {REPORT_SCHEMA}", "schema_version")
    _require_rfc3339(value["created_at"], "created_at")
    if value["status"] not in {"PASS", "FAIL", "UNRESOLVED"} or value["validation_status"] != value["status"]:
        fail("E_CAMERA_TOF_SCHEMA", "report status/validation_status mismatch", "status")
    for key in ("training_pose_count", "heldout_pose_count"):
        if type(value[key]) is not int or value[key] < 0:
            fail("E_CAMERA_TOF_SCHEMA", "pose count must be a non-negative integer", key)
    if type(value["gates"]) is not list:
        fail("E_CAMERA_TOF_SCHEMA", "gates must be a list", "gates")
    for index, gate in enumerate(value["gates"]):
        if type(gate) is not dict:
            fail("E_CAMERA_TOF_SCHEMA", "gate must be an object", f"gates[{index}]")
        _exact_keys(gate, {"name", "status", "details"}, f"gates[{index}]")
        if gate["status"] not in {"PASS", "FAIL", "UNRESOLVED"}:
            fail("E_CAMERA_TOF_SCHEMA", "invalid gate status", f"gates[{index}].status")
    if type(value["warnings"]) is not list or any(type(item) is not str for item in value["warnings"]):
        fail("E_CAMERA_TOF_SCHEMA", "warnings must be strings", "warnings")
    if type(value["formal_profile_generated"]) is not bool:
        fail("E_CAMERA_TOF_SCHEMA", "formal_profile_generated must be boolean", "formal_profile_generated")
    if value["camera_tof_extrinsics_status"] not in {"measured", "unresolved"}:
        fail("E_CAMERA_TOF_SCHEMA", "invalid extrinsics status", "camera_tof_extrinsics_status")
    if value["absolute_geometry_valid"] is not False or value["world_pose_resolved"] is not False:
        fail("E_CAMERA_TOF_SCHEMA", "Stage 7B report cannot resolve absolute/world geometry", "absolute_geometry_valid")
    return value
