import math
from typing import Any

import cv2
import numpy as np


METHOD = "opencv_pinhole_backprojection"


def _rx(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array(
        [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]],
        dtype=np.float64,
    )


def _rz(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array(
        [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


def _camera_to_ground_rotation(mount: dict[str, Any]) -> np.ndarray:
    # Camera frame: +X right, +Y down, +Z forward.
    # Ground frame: +X right, +Y forward, +Z up.
    base = np.array(
        [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]],
        dtype=np.float64,
    )
    yaw = math.radians(float(mount.get("yaw_deg", 0.0)))
    pitch = math.radians(float(mount.get("pitch_deg", 0.0)))
    roll = math.radians(float(mount.get("roll_deg", 0.0)))

    # pitch_deg > 0 means camera points downward.
    # roll_deg > 0 means clockwise image roll.
    return _rz(yaw) @ _rx(-pitch) @ base @ _rz(-roll)


def _undistorted_ray(u: float, v: float, camera: dict[str, Any]) -> np.ndarray:
    k = np.array(
        [
            [float(camera["fx"]), 0.0, float(camera["cx"])],
            [0.0, float(camera["fy"]), float(camera["cy"])],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    d = np.asarray(
        camera.get("distortion", [0.0, 0.0, 0.0, 0.0, 0.0]),
        dtype=np.float64,
    ).reshape(-1)

    point = np.array([[[u, v]]], dtype=np.float64)
    normalized = cv2.undistortPoints(point, k, d)[0, 0]
    return np.array([normalized[0], normalized[1], 1.0], dtype=np.float64)


def _camera_z_from_distance(
    center_ray: np.ndarray,
    distance_cm: float,
    config: dict[str, Any],
) -> float | None:
    mode = str(config["geometry"].get("distance_definition", "camera_radial"))
    unit_ray = center_ray / np.linalg.norm(center_ray)

    if mode == "camera_z":
        return distance_cm

    if mode == "camera_radial":
        return float(distance_cm * unit_ray[2])

    if mode == "tof_radial_aligned":
        extrinsic = config["tof_to_camera"]
        if not bool(extrinsic.get("calibration_ready", False)):
            return None

        # t is the ToF optical origin expressed in camera coordinates.
        t = np.asarray(
            extrinsic.get("translation_cm", [0.0, 0.0, 0.0]),
            dtype=np.float64,
        ).reshape(3)

        # Standard ray-sphere intersection:
        # object lies on the matched camera ray and at ToF radial range r.
        dot = float(np.dot(unit_ray, t))
        discriminant = dot * dot - (
            float(np.dot(t, t)) - distance_cm * distance_cm
        )
        if discriminant < 0.0:
            return None

        ray_length = dot + math.sqrt(discriminant)
        if ray_length <= 0.0:
            return None
        return float((ray_length * unit_ray)[2])

    return None


def _invalid(reason: str) -> dict[str, Any]:
    return {
        "object_height_cm": None,
        "bottom_height_from_ground_cm": None,
        "object_height_valid": False,
        "bottom_height_valid": False,
        "camera_depth_cm": None,
        "geometry_method": METHOD,
        "height_invalid_reason": reason,
    }


def estimate_height(
    bbox: dict[str, int],
    distance_cm: float | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    if distance_cm is None or distance_cm <= 0.0:
        return _invalid("distance_unavailable")

    camera = config["camera"]
    mount = config["camera_mount"]
    geometry_cfg = config["geometry"]

    if not bool(camera.get("calibration_ready", False)):
        return _invalid("camera_intrinsics_not_configured")
    if not bool(mount.get("calibration_ready", False)):
        return _invalid("camera_mount_not_configured")
    if float(camera.get("fx", 0.0)) <= 0.0 or float(camera.get("fy", 0.0)) <= 0.0:
        return _invalid("invalid_camera_intrinsics")

    x1, y1 = float(bbox["x1"]), float(bbox["y1"])
    x2, y2 = float(bbox["x2"]), float(bbox["y2"])
    if x2 <= x1 or y2 <= y1:
        return _invalid("invalid_bbox")

    u = (x1 + x2) / 2.0
    v = (y1 + y2) / 2.0

    center_ray = _undistorted_ray(u, v, camera)
    top_ray = _undistorted_ray(u, y1, camera)
    bottom_ray = _undistorted_ray(u, y2, camera)

    z_camera = _camera_z_from_distance(center_ray, float(distance_cm), config)
    if z_camera is None or z_camera <= 0.0:
        return _invalid("cannot_convert_distance_to_camera_depth")

    # Standard RGB-D approximation:
    # top and bottom lie on the same object front plane Z = z_camera.
    top_camera = top_ray * (z_camera / top_ray[2])
    bottom_camera = bottom_ray * (z_camera / bottom_ray[2])

    r_gc = _camera_to_ground_rotation(mount)
    camera_origin_ground = np.array(
        [
            float(mount.get("x_from_origin_cm", 0.0)),
            float(mount.get("y_from_origin_cm", 0.0)),
            float(mount["height_from_ground_cm"]),
        ],
        dtype=np.float64,
    )

    top_ground = r_gc @ top_camera + camera_origin_ground
    bottom_ground = r_gc @ bottom_camera + camera_origin_ground

    object_height = abs(float(top_ground[2] - bottom_ground[2]))
    bottom_height = float(bottom_ground[2])

    tolerance = float(geometry_cfg.get("ground_tolerance_cm", 3.0))
    if -tolerance <= bottom_height <= tolerance:
        bottom_height = 0.0

    valid_object = (
        float(geometry_cfg.get("minimum_object_height_cm", 1.0))
        <= object_height
        <= float(geometry_cfg.get("maximum_object_height_cm", 250.0))
    )
    valid_bottom = (
        float(geometry_cfg.get("minimum_bottom_height_cm", -10.0))
        <= bottom_height
        <= float(geometry_cfg.get("maximum_bottom_height_cm", 300.0))
    )

    if not valid_object or not valid_bottom:
        result = _invalid("geometry_out_of_configured_range")
        result["camera_depth_cm"] = round(z_camera, 2)
        return result

    return {
        "object_height_cm": round(object_height, 2),
        "bottom_height_from_ground_cm": round(bottom_height, 2),
        "object_height_valid": True,
        "bottom_height_valid": True,
        "camera_depth_cm": round(z_camera, 2),
        "geometry_method": METHOD,
        "height_invalid_reason": None,
    }
