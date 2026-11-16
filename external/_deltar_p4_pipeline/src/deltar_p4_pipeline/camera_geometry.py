from __future__ import annotations

from typing import Any

import numpy as np

from .errors import ErrorCode, PipelineError


def _validate_uv(uv: np.ndarray) -> np.ndarray:
    uv = np.asarray(uv, dtype=np.float64)
    if uv.ndim != 2 or uv.shape[1] != 2 or not np.isfinite(uv).all():
        raise PipelineError(ErrorCode.CAMERA_INTRINSICS, "pixel_uv must be finite [N,2]", "pixel_uv")
    return uv


def normalized_image_coordinates(pixel_uv: np.ndarray, camera_model: dict[str, Any]) -> np.ndarray:
    uv = _validate_uv(pixel_uv)
    fx, fy = float(camera_model["fx"]), float(camera_model["fy"])
    cx, cy = float(camera_model["cx"]), float(camera_model["cy"])
    if fx <= 0 or fy <= 0:
        raise PipelineError(ErrorCode.CAMERA_INTRINSICS, "fx/fy must be positive", "camera_model")
    if camera_model.get("model") == "pinhole":
        return np.column_stack([(uv[:, 0] - cx) / fx, (uv[:, 1] - cy) / fy])
    if camera_model.get("model") != "opencv_brown":
        raise PipelineError(ErrorCode.DISTORTION_MODEL, "unsupported distortion model", "camera_model.model")
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - formal runtime has OpenCV
        raise PipelineError(ErrorCode.DISTORTION_MODEL, "opencv_brown requires existing OpenCV", details=str(exc)) from exc
    intrinsic = np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]], dtype=np.float64)
    coeffs = np.asarray(camera_model.get("distortion", []), dtype=np.float64)
    return cv2.undistortPoints(uv.reshape(-1, 1, 2), intrinsic, coeffs).reshape(-1, 2)


def pixel_rays(pixel_uv: np.ndarray, camera_model: dict[str, Any], normalize: bool = True) -> np.ndarray:
    xy = normalized_image_coordinates(pixel_uv, camera_model)
    rays = np.column_stack([xy, np.ones(xy.shape[0])])
    if normalize:
        rays /= np.linalg.norm(rays, axis=1, keepdims=True)
    return rays


def backproject_pixels(
    pixel_uv: np.ndarray,
    depth_values_m: np.ndarray,
    camera_model: dict[str, Any],
    semantics: str,
) -> np.ndarray:
    uv = _validate_uv(pixel_uv)
    depth = np.asarray(depth_values_m, dtype=np.float64).reshape(-1)
    if depth.shape[0] != uv.shape[0] or not np.isfinite(depth).all() or np.any(depth <= 0):
        raise PipelineError(ErrorCode.DEPTH_SEMANTICS, "depth must be positive finite [N]", "depth_values_m")
    if semantics == "axial_z":
        rays = pixel_rays(uv, camera_model, normalize=False)
        return rays * depth[:, None]
    if semantics == "radial_range":
        rays = pixel_rays(uv, camera_model, normalize=True)
        return rays * depth[:, None]
    raise PipelineError(ErrorCode.DEPTH_SEMANTICS, "semantics must be axial_z or radial_range", "depth.semantics")


def project_points(points_camera: np.ndarray, camera_model: dict[str, Any]) -> np.ndarray:
    points = np.asarray(points_camera, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or np.any(points[:, 2] <= 0):
        raise PipelineError(ErrorCode.CAMERA_INTRINSICS, "camera points must be [N,3] with Z>0", "points_camera")
    normalized = points[:, :2] / points[:, 2:3]
    fx, fy, cx, cy = (float(camera_model[k]) for k in ("fx", "fy", "cx", "cy"))
    if camera_model.get("model") == "pinhole":
        return np.column_stack([normalized[:, 0] * fx + cx, normalized[:, 1] * fy + cy])
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover
        raise PipelineError(ErrorCode.DISTORTION_MODEL, "opencv_brown requires OpenCV", details=str(exc)) from exc
    intrinsic = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64)
    uv, _ = cv2.projectPoints(points, np.zeros(3), np.zeros(3), intrinsic, np.asarray(camera_model["distortion"]))
    return uv.reshape(-1, 2)
