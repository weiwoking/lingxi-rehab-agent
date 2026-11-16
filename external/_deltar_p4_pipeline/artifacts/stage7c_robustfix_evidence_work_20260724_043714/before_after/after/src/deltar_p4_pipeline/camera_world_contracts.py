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

from .camera_calibration_contracts import validate_board_manifest, validate_calibration_v2
from .camera_tof_contracts import validate_profile as validate_camera_tof_profile


CAPTURE_SCHEMA = "camera_world_capture.v1"
PROFILE_SCHEMA = "camera_world_pose.v1"
REPORT_SCHEMA = "camera_world_calibration_report.v1"
TOOL_VERSION = "stage7c1-1.0"

FROZEN_CAMERA_TOF_PROFILE = Path(
    r"C:\calibration\stage7b_result_unitfix_20260724_012041\camera_tof_extrinsics.v1.json"
)
FROZEN_CAMERA_TOF_SHA256 = (
    "dbcb5792338f50cca953c365e436e17d9f044b2f47bcca716e697308dda7f8b5"
)
FROZEN_CAMERA_TOF_PROFILE_ID = "camera_tof_extrinsics_6698a99489d42fb2"
CAMERA_HEIGHT_MM = 969.0
CAMERA_HEIGHT_UNCERTAINTY_MM = 5.0
MIN_CAPTURED_FRAME_COUNT = 15
MIN_ORIENTATION_INLIER_COUNT = 14
MIN_ORIENTATION_INLIERS_PER_POSE = 4

WORLD_FRAME = {
    "x_axis": "vehicle_right",
    "y_axis": "vehicle_forward",
    "z_axis": "up",
    "origin": "ground_point_below_camera_optical_centre",
    "translation_unit": "mm",
}
MATRIX_CONVENTION = "row-major JSON, column-vector convention"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")

CAPTURE_KEYS = {
    "schema_version", "capture_id", "created_at",
    "camera_profile_path", "camera_profile_id", "camera_profile_sha256",
    "camera_tof_profile_path", "camera_tof_profile_id",
    "camera_tof_profile_sha256", "board_manifest_path",
    "board_manifest_sha256", "world_frame", "camera_height_mm",
    "camera_height_uncertainty_mm", "board_alignment",
    "board_origin_height_above_floor_mm", "poses",
}
ALIGNMENT_KEYS = {
    "vertical", "top_edge_horizontal", "front_faces_vehicle",
    "vehicle_forward_perpendicular_to_board", "setup_angle_uncertainty_deg",
}
POSE_COMMON_KEYS = {
    "pose_name", "role", "stationary_confirmed",
    "approximate_distance_mm", "alignment_confirmed", "notes",
}
PROFILE_KEYS = {
    "schema_version", "profile_id", "created_at", "validation_status",
    "camera_profile_id", "camera_tof_profile_id", "world_frame",
    "matrix_convention", "T_world_from_camera",
    "rotation_world_from_camera", "translation_world_from_camera_mm",
    "T_world_from_tof", "rotation_world_from_tof",
    "translation_world_from_tof_mm", "camera_height_mm",
    "camera_height_uncertainty_mm", "orientation_diagnostics",
    "capture_summary", "residual_summary", "source_data_hashes",
    "warnings", "unresolved_fields", "world_pose",
    "world_pose_resolved", "absolute_geometry_valid",
}
ORIENTATION_DIAGNOSTIC_KEYS = {"roll_deg", "pitch_deg", "yaw_deg", "convention"}
CAPTURE_SUMMARY_KEYS = {
    "pose_count", "frame_count", "valid_frame_count", "rejected_frame_count",
}
RESIDUAL_SUMMARY_KEYS = {"camera_orientation", "tof_vertical_plane"}
WORLD_POSE_KEYS = {"status"}


@dataclass(frozen=True)
class CameraWorldError(Exception):
    code: str
    message: str
    field: str | None = None
    details: Any = None

    def __str__(self) -> str:
        suffix = f" [{self.field}]" if self.field else ""
        return f"{self.code}: {self.message}{suffix}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "field": self.field,
            "details": self.details,
        }


