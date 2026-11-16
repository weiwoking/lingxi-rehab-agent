from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from .errors import ErrorCode, PipelineError

CALIBRATION_SCHEMA = "deltar_geometry_calibration.v1"
SEMANTICS = {"axial_z", "radial_range"}
SEMANTICS_STATUSES = {"PROVEN", "PLAUSIBLE_BUT_UNPROVEN", "UNRESOLVED"}
CALIBRATION_STATUSES = {"synthetic", "unverified", "measured"}
TOP_LEVEL_KEYS = {
    "schema_version", "profile_id", "profile_version", "calibration_status",
    "absolute_geometry_valid", "physical_height_valid", "bottom_height_valid",
    "image", "camera_model", "depth", "transforms", "mount_geometry_hint",
    "ground", "warning",
}


def _fail(code: ErrorCode, message: str, field: str, details: Any = None) -> None:
    raise PipelineError(code, message, field, details)


def _require_keys(value: Any, required: set[str], allowed: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(ErrorCode.CALIBRATION_SCHEMA, "expected object", field)
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        _fail(ErrorCode.CALIBRATION_SCHEMA, "required fields are missing", field, missing)
    if unknown:
        _fail(ErrorCode.CALIBRATION_SCHEMA, "unknown fields are not allowed", field, unknown)
    return value


def validate_transform(value: Any, field: str) -> np.ndarray:
    try:
        matrix = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        _fail(ErrorCode.TRANSFORM_SHAPE, "transform must be numeric", field, str(exc))
    if matrix.shape != (4, 4):
        _fail(ErrorCode.TRANSFORM_SHAPE, "transform must have shape [4,4]", field, list(matrix.shape))
    if not np.isfinite(matrix).all():
        _fail(ErrorCode.TRANSFORM_SINGULAR, "transform contains non-finite values", field)
    if not np.allclose(matrix[3], [0.0, 0.0, 0.0, 1.0], atol=1e-9):
        _fail(ErrorCode.TRANSFORM_SHAPE, "transform must use homogeneous column-vector convention", field)
    rotation = matrix[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6):
        _fail(ErrorCode.TRANSFORM_ROTATION, "rotation must be orthonormal", field)
    determinant = float(np.linalg.det(rotation))
    if not np.isclose(determinant, 1.0, atol=1e-6):
        _fail(ErrorCode.TRANSFORM_ROTATION, "rotation determinant must be +1", field, determinant)
    if abs(float(np.linalg.det(matrix))) < 1e-12:
        _fail(ErrorCode.TRANSFORM_SINGULAR, "transform is singular", field)
    return matrix


def validate_calibration(profile: dict[str, Any]) -> dict[str, Any]:
    profile = _require_keys(profile, {
        "schema_version", "profile_id", "calibration_status", "image", "camera_model",
        "depth", "transforms", "ground",
    }, TOP_LEVEL_KEYS, "$")
    if profile.get("schema_version") != CALIBRATION_SCHEMA:
        _fail(ErrorCode.CALIBRATION_SCHEMA, f"schema_version must be {CALIBRATION_SCHEMA}", "schema_version")
    if not isinstance(profile.get("profile_id"), str) or not profile["profile_id"]:
        _fail(ErrorCode.CALIBRATION_SCHEMA, "profile_id must be a non-empty string", "profile_id")
    if profile.get("calibration_status") not in CALIBRATION_STATUSES:
        _fail(ErrorCode.CALIBRATION_SCHEMA, "invalid calibration_status", "calibration_status")
    for key in ("absolute_geometry_valid", "physical_height_valid", "bottom_height_valid"):
        if key in profile and type(profile[key]) is not bool:
            _fail(ErrorCode.CALIBRATION_SCHEMA, f"{key} must be boolean", key)
        if profile.get(key, False) and profile["calibration_status"] != "measured":
            _fail(ErrorCode.CALIBRATION_SCHEMA, "unmeasured calibration cannot assert absolute validity", key)
    image = _require_keys(profile.get("image"), {"width", "height"}, {"width", "height"}, "image")
    if image.get("width") != 640 or image.get("height") != 360:
        _fail(ErrorCode.CALIBRATION_SCHEMA, "Stage 4 source image must be 640x360", "image")
    camera = _require_keys(profile.get("camera_model"), {"model", "fx", "fy", "cx", "cy", "distortion"},
                           {"model", "fx", "fy", "cx", "cy", "distortion"}, "camera_model")
    if camera.get("model") not in {"pinhole", "opencv_brown"}:
        _fail(ErrorCode.DISTORTION_MODEL, "model must be pinhole or opencv_brown", "camera_model.model")
    for name in ("fx", "fy", "cx", "cy"):
        value = camera.get(name)
        if not isinstance(value, (int, float)) or not np.isfinite(value):
            _fail(ErrorCode.CAMERA_INTRINSICS, f"{name} must be finite", f"camera_model.{name}")
    if camera["fx"] <= 0 or camera["fy"] <= 0:
        _fail(ErrorCode.CAMERA_INTRINSICS, "fx and fy must be positive", "camera_model")
    distortion = camera.get("distortion", [])
    if not isinstance(distortion, list) or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in distortion):
        _fail(ErrorCode.DISTORTION_MODEL, "distortion must be an array of finite numbers", "camera_model.distortion")
    if camera["model"] == "pinhole" and distortion not in ([], [0, 0, 0, 0, 0]):
        _fail(ErrorCode.DISTORTION_MODEL, "pinhole distortion must be empty/zero", "camera_model.distortion")
    if camera["model"] == "opencv_brown" and len(distortion) not in {4, 5, 8, 12, 14}:
        _fail(ErrorCode.DISTORTION_MODEL, "OpenCV Brown coefficients must use OpenCV order and length", "camera_model.distortion")
    depth = _require_keys(profile.get("depth"), {"semantics", "semantics_status", "scale_to_meter"},
                          {"semantics", "semantics_status", "scale_to_meter"}, "depth")
    if depth.get("semantics") not in SEMANTICS:
        _fail(ErrorCode.DEPTH_SEMANTICS, "depth semantics must be explicit", "depth.semantics")
    if depth.get("semantics_status") not in SEMANTICS_STATUSES:
        _fail(ErrorCode.DEPTH_SEMANTICS, "invalid semantics status", "depth.semantics_status")
    scale = depth.get("scale_to_meter")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not np.isfinite(scale) or scale <= 0:
        _fail(ErrorCode.DEPTH_SCALE, "scale_to_meter must be finite and positive", "depth.scale_to_meter")
    transforms = _require_keys(profile.get("transforms"), {"T_ground_from_camera", "T_camera_from_tof"},
                               {"T_ground_from_camera", "T_camera_from_tof"}, "transforms")
    ground_from_camera = validate_transform(transforms.get("T_ground_from_camera"), "transforms.T_ground_from_camera")
    camera_from_tof = validate_transform(transforms.get("T_camera_from_tof"), "transforms.T_camera_from_tof")
    if profile.get("absolute_geometry_valid", False) and (
        np.allclose(ground_from_camera, np.eye(4), atol=1e-9) or np.allclose(camera_from_tof, np.eye(4), atol=1e-9)
    ):
        _fail(ErrorCode.CALIBRATION_SCHEMA, "absolute geometry cannot be asserted with unresolved identity extrinsics", "absolute_geometry_valid")
    ground = _require_keys(profile.get("ground"), {"plane_z_m"}, {"plane_z_m"}, "ground")
    if not np.isfinite(ground.get("plane_z_m", np.nan)):
        _fail(ErrorCode.CALIBRATION_SCHEMA, "ground.plane_z_m is required", "ground.plane_z_m")
    return profile


def load_calibration(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        _fail(ErrorCode.CALIBRATION_MISSING, "calibration profile does not exist", str(path))
    try:
        profile = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite constant {value}")))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        _fail(ErrorCode.CALIBRATION_SCHEMA, "cannot read calibration profile", str(path), str(exc))
    return validate_calibration(profile)


def transform_points(points: np.ndarray, transform: Any) -> np.ndarray:
    matrix = validate_transform(transform, "transform")
    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        _fail(ErrorCode.TRANSFORM_SHAPE, "points must be finite [N,3]", "points")
    homogeneous = np.column_stack([points, np.ones(points.shape[0])])
    return (matrix @ homogeneous.T).T[:, :3]


def invert_transform(transform: Any) -> np.ndarray:
    matrix = validate_transform(transform, "transform")
    inverse = np.linalg.inv(matrix)
    validate_transform(inverse, "inverse_transform")
    return inverse
