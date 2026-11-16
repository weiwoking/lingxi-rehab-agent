from __future__ import annotations

import math
from typing import Any

import numpy as np

def gravity_points_from_depth(
    depth_m: np.ndarray,
    camera_model: dict[str, Any],
    gravity_up_camera: np.ndarray,
    camera_height_mm: float,
    *,
    min_depth_m: float,
    max_depth_m: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Back-project axial-Z and apply only the Stage3A gravity world-Z authority."""
    depth = np.asarray(depth_m, dtype=np.float32)
    if depth.ndim != 2:
        raise ValueError("depth_m must be a 2D source-aligned array")
    valid = (
        np.isfinite(depth)
        & (depth > float(min_depth_m))
        & (depth < float(max_depth_m))
    )
    yy, xx = np.nonzero(valid)
    compatible = np.full((*depth.shape, 3), np.nan, dtype=np.float64)
    camera_axial_z_mm = np.full(depth.shape, np.nan, dtype=np.float64)
    if xx.size == 0:
        return compatible, camera_axial_z_mm, valid
    uv = np.column_stack((xx, yy)).astype(np.float64)
    import cv2

    intrinsic = np.asarray(
        [
            [camera_model["fx"], 0.0, camera_model["cx"]],
            [0.0, camera_model["fy"], camera_model["cy"]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    normalized = cv2.undistortPoints(
        uv.reshape(-1, 1, 2),
        intrinsic,
        np.asarray(camera_model["distortion"], dtype=np.float64),
    ).reshape(-1, 2)
    z_mm = depth[yy, xx].astype(np.float64) * 1000.0
    camera_points = np.column_stack(
        (normalized[:, 0] * z_mm, normalized[:, 1] * z_mm, z_mm)
    )
    up = np.asarray(gravity_up_camera, dtype=np.float64)
    if up.shape != (3,) or not np.isfinite(up).all():
        raise ValueError("gravity_up_camera must be finite [3]")
    world_z = camera_points @ up + float(camera_height_mm)
    # X/Y deliberately remain camera-frame metric coordinates. They only
    # parameterize local plane fitting; no world yaw/full rotation is claimed.
    compatible[yy, xx, 0] = camera_points[:, 0]
    compatible[yy, xx, 1] = camera_points[:, 1]
    compatible[yy, xx, 2] = world_z
    camera_axial_z_mm[yy, xx] = z_mm
    return compatible, camera_axial_z_mm, valid


def _fit_plane_svd(points: np.ndarray) -> tuple[np.ndarray, float]:
    centre = np.mean(points, axis=0)
    _, _, vh = np.linalg.svd(points - centre, full_matrices=False)
    normal = vh[-1]
    normal /= np.linalg.norm(normal)
    if normal[2] < 0:
        normal = -normal
    return normal, -float(normal @ centre)


def _sample_plane(points: np.ndarray) -> tuple[np.ndarray, float] | None:
    first, second, third = points
    first_edge = second - first
    second_edge = third - first
    normal = np.asarray(
        [
            first_edge[1] * second_edge[2] - first_edge[2] * second_edge[1],
            first_edge[2] * second_edge[0] - first_edge[0] * second_edge[2],
            first_edge[0] * second_edge[1] - first_edge[1] * second_edge[0],
        ]
    )
    norm = float(np.linalg.norm(normal))
    if norm < 1e-8:
        return None
    normal /= norm
    if normal[2] < 0:
        normal = -normal
    return normal, -float(normal @ first)


def _largest_component(mask: np.ndarray) -> tuple[np.ndarray, list[int], list[list[int]]]:
    import cv2

    binary = np.asarray(mask, dtype=np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if count <= 1:
        return np.zeros_like(mask, dtype=bool), [0, 0, 0, 0], []
    label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    component = labels == label
    x = int(stats[label, cv2.CC_STAT_LEFT])
    y = int(stats[label, cv2.CC_STAT_TOP])
    width = int(stats[label, cv2.CC_STAT_WIDTH])
    height = int(stats[label, cv2.CC_STAT_HEIGHT])
    contours, _ = cv2.findContours(
        component.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    contour_xy: list[list[int]] = []
    if contours:
        contour = max(contours, key=cv2.contourArea)
        epsilon = max(1.0, 0.006 * cv2.arcLength(contour, True))
        polygon = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
        contour_xy = [[int(px), int(py)] for px, py in polygon]
    return component, [x, y, x + width - 1, y + height - 1], contour_xy


def _residual_summary(residual: np.ndarray) -> tuple[float, float, float]:
    return (
        float(np.sqrt(np.mean(np.square(residual)))),
        float(np.percentile(residual, 95)),
        float(np.max(residual)),
    )


def extract_horizontal_planes(
    world_points_mm: np.ndarray,
    valid_mask: np.ndarray,
    profile: dict[str, Any],
    *,
    ransac_batch_bound: int = 1,
) -> dict[str, Any]:
    """Deterministic dense-pixel RANSAC/SVD with additive diagnostic masks."""
    batch_bound = int(ransac_batch_bound)
    if batch_bound < 1 or batch_bound > 32:
        raise ValueError("ransac_batch_bound must be between 1 and 32")
    world = np.asarray(world_points_mm, dtype=np.float64)
    valid = np.asarray(valid_mask, dtype=bool)
    if world.ndim != 3 or world.shape[2] != 3 or world.shape[:2] != valid.shape:
        raise ValueError("world_points_mm must be [H,W,3] and match valid_mask")
    finite = np.isfinite(world).all(axis=2)
    valid &= finite
    height_values = world[..., 2][valid]
    height_statistics = {
        "valid_pixel_count": int(height_values.size),
        "min_mm": None if height_values.size == 0 else float(np.min(height_values)),
        "median_mm": None if height_values.size == 0 else float(np.median(height_values)),
        "max_mm": None if height_values.size == 0 else float(np.max(height_values)),
    }
    stride = int(profile["fit_stride_px"])
    sampled_mask = np.zeros_like(valid)
    sampled_mask[::stride, ::stride] = valid[::stride, ::stride]
    sy, sx = np.nonzero(sampled_mask)
    sampled = world[sy, sx]
    if sampled.shape[0] < 3:
        return {
            "planes": [],
            "rejected_candidates": [],
            "warnings": ["W_STAGE7D_INSUFFICIENT_VALID_DEPTH"],
            "world_height_statistics": height_statistics,
        }

    residual_gate = float(profile["plane_residual_threshold_mm"])
    rng = np.random.default_rng(int(profile["ransac_seed"]))
    remaining = np.ones(sampled.shape[0], dtype=bool)
    claimed_full = np.zeros_like(valid)
    candidates: list[dict[str, Any]] = []
    max_candidates = int(profile["max_planes"]) + 4
    min_sample_support = max(
        12,
        min(int(profile["min_ground_pixels"]), int(profile["min_support_pixels"]))
        // max(1, stride * stride * 3),
    )

    for _ in range(max_candidates):
        available_indices = np.flatnonzero(remaining)
        if available_indices.size < max(3, min_sample_support):
            break
        available_points = sampled[available_indices]
        best_local: np.ndarray | None = None
        best_count = 0
        iteration_count = int(profile["ransac_iterations"])
        if batch_bound == 1:
            for _iteration in range(iteration_count):
                chosen = rng.choice(available_points.shape[0], size=3, replace=False)
                fitted = _sample_plane(available_points[chosen])
                if fitted is None:
                    continue
                normal, offset = fitted
                inlier = np.abs(available_points @ normal + offset) <= residual_gate
                count = int(np.count_nonzero(inlier))
                if count > best_count:
                    best_count = count
                    best_local = inlier
        else:
            for batch_start in range(0, iteration_count, batch_bound):
                normals: list[np.ndarray] = []
                offsets: list[float] = []
                for _iteration in range(
                    batch_start, min(iteration_count, batch_start + batch_bound)
                ):
                    chosen = rng.choice(
                        available_points.shape[0], size=3, replace=False
                    )
                    fitted = _sample_plane(available_points[chosen])
                    if fitted is None:
                        continue
                    normal, offset = fitted
                    normals.append(normal)
                    offsets.append(offset)
                if not normals:
                    continue
                normal_matrix = np.column_stack(normals)
                residual_batch = np.abs(
                    available_points @ normal_matrix + np.asarray(offsets)
                )
                inlier_batch = residual_batch <= residual_gate
                counts = np.count_nonzero(inlier_batch, axis=0)
                for column, count_value in enumerate(counts):
                    count = int(count_value)
                    if count > best_count:
                        best_count = count
                        best_local = inlier_batch[:, column].copy()
        if best_local is None or best_count < min_sample_support:
            break
        inlier_indices = available_indices[best_local]
        normal, offset = _fit_plane_svd(sampled[inlier_indices])
        refined_sample_residual = np.abs(sampled @ normal + offset)
        sample_inlier = remaining & (refined_sample_residual <= residual_gate)
        if int(np.count_nonzero(sample_inlier)) < min_sample_support:
            remaining[inlier_indices] = False
            continue
        normal, offset = _fit_plane_svd(sampled[sample_inlier])
        full_residual = np.full(valid.shape, np.inf, dtype=np.float64)
        full_residual[valid] = np.abs(world[valid] @ normal + offset)
        full_inlier = valid & ~claimed_full & (full_residual <= residual_gate)
        component, bbox, contour = _largest_component(full_inlier)
        claimed_full |= full_inlier
        remaining &= refined_sample_residual > residual_gate
        pixel_count = int(np.count_nonzero(component))
        if pixel_count < 3:
            continue
        component_points = world[component]
        normal, offset = _fit_plane_svd(component_points)
        residual = np.abs(component_points @ normal + offset)
        rms, p95, maximum = _residual_summary(residual)
        median_height = float(np.median(component_points[:, 2]))
        tilt_deg = float(math.degrees(math.acos(float(np.clip(normal[2], -1.0, 1.0)))))
        span_x = bbox[2] - bbox[0] + 1
        span_y = bbox[3] - bbox[1] + 1
        candidates.append(
            {
                "normal_world": [float(item) for item in normal],
                "median_height_mm": median_height,
                "plane_offset_mm": float(offset),
                "residual_rms_mm": rms,
                "residual_p95_mm": p95,
                "residual_max_mm": maximum,
                "pixel_count": pixel_count,
                "bbox_xyxy": bbox,
                "contour_xy": contour,
                "confidence_diagnostics": {
                    "normal_tilt_deg": tilt_deg,
                    "sample_inlier_count": int(np.count_nonzero(sample_inlier)),
                    "visible_span_px": int(max(span_x, span_y)),
                    "component_span_xy_px": [int(span_x), int(span_y)],
                    "residual_threshold_mm": residual_gate,
                },
                "_mask": component,
            }
        )

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    horizontal_cos = math.cos(math.radians(float(profile["max_horizontal_tilt_deg"])))
    for candidate in candidates:
        normal_z = float(candidate["normal_world"][2])
        height = float(candidate["median_height_mm"])
        pixels = int(candidate["pixel_count"])
        span = int(candidate["confidence_diagnostics"]["visible_span_px"])
        label: str | None = None
        reason: str | None = None
        if normal_z < horizontal_cos:
            reason = "non_horizontal"
        elif (
            abs(height) <= float(profile["ground_abs_height_gate_mm"])
            and pixels >= int(profile["min_ground_pixels"])
            and span >= int(profile["min_visible_span_px"])
        ):
            label = "ground"
        elif (
            float(profile["min_support_height_mm"])
            <= height
            <= float(profile["max_support_height_mm"])
            and pixels >= int(profile["min_support_pixels"])
            and span >= int(profile["min_visible_span_px"])
        ):
            label = "support_surface"
        elif pixels < min(
            int(profile["min_ground_pixels"]), int(profile["min_support_pixels"])
        ):
            reason = "insufficient_pixel_support"
        elif span < int(profile["min_visible_span_px"]):
            reason = "insufficient_visible_span"
        else:
            reason = "height_out_of_demo_range"
        if label is not None:
            item = dict(candidate)
            item["label"] = label
            accepted.append(item)
        else:
            rejected.append(
                {
                    "reason": reason,
                    "normal": candidate["normal_world"],
                    "height": height,
                    "pixel_count": pixels,
                    "bbox_xyxy": candidate["bbox_xyxy"],
                    "contour_xy": candidate["contour_xy"],
                    "confidence_diagnostics": candidate["confidence_diagnostics"],
                    "_mask": candidate["_mask"],
                }
            )

    accepted.sort(
        key=lambda item: (
            0 if item["label"] == "ground" else 1,
            float(item["median_height_mm"]),
            -int(item["pixel_count"]),
        )
    )
    accepted = accepted[: int(profile["max_planes"])]
    for index, item in enumerate(accepted):
        item["plane_id"] = f"plane_{index:02d}"
    rejected.sort(key=lambda item: (-int(item["pixel_count"]), str(item["reason"])))
    warnings: list[str] = []
    if not accepted:
        warnings.append("W_STAGE7D_NO_CLASSIFIED_HORIZONTAL_PLANE")
    if not any(item["label"] == "ground" for item in accepted):
        warnings.append("W_STAGE7D_NO_GROUND_CANDIDATE")
    if not any(item["label"] == "support_surface" for item in accepted):
        warnings.append("W_STAGE7D_NO_SUPPORT_SURFACE_CANDIDATE")
    return {
        "planes": accepted,
        "rejected_candidates": rejected,
        "warnings": warnings,
        "world_height_statistics": height_statistics,
    }
