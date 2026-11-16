from __future__ import annotations

from typing import Any

import numpy as np

from .calibration import invert_transform, transform_points
from .camera_geometry import backproject_pixels, project_points
from .object_geometry import summarize_geometry
from .point_cloud import build_point_cloud, erode_mask


def rotation_xyz(roll_deg: float = 0.0, pitch_deg: float = 0.0, yaw_deg: float = 0.0) -> np.ndarray:
    r, p, y = np.radians([roll_deg, pitch_deg, yaw_deg])
    rx = np.array([[1, 0, 0], [0, np.cos(r), -np.sin(r)], [0, np.sin(r), np.cos(r)]])
    ry = np.array([[np.cos(p), 0, np.sin(p)], [0, 1, 0], [-np.sin(p), 0, np.cos(p)]])
    rz = np.array([[np.cos(y), -np.sin(y), 0], [np.sin(y), np.cos(y), 0], [0, 0, 1]])
    return rz @ ry @ rx


def synthetic_calibration(*, semantics: str = "axial_z", status: str = "synthetic", camera_height_m: float = 0.8, roll_deg: float = 0.0, pitch_deg: float = 0.0, yaw_deg: float = 0.0, fx: float = 520.0, fy: float = 520.0, cx: float = 319.5, cy: float = 179.5) -> dict[str, Any]:
    base = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)
    rotation = rotation_xyz(roll_deg, pitch_deg, yaw_deg) @ base
    transform = np.eye(4)
    transform[:3, :3] = rotation
    transform[:3, 3] = [0.0, 0.0, camera_height_m]
    return {
        "schema_version": "deltar_geometry_calibration.v1",
        "profile_id": "synthetic_test_calibration",
        "calibration_status": status,
        "image": {"width": 640, "height": 360},
        "camera_model": {"model": "pinhole", "fx": fx, "fy": fy, "cx": cx, "cy": cy, "distortion": []},
        "depth": {"semantics": semantics, "semantics_status": "PROVEN" if status == "synthetic" else "PLAUSIBLE_BUT_UNPROVEN", "scale_to_meter": 1.0},
        "transforms": {"T_ground_from_camera": transform.tolist(), "T_camera_from_tof": np.eye(4).tolist()},
        "mount_geometry_hint": {"camera_and_tof_coplanar": True, "camera_on_tof_long_axis_extension": True, "tof_physical_rotation_present": True, "note": "hint only; not numeric calibration"},
        "ground": {"plane_z_m": 0.0},
    }


def rectangle_depth(calibration: dict[str, Any], *, distance_m: float, bottom_m: float, height_m: float, width_m: float = 0.8, samples_x: int = 220, samples_z: int = 220, slant_m: float = 0.0) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    x = np.linspace(-width_m / 2, width_m / 2, samples_x)
    z = np.linspace(bottom_m, bottom_m + height_m, samples_z)
    xx, zz = np.meshgrid(x, z)
    yy = distance_m + slant_m * xx / max(width_m, 1e-9)
    ground = np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()])
    camera = transform_points(ground, invert_transform(calibration["transforms"]["T_ground_from_camera"]))
    in_front = camera[:, 2] > 0
    camera, ground = camera[in_front], ground[in_front]
    uv = np.rint(project_points(camera, calibration["camera_model"])).astype(int)
    inside = (uv[:, 0] >= 0) & (uv[:, 0] < 640) & (uv[:, 1] >= 0) & (uv[:, 1] < 360)
    uv, camera, ground = uv[inside], camera[inside], ground[inside]
    source = np.zeros((360, 640), dtype=np.float64)
    depth_values = camera[:, 2] if calibration["depth"]["semantics"] == "axial_z" else np.linalg.norm(camera, axis=1)
    for (u, v), value in zip(uv, depth_values):
        if source[v, u] == 0 or value < source[v, u]:
            source[v, u] = value
    mask = source > 0
    radial = np.linalg.norm(camera, axis=1)
    truth = {
        "distance_m": float(np.linalg.norm(np.median(camera, axis=0))),
        "distance_axial_m": float(np.median(camera[:, 2])),
        "distance_radial_m": float(np.median(radial)),
        "bottom_height_m": bottom_m,
        "top_height_m": bottom_m + height_m,
        "object_height_m": height_m,
    }
    return source, mask, truth


def evaluate_synthetic(source: np.ndarray, mask: np.ndarray, calibration: dict[str, Any], profile: dict[str, Any], truth: dict[str, float], *, source_type: str = "segmentation", surface_depth_m: float | None = None, erode_pixels: int = 0) -> dict[str, Any]:
    region = erode_mask(mask, erode_pixels)
    cloud = build_point_cloud(source, region, calibration, profile, stage3_surface_depth_m=surface_depth_m, source_type=source_type)
    geometry = summarize_geometry(cloud, calibration, profile)
    errors = {name: abs(float(geometry[name]) - float(truth[name])) for name in ("distance_m", "object_height_m", "bottom_height_m")}
    return {"geometry": geometry, "truth": truth, "errors": errors, "filter_metrics": cloud["filter_metrics"]}


def semantics_roundtrip_error(semantics: str) -> dict[str, float]:
    calibration = synthetic_calibration(semantics=semantics)
    uv = np.array([[40.0, 40.0], [600.0, 300.0], [500.0, 80.0]])
    true_range = np.array([2.0, 2.5, 3.0])
    rays = np.column_stack([(uv[:, 0] - 319.5) / 520.0, (uv[:, 1] - 179.5) / 520.0, np.ones(3)])
    rays /= np.linalg.norm(rays, axis=1, keepdims=True)
    truth = rays * true_range[:, None]
    depth = truth[:, 2] if semantics == "axial_z" else true_range
    recovered = backproject_pixels(uv, depth, calibration["camera_model"], semantics)
    wrong = backproject_pixels(uv, depth, calibration["camera_model"], "radial_range" if semantics == "axial_z" else "axial_z")
    return {"correct_max_error_m": float(np.max(np.abs(recovered - truth))), "wrong_semantics_median_point_error_m": float(np.median(np.linalg.norm(wrong - truth, axis=1)))}
