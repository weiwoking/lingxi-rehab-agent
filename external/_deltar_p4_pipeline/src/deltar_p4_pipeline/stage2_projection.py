from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from .canvas import CanvasResult, transform_fr
from .formal_geometry_authority import FormalGeometryAuthority


def valid_zone_mask(tof: dict[str, Any]) -> np.ndarray:
    distance = np.asarray(tof["distance_mm"], dtype=np.float64)
    status = np.asarray(tof["target_status"])
    count = np.asarray(tof["nb_target_detected"])
    sigma = np.asarray(tof["range_sigma_mm"], dtype=np.float64)
    if any(value.shape != (64,) for value in (distance, status, count, sigma)):
        raise ValueError("formal ToF arrays must be raw-order [64]")
    return (
        np.isfinite(distance)
        & (distance > 0.0)
        & np.isfinite(sigma)
        & (sigma > 0.0)
        & (count != 0)
        & (status != 255)
    )


def project_source_rectangles(
    authority: FormalGeometryAuthority, axial_depth_mm: np.ndarray
) -> np.ndarray:
    depth = np.asarray(axial_depth_mm, dtype=np.float64)
    if depth.shape != (64,) or not np.isfinite(depth).all() or np.any(depth <= 0.0):
        raise ValueError("effective axial depth must be positive finite raw-order [64]")
    stage2 = authority.stage2
    camera = authority.camera_model
    intrinsic = np.asarray(
        [
            [camera["fx"], 0.0, camera["cx"]],
            [0.0, camera["fy"], camera["cy"]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    distortion = np.asarray(camera["distortion"], dtype=np.float64)
    transform = stage2["camera_tof"]["T_camera_from_tof"]
    rotation = np.asarray(transform["R"], dtype=np.float64)
    translation = np.asarray(transform["t_mm"], dtype=np.float64)
    rays = np.asarray(
        [
            item["tof_frame_corner_rays_model_derived"]
            for item in stage2["camera_tof"]["raw_zone_camera_mapping"]
        ],
        dtype=np.float64,
    )
    if rays.shape != (64, 4, 3) or np.any(np.abs(rays[..., 2]) < 1e-12):
        raise ValueError("formal Stage2 corner rays must be finite [64,4,3] with nonzero z")
    points_tof = rays * (depth[:, None, None] / rays[..., 2:3])
    points_camera = points_tof @ rotation.T + translation
    projected, _ = cv2.projectPoints(
        points_camera.reshape(-1, 3),
        np.zeros(3, dtype=np.float64),
        np.zeros(3, dtype=np.float64),
        intrinsic,
        distortion,
    )
    uv = projected.reshape(64, 4, 2)
    rectangles = np.column_stack(
        (
            uv[..., 1].min(axis=1),
            uv[..., 0].min(axis=1),
            uv[..., 1].max(axis=1),
            uv[..., 0].max(axis=1),
        )
    )
    if not np.isfinite(rectangles).all():
        raise ValueError("Stage2 projection produced non-finite rectangles")
    return rectangles


def dynamic_rectangles(
    authority: FormalGeometryAuthority,
    tof: dict[str, Any],
    canvas_result: CanvasResult,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    distance = np.asarray(tof["distance_mm"], dtype=np.float64)
    valid = valid_zone_mask(tof)
    if not np.any(valid):
        raise ValueError("formal Stage2 projection has no valid ToF zone")
    median = float(np.median(distance[valid]))
    effective = np.where(valid, distance, median)
    source = project_source_rectangles(authority, effective)
    canvas = transform_fr(source, canvas_result.source_to_canvas).astype(np.float32)
    return canvas, valid, {
        "authority": authority.manifest["profile_id"],
        "fusion": "D_TRUE_READ_OFFICIAL_WRITE",
        "measurement_semantics": "axial_z",
        "zone_order": "raw_index_0_to_63_identity",
        "invalid_zone_policy": "mask invalid; median axial-z used only to construct ignored read rectangle",
        "invalid_zone_projection_depth_mm": median,
        "valid_zone_count": int(valid.sum()),
        "canvas_height": int(canvas_result.rgb.shape[0]),
        "canvas_width": int(canvas_result.rgb.shape[1]),
    }


def official_envelope_grid(rectangles: np.ndarray) -> np.ndarray:
    """Official 8x8 write topology spanning the Stage2 projected envelope."""
    rects = np.asarray(rectangles, dtype=np.float64)
    if rects.shape != (64, 4) or not np.isfinite(rects).all():
        raise ValueError("formal rectangles must be finite [64,4]")
    sy, sx = float(rects[:, 0].min()), float(rects[:, 1].min())
    ey, ex = float(rects[:, 2].max()), float(rects[:, 3].max())
    ys = np.linspace(sy, ey, 9)
    xs = np.linspace(sx, ex, 9)
    return np.asarray(
        [
            [ys[row], xs[column], ys[row + 1], xs[column + 1]]
            for row in range(8)
            for column in range(8)
        ],
        dtype=np.float32,
    )