def fail(code: str, message: str, field: str | None = None, details: Any = None) -> None:
    raise CameraWorldError(code, message, field, details)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def _assert_finite(value: Any, field: str = "$") -> None:
    if type(value) is float and not math.isfinite(value):
        fail("E_CAMERA_WORLD_JSON", "JSON contains a non-finite number", field)
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_finite(item, f"{field}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_finite(item, f"{field}[{index}]")


def load_strict_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), parse_constant=_reject_constant
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        fail(
            "E_CAMERA_WORLD_JSON",
            f"cannot read strict UTF-8 JSON: {exc}",
            str(path),
        )
    if type(value) is not dict:
        fail("E_CAMERA_WORLD_JSON", "JSON root must be an object", str(path))
    _assert_finite(value)
    return value


def write_json(path: Path, value: Any) -> None:
    _assert_finite(value)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        fail("E_CAMERA_WORLD_PATH", f"cannot hash file: {exc}", str(path))
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _exact_keys(value: dict[str, Any], expected: set[str], field: str) -> None:
    actual = set(value)
    if actual != expected:
        fail(
            "E_CAMERA_WORLD_SCHEMA",
            "object fields do not match the closed-world schema",
            field,
            {
                "missing": sorted(expected - actual),
                "unknown": sorted(actual - expected),
            },
        )


def _require_timestamp(value: Any, field: str) -> None:
    if type(value) is not str:
        fail("E_CAMERA_WORLD_SCHEMA", "must be an RFC3339 timestamp", field)
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError as exc:
        fail("E_CAMERA_WORLD_SCHEMA", "must be an RFC3339 timestamp", field, str(exc))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        fail(
            "E_CAMERA_WORLD_SCHEMA",
            "timestamp must include Z or an explicit UTC offset",
            field,
        )


def _require_number(value: Any, field: str, *, positive: bool = False) -> float:
    if type(value) not in {int, float} or not math.isfinite(float(value)):
        fail("E_CAMERA_WORLD_SCHEMA", "must be a finite number", field)
    number = float(value)
    if positive and number <= 0:
        fail("E_CAMERA_WORLD_SCHEMA", "must be positive", field)
    return number


def _require_sha256(value: Any, field: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None:
        fail("E_CAMERA_WORLD_SCHEMA", "must be a 64-character SHA256", field)
    return value.lower()


def _resolve_path(value: Any, root: Path, field: str) -> Path:
    if type(value) is not str or not value:
        fail("E_CAMERA_WORLD_SCHEMA", "must be a non-empty path", field)
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        fail("E_CAMERA_WORLD_PATH", f"path does not exist: {exc}", field, str(path))


def _verify_declared_source(
    path: Path, declared_hash: Any, field: str
) -> str:
    expected = _require_sha256(declared_hash, field)
    actual = sha256_file(path)
    if actual != expected:
        fail(
            "E_CAMERA_WORLD_HASH",
            "source hash mismatch",
            field,
            {"expected": expected, "actual": actual, "path": str(path)},
        )
    return actual


def load_frozen_camera_tof_profile(path: Path) -> dict[str, Any]:
    try:
        resolved = path.resolve(strict=True)
        frozen = FROZEN_CAMERA_TOF_PROFILE.resolve(strict=True)
    except OSError as exc:
        fail("E_CAMERA_WORLD_PATH", f"Stage 7B profile is missing: {exc}", str(path))
    if resolved != frozen:
        fail(
            "E_CAMERA_WORLD_STAGE7B_BINDING",
            "only the frozen absolute Stage 7B profile path is accepted",
            "camera_tof_profile_path",
            {"required": str(frozen), "actual": str(resolved)},
        )
    actual_hash = sha256_file(resolved)
    if actual_hash != FROZEN_CAMERA_TOF_SHA256:
        fail(
            "E_CAMERA_WORLD_STAGE7B_BINDING",
            "frozen Stage 7B SHA256 mismatch",
            "camera_tof_profile_sha256",
            {"required": FROZEN_CAMERA_TOF_SHA256, "actual": actual_hash},
        )
    profile = load_strict_json(resolved)
    try:
        validate_camera_tof_profile(profile, require_pass=True)
    except Exception as exc:
        fail(
            "E_CAMERA_WORLD_STAGE7B_BINDING",
            f"frozen Stage 7B profile validator failed: {exc}",
            str(resolved),
        )
    required = {
        "profile_id": FROZEN_CAMERA_TOF_PROFILE_ID,
        "schema_version": "camera_tof_extrinsics.v1",
        "validation_status": "PASS",
        "transform_direction": "T_camera_from_tof",
        "translation_unit": "mm",
        "world_pose_resolved": False,
        "absolute_geometry_valid": False,
    }
    actual = {key: profile.get(key) for key in required}
    actual["camera_tof_extrinsics.status"] = (
        profile.get("camera_tof_extrinsics") or {}
    ).get("status")
    required["camera_tof_extrinsics.status"] = "measured"
    if any(actual.get(key) != expected for key, expected in required.items()):
        fail(
            "E_CAMERA_WORLD_STAGE7B_BINDING",
            "frozen Stage 7B contract fields mismatch",
            str(resolved),
            {"required": required, "actual": actual},
        )
    return profile


def validate_capture_manifest(
    value: dict[str, Any],
    *,
    manifest_path: Path,
    camera_profile_path: Path | None = None,
    camera_tof_profile_path: Path | None = None,
    board_manifest_path: Path | None = None,
) -> dict[str, Any]:
    _exact_keys(value, CAPTURE_KEYS, "$")
    if value["schema_version"] != CAPTURE_SCHEMA:
        fail(
            "E_CAMERA_WORLD_SCHEMA",
            f"schema_version must be {CAPTURE_SCHEMA}",
            "schema_version",
        )
    if (
        type(value["capture_id"]) is not str
        or ID_RE.fullmatch(value["capture_id"]) is None
    ):
        fail(
            "E_CAMERA_WORLD_SCHEMA",
            "capture_id must be a safe identifier",
            "capture_id",
        )
    _require_timestamp(value["created_at"], "created_at")
    if value["world_frame"] != WORLD_FRAME:
        fail(
            "E_CAMERA_WORLD_FRAME",
            "world_frame differs from the frozen Stage 7C contract",
            "world_frame",
            {"required": WORLD_FRAME, "actual": value["world_frame"]},
        )
    if _require_number(value["camera_height_mm"], "camera_height_mm") != CAMERA_HEIGHT_MM:
        fail(
            "E_CAMERA_WORLD_HEIGHT",
            "camera height must remain the measured 969 mm",
            "camera_height_mm",
        )
    if (
        _require_number(
            value["camera_height_uncertainty_mm"],
            "camera_height_uncertainty_mm",
        )
        != CAMERA_HEIGHT_UNCERTAINTY_MM
    ):
        fail(
            "E_CAMERA_WORLD_HEIGHT",
            "camera height uncertainty must remain 5 mm",
            "camera_height_uncertainty_mm",
        )
    alignment = value["board_alignment"]
    if type(alignment) is not dict:
        fail("E_CAMERA_WORLD_SCHEMA", "board_alignment must be an object", "board_alignment")
    _exact_keys(alignment, ALIGNMENT_KEYS, "board_alignment")
    for key in (
        "vertical", "top_edge_horizontal", "front_faces_vehicle",
        "vehicle_forward_perpendicular_to_board",
    ):
        if alignment[key] is not True:
            fail(
                "E_CAMERA_WORLD_ALIGNMENT",
                "formal capture requires explicit alignment confirmation",
                f"board_alignment.{key}",
            )
    setup_uncertainty = _require_number(
        alignment["setup_angle_uncertainty_deg"],
        "board_alignment.setup_angle_uncertainty_deg",
        positive=True,
    )
    if setup_uncertainty > 0.5:
        fail(
            "E_CAMERA_WORLD_ALIGNMENT",
            "setup angle uncertainty must be <= 0.5 deg",
            "board_alignment.setup_angle_uncertainty_deg",
        )
    board_height = value["board_origin_height_above_floor_mm"]
    if board_height is not None:
        _require_number(
            board_height,
            "board_origin_height_above_floor_mm",
            positive=True,
        )

    root = manifest_path.resolve().parent
    camera_path = _resolve_path(value["camera_profile_path"], root, "camera_profile_path")
    camera_tof_path = _resolve_path(
        value["camera_tof_profile_path"], root, "camera_tof_profile_path"
    )
    board_path = _resolve_path(value["board_manifest_path"], root, "board_manifest_path")
    camera_hash = _verify_declared_source(
        camera_path, value["camera_profile_sha256"], "camera_profile_sha256"
    )
    camera_tof_hash = _verify_declared_source(
        camera_tof_path,
        value["camera_tof_profile_sha256"],
        "camera_tof_profile_sha256",
    )
    board_hash = _verify_declared_source(
        board_path, value["board_manifest_sha256"], "board_manifest_sha256"
    )
    if camera_profile_path is not None and camera_path != camera_profile_path.resolve():
        fail(
            "E_CAMERA_WORLD_PATH",
            "CLI camera profile differs from capture manifest",
            "camera_profile_path",
        )
    if (
        camera_tof_profile_path is not None
        and camera_tof_path != camera_tof_profile_path.resolve()
    ):
        fail(
            "E_CAMERA_WORLD_PATH",
            "CLI Camera-ToF profile differs from capture manifest",
            "camera_tof_profile_path",
        )
    if board_manifest_path is not None and board_path != board_manifest_path.resolve():
        fail(
            "E_CAMERA_WORLD_PATH",
            "CLI board manifest differs from capture manifest",
            "board_manifest_path",
        )

    camera = load_strict_json(camera_path)
    try:
        validate_calibration_v2(camera)
    except Exception as exc:
        fail(
            "E_CAMERA_WORLD_CAMERA_PROFILE",
            f"Stage 7A profile validator failed: {exc}",
            str(camera_path),
        )
    if camera.get("profile_id") != value["camera_profile_id"]:
        fail(
            "E_CAMERA_WORLD_PROFILE_ID",
            "Stage 7A profile_id differs from capture manifest",
            "camera_profile_id",
        )
    camera_tof = load_frozen_camera_tof_profile(camera_tof_path)
    if camera_tof_hash != FROZEN_CAMERA_TOF_SHA256:
        fail(
            "E_CAMERA_WORLD_STAGE7B_BINDING",
            "capture manifest does not bind the frozen Stage 7B SHA256",
            "camera_tof_profile_sha256",
        )
    if camera_tof.get("profile_id") != value["camera_tof_profile_id"]:
        fail(
            "E_CAMERA_WORLD_PROFILE_ID",
            "Stage 7B profile_id differs from capture manifest",
            "camera_tof_profile_id",
        )
    board = load_strict_json(board_path)
    try:
        validate_board_manifest(board, require_print_measurement=True)
    except Exception as exc:
        fail(
            "E_CAMERA_WORLD_BOARD",
            f"board manifest validator failed: {exc}",
            str(board_path),
        )

    poses = value["poses"]
    if type(poses) is not list or len(poses) < 3:
        fail("E_CAMERA_WORLD_CAPTURE_GATE", "at least 3 poses are required", "poses")
    names: set[str] = set()
    roles: set[str] = set()
    resolved_poses: list[dict[str, Any]] = []
    for index, pose in enumerate(poses):
        field = f"poses[{index}]"
        if type(pose) is not dict:
            fail("E_CAMERA_WORLD_SCHEMA", "pose must be an object", field)
        actual_keys = set(pose)
        path_keys = actual_keys & {"scan_dir", "scan_dirs"}
        expected = POSE_COMMON_KEYS | path_keys
        if len(path_keys) != 1 or actual_keys != expected:
            fail(
                "E_CAMERA_WORLD_SCHEMA",
                "pose requires exactly one of scan_dir or scan_dirs and no unknown fields",
                field,
                {
                    "required_common": sorted(POSE_COMMON_KEYS),
                    "path_choice": ["scan_dir", "scan_dirs"],
                    "actual": sorted(actual_keys),
                },
            )
        name = pose["pose_name"]
        if type(name) is not str or ID_RE.fullmatch(name) is None or name in names:
            fail(
                "E_CAMERA_WORLD_SCHEMA",
                "pose_name must be a unique safe identifier",
                f"{field}.pose_name",
            )
        names.add(name)
        role = pose["role"]
        if role not in {"world_front_near", "world_front_mid", "world_front_far"}:
            fail(
                "E_CAMERA_WORLD_SCHEMA",
                "role must be world_front_near/mid/far",
                f"{field}.role",
            )
        if role in roles:
            fail(
                "E_CAMERA_WORLD_CAPTURE_GATE",
                "near/mid/far roles must be unique",
                f"{field}.role",
            )
        roles.add(role)
        if pose["stationary_confirmed"] is not True:
            fail(
                "E_CAMERA_WORLD_CAPTURE_GATE",
                "stationary_confirmed must be true",
                f"{field}.stationary_confirmed",
            )
        if pose["alignment_confirmed"] is not True:
            fail(
                "E_CAMERA_WORLD_ALIGNMENT",
                "alignment_confirmed must be true",
                f"{field}.alignment_confirmed",
            )
        _require_number(
            pose["approximate_distance_mm"],
            f"{field}.approximate_distance_mm",
            positive=True,
        )
        if type(pose["notes"]) is not str:
            fail("E_CAMERA_WORLD_SCHEMA", "notes must be a string", f"{field}.notes")
        scan_value = pose[next(iter(path_keys))]
        scan_values = scan_value if type(scan_value) is list else [scan_value]
        if not scan_values:
            fail("E_CAMERA_WORLD_SCHEMA", "scan path list must not be empty", field)
        scan_dirs = [
            _resolve_path(item, root, f"{field}.{next(iter(path_keys))}")
            for item in scan_values
        ]
        if any(not item.is_dir() for item in scan_dirs):
            fail(
                "E_CAMERA_WORLD_PATH",
                "every scan path must resolve to a directory",
                field,
            )
        resolved_poses.append({**pose, "_scan_dirs": scan_dirs})
    required_roles = {"world_front_near", "world_front_mid", "world_front_far"}
    if not required_roles.issubset(roles):
        fail(
            "E_CAMERA_WORLD_CAPTURE_GATE",
            "capture must include near, mid and far",
            "poses",
        )
    return {
        **value,
        "_camera_profile_path": camera_path,
        "_camera_tof_profile_path": camera_tof_path,
        "_board_manifest_path": board_path,
        "_camera_profile": camera,
        "_camera_tof_profile": camera_tof,
        "_board_manifest": board,
        "_resolved_poses": resolved_poses,
        "_source_hashes": {
            str(manifest_path.resolve()): sha256_file(manifest_path),
            str(camera_path): camera_hash,
            str(camera_tof_path): camera_tof_hash,
            str(board_path): board_hash,
        },
    }


def prepare_output_dir(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.exists():
        if not resolved.is_dir() or any(resolved.iterdir()):
            fail(
                "E_CAMERA_WORLD_OUTPUT",
                "output directory must not exist or must be empty",
                str(resolved),
            )
    else:
        resolved.mkdir(parents=True)
    return resolved


def validate_transform(
    rotation: Any, translation: Any, transform: Any, field: str
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r = np.asarray(rotation, dtype=np.float64)
    t = np.asarray(translation, dtype=np.float64)
    matrix = np.asarray(transform, dtype=np.float64)
    if r.shape != (3, 3) or t.shape != (3,) or matrix.shape != (4, 4):
        fail(
            "E_CAMERA_WORLD_TRANSFORM",
            "R, t and T shapes must be 3x3, 3 and 4x4",
            field,
        )
    if not np.isfinite(r).all() or not np.isfinite(t).all() or not np.isfinite(matrix).all():
        fail("E_CAMERA_WORLD_TRANSFORM", "transform must be finite", field)
    if (
        not np.allclose(r.T @ r, np.eye(3), atol=1e-6)
        or abs(float(np.linalg.det(r)) - 1.0) > 1e-6
    ):
        fail(
            "E_CAMERA_WORLD_TRANSFORM",
            "rotation must be right-handed and orthonormal",
            field,
        )
    expected = np.eye(4)
    expected[:3, :3] = r
    expected[:3, 3] = t
    if not np.allclose(matrix, expected, atol=1e-9):
        fail("E_CAMERA_WORLD_TRANSFORM", "T does not match R/t", field)
    if not np.allclose(matrix @ np.linalg.inv(matrix), np.eye(4), atol=1e-9):
        fail("E_CAMERA_WORLD_TRANSFORM", "inverse roundtrip failed", field)
    return r, t, matrix


def validate_profile(value: dict[str, Any], *, require_pass: bool = True) -> dict[str, Any]:
    _exact_keys(value, PROFILE_KEYS, "$")
    if value["schema_version"] != PROFILE_SCHEMA:
        fail(
            "E_CAMERA_WORLD_SCHEMA",
            f"schema_version must be {PROFILE_SCHEMA}",
            "schema_version",
        )
    if (
        type(value["profile_id"]) is not str
        or ID_RE.fullmatch(value["profile_id"]) is None
    ):
        fail(
            "E_CAMERA_WORLD_SCHEMA",
            "profile_id must be a safe identifier",
            "profile_id",
        )
    _require_timestamp(value["created_at"], "created_at")
    if require_pass and value["validation_status"] != "PASS":
        fail(
            "E_CAMERA_WORLD_PROFILE",
            "formal profile must have validation_status PASS",
            "validation_status",
        )
    if value["camera_tof_profile_id"] != FROZEN_CAMERA_TOF_PROFILE_ID:
        fail(
            "E_CAMERA_WORLD_STAGE7B_BINDING",
            "formal profile is not bound to the frozen Stage 7B profile_id",
            "camera_tof_profile_id",
        )
    if value["world_frame"] != WORLD_FRAME:
        fail("E_CAMERA_WORLD_FRAME", "world axes/origin text mismatch", "world_frame")
    if value["matrix_convention"] != MATRIX_CONVENTION:
        fail(
            "E_CAMERA_WORLD_TRANSFORM",
            "matrix convention mismatch",
            "matrix_convention",
        )
    r_wc, t_wc, matrix_wc = validate_transform(
        value["rotation_world_from_camera"],
        value["translation_world_from_camera_mm"],
        value["T_world_from_camera"],
        "T_world_from_camera",
    )
    r_wt, t_wt, matrix_wt = validate_transform(
        value["rotation_world_from_tof"],
        value["translation_world_from_tof_mm"],
        value["T_world_from_tof"],
        "T_world_from_tof",
    )
    if not np.array_equal(t_wc, np.asarray([0.0, 0.0, CAMERA_HEIGHT_MM])):
        fail(
            "E_CAMERA_WORLD_HEIGHT",
            "Camera world translation must be exactly [0,0,969]",
            "translation_world_from_camera_mm",
        )
    if value["camera_height_mm"] != CAMERA_HEIGHT_MM:
        fail("E_CAMERA_WORLD_HEIGHT", "camera_height_mm must equal 969", "camera_height_mm")
    if value["camera_height_uncertainty_mm"] != CAMERA_HEIGHT_UNCERTAINTY_MM:
        fail(
            "E_CAMERA_WORLD_HEIGHT",
            "camera_height_uncertainty_mm must equal 5",
            "camera_height_uncertainty_mm",
        )
    frozen = load_frozen_camera_tof_profile(FROZEN_CAMERA_TOF_PROFILE)
    matrix_ct = np.asarray(frozen["T_camera_from_tof"], dtype=np.float64)
    if not np.allclose(matrix_wt, matrix_wc @ matrix_ct, atol=1e-9):
        fail(
            "E_CAMERA_WORLD_COMPOSITION",
            "T_world_from_tof must equal T_world_from_camera @ T_camera_from_tof",
            "T_world_from_tof",
        )
    if not np.allclose(matrix_wt[:3, :3], r_wt, atol=1e-12) or not np.allclose(
        matrix_wt[:3, 3], t_wt, atol=1e-12
    ):
        fail("E_CAMERA_WORLD_COMPOSITION", "ToF R/t roundtrip mismatch", "T_world_from_tof")
    orientation = value["orientation_diagnostics"]
    if type(orientation) is not dict:
        fail(
            "E_CAMERA_WORLD_SCHEMA",
            "orientation_diagnostics must be an object",
            "orientation_diagnostics",
        )
    _exact_keys(
        orientation,
        ORIENTATION_DIAGNOSTIC_KEYS,
        "orientation_diagnostics",
    )
    for key in ("roll_deg", "pitch_deg", "yaw_deg"):
        angle = _require_number(
            orientation[key], f"orientation_diagnostics.{key}"
        )
        if require_pass and abs(angle) > 5.0:
            fail(
                "E_CAMERA_WORLD_MECHANICAL_GATE",
                "formal profile angle exceeds 5 deg",
                f"orientation_diagnostics.{key}",
            )
    if type(orientation["convention"]) is not str or not orientation["convention"]:
        fail(
            "E_CAMERA_WORLD_SCHEMA",
            "orientation convention text must be non-empty",
            "orientation_diagnostics.convention",
        )
    summary = value["capture_summary"]
    if type(summary) is not dict:
        fail("E_CAMERA_WORLD_SCHEMA", "capture_summary must be an object", "capture_summary")
    _exact_keys(summary, CAPTURE_SUMMARY_KEYS, "capture_summary")
    for key in CAPTURE_SUMMARY_KEYS:
        if type(summary[key]) is not int or summary[key] < 0:
            fail(
                "E_CAMERA_WORLD_SCHEMA",
                "capture summary values must be non-negative integers",
                f"capture_summary.{key}",
            )
    if require_pass and (
        summary["pose_count"] < 3
        or summary["frame_count"] < MIN_CAPTURED_FRAME_COUNT
        or summary["valid_frame_count"] < MIN_ORIENTATION_INLIER_COUNT
    ):
        fail(
            "E_CAMERA_WORLD_CAPTURE_GATE",
            "formal profile capture counts are below the gate",
            "capture_summary",
        )
    residuals = value["residual_summary"]
    if type(residuals) is not dict:
        fail("E_CAMERA_WORLD_SCHEMA", "residual_summary must be an object", "residual_summary")
    _exact_keys(residuals, RESIDUAL_SUMMARY_KEYS, "residual_summary")
    if type(value["source_data_hashes"]) is not dict or not value["source_data_hashes"]:
        fail(
            "E_CAMERA_WORLD_SCHEMA",
            "source_data_hashes must be a non-empty object",
            "source_data_hashes",
        )
    for path, digest in value["source_data_hashes"].items():
        if type(path) is not str or not path:
            fail(
                "E_CAMERA_WORLD_SCHEMA",
                "source hash keys must be non-empty paths",
                "source_data_hashes",
            )
        _require_sha256(digest, f"source_data_hashes.{path}")
    frozen_key = str(FROZEN_CAMERA_TOF_PROFILE.resolve())
    if value["source_data_hashes"].get(frozen_key) != FROZEN_CAMERA_TOF_SHA256:
        fail(
            "E_CAMERA_WORLD_STAGE7B_BINDING",
            "source hashes do not contain the exact frozen Stage 7B binding",
            "source_data_hashes",
        )
    if type(value["warnings"]) is not list or any(
        type(item) is not str for item in value["warnings"]
    ):
        fail("E_CAMERA_WORLD_SCHEMA", "warnings must be an array of strings", "warnings")
    if type(value["unresolved_fields"]) is not list or any(
        type(item) is not str for item in value["unresolved_fields"]
    ):
        fail(
            "E_CAMERA_WORLD_SCHEMA",
            "unresolved_fields must be an array of strings",
            "unresolved_fields",
        )
    if value["world_pose"] != {"status": "measured"}:
        fail("E_CAMERA_WORLD_PROFILE", "world_pose.status must be measured", "world_pose")
    if value["world_pose_resolved"] is not True:
        fail(
            "E_CAMERA_WORLD_PROFILE",
            "world_pose_resolved must be true",
            "world_pose_resolved",
        )
    if value["absolute_geometry_valid"] is not False:
        fail(
            "E_CAMERA_WORLD_PROFILE",
            "absolute_geometry_valid must remain false in Stage 7C-1",
            "absolute_geometry_valid",
        )
    return value


def validate_report(value: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version", "created_at", "capture_id", "status",
        "validation_status", "gates", "capture_summary",
        "orientation_summary", "tof_plane_summary", "height_sanity_check",
        "source_profile_binding", "warnings", "formal_profile_generated",
        "tool_version",
    }
    _exact_keys(value, expected, "$")
    if value["schema_version"] != REPORT_SCHEMA:
        fail(
            "E_CAMERA_WORLD_SCHEMA",
            f"schema_version must be {REPORT_SCHEMA}",
            "schema_version",
        )
    _require_timestamp(value["created_at"], "created_at")
    if value["status"] not in {"PASS", "FAIL"}:
        fail("E_CAMERA_WORLD_SCHEMA", "status must be PASS or FAIL", "status")
    if value["validation_status"] != value["status"]:
        fail(
            "E_CAMERA_WORLD_SCHEMA",
            "validation_status must equal status",
            "validation_status",
        )
    if value["formal_profile_generated"] is True and value["status"] != "PASS":
        fail(
            "E_CAMERA_WORLD_SCHEMA",
            "a formal profile may only be generated by a PASS calibration",
            "formal_profile_generated",
        )
    if value["tool_version"] != TOOL_VERSION:
        fail("E_CAMERA_WORLD_SCHEMA", "tool_version mismatch", "tool_version")
    _assert_finite(value)
    return value
