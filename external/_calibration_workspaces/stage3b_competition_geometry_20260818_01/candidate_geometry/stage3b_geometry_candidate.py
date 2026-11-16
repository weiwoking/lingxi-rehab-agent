#!/usr/bin/env python3
"""Validation-only Stage3B geometry attribution and candidate replay."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np

WORKSPACE = Path(r"C:\ec\_calibration_workspaces\stage3b_competition_geometry_20260818_01")
CANDIDATE_ROOT = WORKSPACE / "candidate_geometry"
RESULT_ROOT = CANDIDATE_ROOT / "results"
EXPECTED_PYTHON = Path(r"C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe")
PIPELINE_SRC = Path(r"C:\ec\_deltar_p4_pipeline\src")
for item in (WORKSPACE, PIPELINE_SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

import stage3b_competition_geometry as stage3b  # noqa: E402
from stage2_dynamic_fr_adapter import stage2_camera_model  # noqa: E402

CONFIG_PATH = CANDIDATE_ROOT / "candidate_config.v1.json"
PERCENTILES = (1, 2, 5, 10, 50, 90, 95, 97, 98, 99, 100)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    stage3b.atomic_json(path, value)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _largest_component(mask: np.ndarray) -> np.ndarray:
    binary = np.asarray(mask, dtype=np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    if count <= 1:
        return np.zeros(binary.shape, dtype=bool)
    label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return labels == label


def _ellipse_from_bbox(bbox: list[float], shape: tuple[int, int] = (360, 640)) -> np.ndarray:
    x1, y1, x2, y2 = (float(v) for v in bbox)
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    ax = max(1, int(round((x2 - x1) / 2.0)))
    ay = max(1, int(round((y2 - y1) / 2.0)))
    result = np.zeros(shape, dtype=np.uint8)
    cv2.ellipse(result, (int(round(cx)), int(round(cy))), (ax, ay), 0, 0, 360, 1, -1)
    return result.astype(bool)


def _undistorted_rays(uv: np.ndarray, camera: dict[str, Any]) -> np.ndarray:
    intrinsic = np.asarray(
        [[camera["fx"], 0.0, camera["cx"]], [0.0, camera["fy"], camera["cy"]], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    xy = cv2.undistortPoints(
        np.asarray(uv, dtype=np.float64).reshape(-1, 1, 2),
        intrinsic,
        np.asarray(camera["distortion"], dtype=np.float64),
    ).reshape(-1, 2)
    return np.column_stack((xy, np.ones(xy.shape[0], dtype=np.float64)))


def _project(points_camera_mm: np.ndarray, camera: dict[str, Any]) -> np.ndarray:
    intrinsic = np.asarray(
        [[camera["fx"], 0.0, camera["cx"]], [0.0, camera["fy"], camera["cy"]], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    uv, _ = cv2.projectPoints(
        np.asarray(points_camera_mm, dtype=np.float64).reshape(-1, 1, 3),
        np.zeros(3), np.zeros(3), intrinsic,
        np.asarray(camera["distortion"], dtype=np.float64),
    )
    return uv.reshape(-1, 2)


def _bottom_contour(mask: np.ndarray) -> np.ndarray:
    xs = np.flatnonzero(np.any(mask, axis=0))
    if xs.size == 0:
        return np.empty((0, 2), dtype=np.float64)
    ys = np.asarray([np.flatnonzero(mask[:, x])[-1] for x in xs], dtype=np.float64)
    keep = np.ones(xs.size, dtype=bool)
    for index in range(xs.size):
        neighbourhood = ys[max(0, index - 2):min(xs.size, index + 3)]
        if neighbourhood.size >= 3:
            keep[index] = abs(ys[index] - float(np.median(neighbourhood))) <= 3.0
    return np.column_stack((xs[keep], ys[keep]))


def _anchors(mask: np.ndarray, config: dict[str, Any]) -> np.ndarray:
    contour = _bottom_contour(mask)
    if contour.shape[0] < int(config["min_anchor_count"]):
        return np.empty((0, 2), dtype=np.float64)
    trim = float(config["base_anchor_edge_trim_fraction"])
    lo, hi = np.quantile(contour[:, 0], [trim, 1.0 - trim])
    usable = contour[(contour[:, 0] >= lo) & (contour[:, 0] <= hi)]
    count = min(int(config["max_anchor_count"]), usable.shape[0])
    indices = np.unique(np.rint(np.linspace(0, usable.shape[0] - 1, count)).astype(int))
    return usable[indices]


def _robust_depth(values: np.ndarray, scale: float) -> tuple[float | None, dict[str, Any]]:
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values) & (values > 0.0)]
    if not values.size:
        return None, {"count": 0, "median_mm": None, "mad_mm": None}
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    limit = max(30.0, float(scale) * 1.4826 * mad)
    kept = values[np.abs(values - median) <= limit]
    return float(np.median(kept)), {
        "count": int(values.size), "kept_count": int(kept.size), "median_mm": median,
        "mad_mm": mad, "limit_mm": limit,
    }


def _contact_depth(mask: np.ndarray, axial: np.ndarray, config: dict[str, Any]) -> tuple[float | None, dict[str, Any]]:
    yy, xx = np.nonzero(mask)
    if not yy.size:
        return None, {"reason": "empty_silhouette"}
    x1, x2 = float(np.min(xx)), float(np.max(xx))
    y1, y2 = float(np.min(yy)), float(np.max(yy))
    width_fraction = float(config["central_width_fraction"])
    cx = (x1 + x2) / 2.0
    half = (x2 - x1 + 1.0) * width_fraction / 2.0
    ya = y1 + float(config["lower_fraction_start"]) * (y2 - y1 + 1.0)
    yb = y1 + float(config["lower_fraction_end"]) * (y2 - y1 + 1.0)
    band = mask.copy()
    gy, gx = np.indices(mask.shape)
    band &= (gx >= cx - half) & (gx <= cx + half) & (gy >= ya) & (gy <= yb)
    object_depth, object_diagnostic = _robust_depth(axial[band], float(config["depth_mad_scale"]))
    object_diagnostic.update({"band_pixel_count": int(np.count_nonzero(band)), "y_range": [ya, yb]})

    # Stage7E deliberately does not trust dense depth on thin/transparent
    # containers.  Sample the visible support immediately below the robust
    # lower contour instead; this is the image-space analogue of its plane
    # contact search and does not use any nominal shelf height.
    contour = _bottom_contour(mask)
    support_band = np.zeros(mask.shape, dtype=bool)
    for x_value, y_value in contour:
        x = int(round(x_value))
        y = int(round(y_value))
        if x < cx - half or x > cx + half:
            continue
        y_start = min(mask.shape[0], y + 2)
        y_stop = min(mask.shape[0], y + 13)
        if y_stop > y_start:
            support_band[y_start:y_stop, max(0, x - 1):min(mask.shape[1], x + 2)] = True
    support_band &= ~mask
    support_depth, support_diagnostic = _robust_depth(
        axial[support_band], float(config["depth_mad_scale"])
    )
    support_diagnostic["band_pixel_count"] = int(np.count_nonzero(support_band))
    minimum = int(config["min_depth_pixels"])
    if support_diagnostic.get("count", 0) >= minimum:
        return support_depth, {
            "reason": None,
            "selected_source": "support_context_below_contour",
            "selected_depth_mm": support_depth,
            "support_context": support_diagnostic,
            "object_lower_interior": object_diagnostic,
        }
    if object_diagnostic.get("count", 0) >= minimum:
        return object_depth, {
            "reason": None,
            "selected_source": "object_lower_interior_fallback",
            "selected_depth_mm": object_depth,
            "support_context": support_diagnostic,
            "object_lower_interior": object_diagnostic,
        }
    return None, {
        "reason": "insufficient_contact_depth",
        "selected_source": None,
        "support_context": support_diagnostic,
        "object_lower_interior": object_diagnostic,
    }


def _candidate_estimate(
    class_name: str,
    bbox: list[float],
    raw_mask: np.ndarray | None,
    axial: np.ndarray,
    world_points: np.ndarray,
    camera: dict[str, Any],
    gravity: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    availability = config["availability"]
    if raw_mask is not None and np.asarray(raw_mask).shape == axial.shape and np.any(raw_mask):
        silhouette = _largest_component(np.asarray(raw_mask, dtype=bool))
        source = "instance_mask"
    elif class_name == "sports ball" and availability["sports_ball_allows_detector_ellipse"]:
        silhouette = _ellipse_from_bbox(bbox, axial.shape)
        source = "detector_ellipse"
    else:
        return {"available": False, "reason": "instance_mask_required", "source": "none"}
    sphere_audit = None
    if class_name == "sports ball":
        fitted = _robust_sphere_estimate(silhouette, axial, world_points, gravity, config["sphere_fit"])
        if fitted.get("available"):
            fitted["source"] = source
            return fitted
        sphere_audit = fitted
    scfg = config["silhouette"]
    if class_name == "sports ball" and int(scfg.get("sports_ball_proxy_erosion_px", 0)):
        silhouette = cv2.erode(
            silhouette.astype(np.uint8), np.ones((3, 3), np.uint8),
            iterations=int(scfg["sports_ball_proxy_erosion_px"]),
        ).astype(bool)
    anchors = _anchors(silhouette, scfg)
    if anchors.shape[0] < int(scfg["min_anchor_count"]):
        return {"available": False, "reason": "insufficient_base_anchors", "source": source}
    depth, depth_diag = _contact_depth(silhouette, axial, config["contact_band"])
    if depth is None:
        return {"available": False, "reason": "contact_depth_unavailable", "source": source, "contact_depth": depth_diag}
    rays = _undistorted_rays(anchors, camera)
    bases = rays * float(depth)
    up = np.asarray(gravity["gravity_authority"]["u_up_camera"], dtype=np.float64)
    camera_height = float(gravity["camera_height_mm"])
    base_z = bases @ up + camera_height
    heights = np.arange(
        float(scfg["height_scan_min_mm"]),
        float(scfg["height_scan_max_mm"]) + 0.5 * float(scfg["height_scan_step_mm"]),
        float(scfg["height_scan_step_mm"]),
    )
    tolerance = int(scfg["mask_hole_tolerance_px"])
    test_mask = silhouette.astype(np.uint8)
    if tolerance:
        test_mask = cv2.dilate(test_mask, np.ones((2 * tolerance + 1, 2 * tolerance + 1), np.uint8))
    candidates: list[float] = []
    rejected: list[str] = []
    for base in bases:
        points = base[None, :] + heights[:, None] * up[None, :]
        uv = _project(points, camera)
        xy = np.rint(uv).astype(int)
        inside_image = (
            (xy[:, 0] >= 0) & (xy[:, 0] < silhouette.shape[1]) &
            (xy[:, 1] >= 0) & (xy[:, 1] < silhouette.shape[0])
        )
        inside = np.zeros(heights.size, dtype=bool)
        good = xy[inside_image]
        inside[inside_image] = test_mask[good[:, 1], good[:, 0]] != 0
        indices = np.flatnonzero(inside)
        if indices.size < 3 or int(indices[0]) > 2:
            rejected.append("unstable_mask_run")
            continue
        first, last = int(indices[0]), int(indices[-1])
        if np.count_nonzero(~inside[first:last + 1]) > max(1, tolerance):
            rejected.append("mask_run_has_gaps")
            continue
        candidates.append(float(heights[last]))
    if len(candidates) < int(scfg["min_anchor_count"]):
        return {
            "available": False, "reason": "insufficient_valid_height_anchors", "source": source,
            "anchor_count": int(anchors.shape[0]), "valid_anchor_count": len(candidates),
            "contact_depth": depth_diag,
        }
    values = np.asarray(candidates, dtype=np.float64)
    if class_name == "sports ball":
        cutoff = float(np.quantile(values, 0.8))
        selected = values[values >= cutoff]
        height = float(np.median(selected))
        height_statistic = "upper_quintile_median"
    else:
        height = float(np.median(values))
        height_statistic = "median"
    support = float(np.median(base_z))
    return {
        "available": True,
        "reason": None,
        "support_source": "candidate_contact_band",
        "support_mm": support,
        "top_mm": support + height,
        "object_height_mm": height,
        "source": source,
        "height_method": "gravity_only_upright_silhouette" if class_name != "sports ball" else "gravity_only_sphere_envelope",
        "height_statistic": height_statistic,
        "anchor_count": int(anchors.shape[0]),
        "valid_anchor_count": int(values.size),
        "candidate_height_p25_mm": float(np.percentile(values, 25)),
        "candidate_height_median_mm": float(np.median(values)),
        "candidate_height_p75_mm": float(np.percentile(values, 75)),
        "candidate_height_min_mm": float(np.min(values)),
        "candidate_height_max_mm": float(np.max(values)),
        "support_anchor_mad_mm": float(np.median(np.abs(base_z - np.median(base_z)))),
        "contact_depth": depth_diag,
        "sphere_fit_fallback_audit": sphere_audit,
    }


def _robust_sphere_estimate(
    silhouette: np.ndarray,
    axial: np.ndarray,
    world_points: np.ndarray,
    gravity: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Fit a free-radius sphere to the coherent foreground; nominal size is absent."""
    from scipy.optimize import least_squares

    finite = silhouette & np.isfinite(axial) & (axial > 0.0) & np.isfinite(world_points).all(axis=2)
    values = axial[finite]
    minimum = int(config["min_points"])
    if values.size < minimum:
        return {"available": False, "reason": "sphere_fit_insufficient_points"}
    # Use the nearest coherent 50 mm histogram mode and its neighbours.  This
    # separates the opaque ball surface from the cabinet background without a
    # radius or truth prior.
    lo, hi = np.percentile(values, [1, 99])
    edges = np.arange(math.floor(lo / 50.0) * 50.0, hi + 100.0, 50.0)
    counts, edges = np.histogram(values, bins=edges)
    if not counts.size or not np.max(counts):
        return {"available": False, "reason": "sphere_fit_no_depth_mode"}
    peak_candidates = np.flatnonzero(counts == np.max(counts))
    peak = int(peak_candidates[0])
    depth_keep = finite & (axial >= edges[max(0, peak - 1)]) & (axial <= edges[min(len(edges) - 1, peak + 2)])
    points = np.column_stack((world_points[..., 0][depth_keep], world_points[..., 1][depth_keep], axial[depth_keep]))
    if points.shape[0] < minimum:
        return {"available": False, "reason": "sphere_fit_cluster_too_small", "point_count": int(points.shape[0])}
    centre0 = np.mean(points, axis=0)
    radius0 = float(np.median(np.linalg.norm(points - centre0, axis=1)))
    def residual(params: np.ndarray) -> np.ndarray:
        return np.linalg.norm(points - params[:3], axis=1) - params[3]
    lower_radius, upper_radius = (float(v) for v in config["radius_range_mm"])
    solved = least_squares(
        residual,
        np.r_[centre0, np.clip(radius0, lower_radius, upper_radius)],
        bounds=(np.r_[[-np.inf] * 3, lower_radius], np.r_[[np.inf] * 3, upper_radius]),
        loss=str(config["loss"]), f_scale=float(config["f_scale_mm"]), max_nfev=300,
    )
    res = residual(solved.x)
    rms = float(np.sqrt(np.mean(np.square(res))))
    singular = np.linalg.svd(solved.jac, compute_uv=False)
    condition = float(np.inf if singular[-1] <= 1e-12 else singular[0] / singular[-1])
    radius = float(solved.x[3])
    relative_residual = rms / max(radius, 1e-9)
    gate = (
        solved.success and rms <= float(config["max_residual_rms_mm"])
        and relative_residual <= float(config["max_relative_residual"])
        and condition <= float(config["max_jacobian_condition"])
    )
    diagnostic = {
        "point_count": int(points.shape[0]), "radius_mm": radius,
        "residual_rms_mm": rms, "jacobian_condition": condition,
        "relative_residual": relative_residual,
        "solver_success": bool(solved.success), "gate_pass": bool(gate),
    }
    if not gate:
        return {"available": False, "reason": "sphere_fit_quality_gate", "sphere_fit": diagnostic}
    up = np.asarray(gravity["gravity_authority"]["u_up_camera"], dtype=np.float64)
    centre_world_z = float(solved.x[:3] @ up + float(gravity["camera_height_mm"]))
    support = centre_world_z - radius
    height = 2.0 * radius
    return {
        "available": True, "reason": None,
        "support_source": "candidate_sphere_fit",
        "support_mm": support, "top_mm": centre_world_z + radius,
        "object_height_mm": height,
        "height_method": "robust_free_radius_sphere_fit",
        "height_statistic": "fitted_diameter",
        "sphere_fit": diagnostic,
    }


def _distribution_summary(
    obj: dict[str, Any], group: dict[str, Any], segment_masks: dict[str, np.ndarray],
    axial: np.ndarray, world_points: np.ndarray, profile: dict[str, Any],
) -> tuple[dict[str, Any], np.ndarray | None]:
    import deltar_p4_pipeline.all_object_geometry as aog

    _, selected = aog._selected_observation(group)
    bbox = [float(v) for v in selected.get("bbox_xyxy", obj.get("bbox_xyxy"))]
    mask_key = selected.get("mask_key")
    raw_mask = segment_masks.get(str(mask_key)) if mask_key is not None else None
    finite = np.isfinite(axial) & (axial > 0) & np.isfinite(world_points[..., 2])
    region = None
    keep = None
    if raw_mask is not None and np.asarray(raw_mask).shape == axial.shape and np.any(raw_mask):
        raw = np.asarray(raw_mask, dtype=bool)
        eroded = aog.erode_mask_one_pixel(raw)
        if np.count_nonzero(eroded & finite) >= int(profile["min_points"]):
            region = eroded
            keep = aog._robust_keep(eroded, finite, axial, profile)
        else:
            region = raw
            keep = aog._robust_keep(raw, finite, axial, profile)
    if region is None:
        region = aog._bbox_mask(bbox, 1.0)
        cluster = aog._main_depth_cluster(region, finite, axial, profile) if region is not None else None
        keep = cluster if cluster is not None else (aog._robust_keep(region, finite, axial, profile) if region is not None else None)
    valid = keep & finite if keep is not None else np.zeros(axial.shape, dtype=bool)
    depths = axial[valid]
    heights = world_points[..., 2][valid]
    ys, xs = np.nonzero(valid)
    zstats = {f"p{p:02d}" if p < 100 else "max": float(np.percentile(heights, p)) for p in PERCENTILES} if heights.size else {}
    dstats = {name: float(value) for name, value in zip(
        ("p05", "median", "p95"), np.percentile(depths, (5, 50, 95))
    )} if depths.size else {}
    if heights.size:
        top_gate = float(np.percentile(heights, 95))
        bottom_gate = float(np.percentile(heights, 5))
        top_sel = heights >= top_gate
        boundary = np.zeros(valid.shape, dtype=np.uint8)
        boundary[ys[top_sel], xs[top_sel]] = 1
        object_boundary = cv2.morphologyEx(region.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)).astype(bool)
        top_boundary_fraction = float(np.mean(object_boundary[ys[top_sel], xs[top_sel]])) if np.any(top_sel) else None
        top_location = [float(np.median(xs[top_sel])), float(np.median(ys[top_sel]))] if np.any(top_sel) else None
        upper_count, lower_count = int(np.count_nonzero(top_sel)), int(np.count_nonzero(heights <= bottom_gate))
        mad = float(np.median(np.abs(heights - np.median(heights))))
        iqr = float(np.percentile(heights, 75) - np.percentile(heights, 25))
    else:
        top_boundary_fraction = top_location = None
        upper_count = lower_count = 0
        mad = iqr = None
    diagnostics = obj.get("diagnostics", {})
    return {
        "frame_id": group.get("provenance", {}).get("frame_id"),
        "logical_object_id": obj.get("logical_object_id"),
        "class_name": obj.get("class_name"),
        "bbox_xyxy": bbox,
        "region_source": diagnostics.get("region_source"),
        "region_method": diagnostics.get("region_method"),
        "mask_vs_bbox": "mask" if raw_mask is not None else "bbox",
        "object_pixel_count": int(np.count_nonzero(region)) if region is not None else 0,
        "valid_dense_depth_count": int(depths.size),
        "coverage": float(depths.size / max(1, np.count_nonzero(region))) if region is not None else 0.0,
        "touches_border": diagnostics.get("touches_border"),
        "detect_segment_agreement": diagnostics.get("detect_segment_agreed"),
        "axial_depth_mm": dstats,
        "world_z_mm": zstats,
        "world_z_min_mm": float(np.min(heights)) if heights.size else None,
        "world_z_mad_mm": mad,
        "world_z_iqr_mm": iqr,
        "upper_tail_pixel_count": upper_count,
        "lower_tail_pixel_count": lower_count,
        "top_percentile_spatial_median_xy": top_location,
        "top_points_on_region_boundary_fraction": top_boundary_fraction,
        "confidence": obj.get("geometry_confidence"),
        "confidence_ingredients": diagnostics.get("quality_components", {}),
    }, None if raw_mask is None else np.asarray(raw_mask, dtype=bool)


class CaptureState:
    records: list[dict[str, Any]] = []
    planes: list[dict[str, Any]] = []


def _processor_class(base_class: type, camera: dict[str, Any], gravity: dict[str, Any], config: dict[str, Any]) -> type:
    class CapturingProcessor(base_class):
        def process_frame(self, frame_id, sequence, logical_groups, segment_masks, camera_axial_z_mm, world_points_or_z_mm, support_planes, source_image_path):
            frame = super().process_frame(
                frame_id, sequence, logical_groups, segment_masks, camera_axial_z_mm,
                world_points_or_z_mm, support_planes, source_image_path,
            )
            world = np.asarray(world_points_or_z_mm)
            from deltar_p4_pipeline.horizontal_planes import extract_horizontal_planes
            plane_profile = read_json(Path(r"C:\ec\_deltar_p4_pipeline\profiles\stage7d_horizontal_plane_profile.json"))
            plane_valid = np.isfinite(world).all(axis=2)
            plane_extraction = extract_horizontal_planes(world, plane_valid, plane_profile)
            plane_items = []
            for qualification, values in (
                ("accepted", plane_extraction.get("planes", [])),
                ("rejected", plane_extraction.get("rejected_candidates", [])),
            ):
                for value in values:
                    plane_mask = np.asarray(value.get("_mask"), dtype=bool)
                    points = world[plane_mask] if plane_mask.shape == plane_valid.shape else np.empty((0, 3))
                    normal = np.asarray(value.get("normal_world", value.get("normal")), dtype=np.float64)
                    if points.size and normal.shape == (3,):
                        normal = normal / np.linalg.norm(normal)
                        offset = -float(np.mean(points @ normal))
                        residual = np.abs(points @ normal + offset)
                        residual_summary = {
                            "rms_mm": float(np.sqrt(np.mean(np.square(residual)))),
                            "p95_mm": float(np.percentile(residual, 95)),
                            "max_mm": float(np.max(residual)),
                        }
                    else:
                        offset, residual_summary = None, {"rms_mm": None, "p95_mm": None, "max_mm": None}
                    plane_items.append({
                        "qualification": qualification,
                        "reason": value.get("reason"),
                        "label": value.get("label"),
                        "plane_id": value.get("plane_id"),
                        "height_mm": value.get("median_height_mm", value.get("height")),
                        "normal": normal.tolist() if normal.shape == (3,) else None,
                        "plane_offset_mm_recomputed": offset,
                        "tilt_deg": value.get("confidence_diagnostics", {}).get("normal_tilt_deg"),
                        "pixel_count": value.get("pixel_count"),
                        "visible_span_px": value.get("confidence_diagnostics", {}).get("visible_span_px"),
                        "component_span_xy_px": value.get("confidence_diagnostics", {}).get("component_span_xy_px"),
                        "residual": residual_summary,
                        "gates": {
                            "max_horizontal_tilt_deg": plane_profile["max_horizontal_tilt_deg"],
                            "min_support_pixels": plane_profile["min_support_pixels"],
                            "min_visible_span_px": plane_profile["min_visible_span_px"],
                            "support_height_range_mm": [plane_profile["min_support_height_mm"], plane_profile["max_support_height_mm"]],
                        },
                    })
            CaptureState.planes.append({
                "frame_id": frame_id,
                "detected_candidate_count": len(plane_items),
                "qualified_support_plane_count": sum(1 for v in plane_extraction.get("planes", []) if v.get("label") == "support_surface"),
                "qualified_plane_count": len(plane_extraction.get("planes", [])),
                "candidates": plane_items,
                "warnings": plane_extraction.get("warnings", []),
            })
            by_id = {str(g.get("logical_object_id")): g for g in logical_groups}
            for obj in frame.get("objects", []):
                if obj.get("geometry_status") != "success":
                    continue
                group = by_id.get(str(obj.get("logical_object_id")))
                if group is None:
                    continue
                inventory, raw_mask = _distribution_summary(
                    obj, group, segment_masks, np.asarray(camera_axial_z_mm), world, self.profile,
                )
                candidate = _candidate_estimate(
                    str(obj.get("class_name")), [float(v) for v in obj.get("bbox_xyxy")], raw_mask,
                    np.asarray(camera_axial_z_mm), world, camera, gravity, config,
                )
                CaptureState.records.append({
                    "sample_frame": frame_id,
                    "logical_object_id": obj.get("logical_object_id"),
                    "baseline": {
                        "support_source": obj.get("diagnostics", {}).get("support_source"),
                        "support_mm": obj.get("geometry", {}).get("support_height_mm"),
                        "top_mm": obj.get("geometry", {}).get("top_height_above_floor_mm"),
                        "object_height_mm": obj.get("geometry", {}).get("object_height_mm"),
                    },
                    "candidate": candidate,
                    "inventory": inventory,
                })
            return frame

    return CapturingProcessor


def _safe_replace_directory(path: Path) -> None:
    resolved = path.resolve()
    root = RESULT_ROOT.resolve()
    if root not in resolved.parents:
        raise RuntimeError(f"refusing to replace path outside candidate results: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def _aggregate(sample_id: str, records: list[dict[str, Any]], baseline_summary: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    logical_to_target: dict[str, str] = {}
    for target, value in baseline_summary["raw"]["targets"].items():
        for logical in value.get("logical_objects", []):
            logical_to_target[str(logical)] = target
    result: dict[str, Any] = {"sample_id": sample_id, "ground_truth_used_for_fit": False, "targets": {}}
    for record in records:
        record["target"] = logical_to_target.get(str(record["logical_object_id"]))
        record["inventory"]["target"] = record["target"]
    usable_by_target: dict[str, list[dict[str, Any]]] = {}
    for target in baseline_summary["raw"]["targets"]:
        selected = [r for r in records if r.get("target") == target]
        usable_by_target[target] = [r["candidate"] for r in selected if r["candidate"].get("available")]
    layer_targets = ["lower_bottle", "lower_volleyball"]
    layer_support = None
    if all(usable_by_target.get(target) for target in layer_targets):
        target_medians = [
            float(np.median([value["support_mm"] for value in usable_by_target[target]]))
            for target in layer_targets
        ]
        layer_support = float(np.median(target_medians))
    for target, baseline in baseline_summary["raw"]["targets"].items():
        selected = [r for r in records if r.get("target") == target]
        usable = usable_by_target[target]
        baseline_support = float(baseline["support_height_mm"]["median"])
        baseline_height = float(baseline["object_height_mm"]["median"])
        baseline_top = baseline_support + baseline_height
        target_truth = truth["targets"][target]
        item: dict[str, Any] = {
            "target": target,
            "frame_count": len(selected),
            "candidate_valid_frame_count": len(usable),
            "baseline": {
                "support_source": baseline["support_source"], "support_mm": baseline_support,
                "top_mm": baseline_top, "object_height_mm": baseline_height,
            },
            "candidate": {"available": bool(usable)},
            "truth": target_truth,
        }
        if usable:
            raw_support = float(np.median([v["support_mm"] for v in usable]))
            raw_height = float(np.median([v["object_height_mm"] for v in usable]))
            raw_top = float(np.median([v["top_mm"] for v in usable]))
            support = layer_support if target in layer_targets and layer_support is not None else raw_support
            top = raw_top if target in layer_targets and layer_support is not None else support + raw_height
            height = top - support
            source_name = (
                "candidate_same_layer_consensus"
                if target in layer_targets and layer_support is not None
                else Counter(v["support_source"] for v in usable).most_common(1)[0][0]
            )
            item["candidate"] = {
                "available": True,
                "support_source": source_name,
                "height_method": Counter(v["height_method"] for v in usable).most_common(1)[0][0],
                "source_counts": dict(Counter(v["source"] for v in usable)),
                "support_mm": support, "top_mm": top, "object_height_mm": height,
                "pre_consensus_support_mm": raw_support,
                "pre_consensus_object_height_mm": raw_height,
                "same_layer_consensus_applied": bool(target in layer_targets and layer_support is not None),
                "support_error_mm": support - float(target_truth["expected_support_mm"]),
                "top_error_mm": top - float(target_truth["diagnostic_expected_top_z_mm"]),
                "object_height_error_mm": height - float(target_truth["expected_object_height_mm"]),
                "object_height_error_percent": 100.0 * (height - float(target_truth["expected_object_height_mm"])) / float(target_truth["expected_object_height_mm"]),
            }
        item["baseline"].update({
            "support_error_mm": baseline_support - float(target_truth["expected_support_mm"]),
            "top_error_mm": baseline_top - float(target_truth["diagnostic_expected_top_z_mm"]),
            "object_height_error_mm": baseline_height - float(target_truth["expected_object_height_mm"]),
            "object_height_error_percent": 100.0 * (baseline_height - float(target_truth["expected_object_height_mm"])) / float(target_truth["expected_object_height_mm"]),
        })
        result["targets"][target] = item
    return result


def replay_sample(sample_id: str) -> dict[str, Any]:
    registry = stage3b.load_registry()
    sample = next((s for s in registry["samples"] if s["sample_id"] == sample_id), None)
    if sample is None:
        raise RuntimeError(f"sample not registered: {sample_id}")
    stage2, stage3a, _ = stage3b.verify_authorities()
    config = read_json(CONFIG_PATH)
    camera = stage2_camera_model(stage2)
    output = RESULT_ROOT / sample_id / "replay_chain"
    _safe_replace_directory(output)
    import deltar_p4_pipeline.all_object_geometry as aog

    original = aog.AllObjectGeometryProcessor
    CaptureState.records = []
    CaptureState.planes = []
    aog.AllObjectGeometryProcessor = _processor_class(original, camera, stage3a, config)
    try:
        stage3b._run_formal_candidate_chain(
            Path(sample["scan_path"]), output, sample_id, stage2, stage3a,
        )
    finally:
        aog.AllObjectGeometryProcessor = original
    baseline_summary = read_json(Path(sample["result_path"]))
    truth = read_json(stage3b.TRUTH_PATH)
    aggregation = _aggregate(sample_id, CaptureState.records, baseline_summary, truth)
    result_dir = RESULT_ROOT / sample_id
    write_json(result_dir / "object_records.compact.json", CaptureState.records)
    write_json(result_dir / "support_plane_ledger.raw.json", CaptureState.planes)
    write_json(result_dir / "baseline_vs_candidate.json", aggregation)
    write_json(result_dir / "replay_manifest.json", {
        "schema_version": "stage3b_geometry_candidate_replay.v1",
        "sample_id": sample_id,
        "scan_id": sample["scan_id"],
        "record_count": len(CaptureState.records),
        "python_executable": sys.executable,
        "config_sha256": sha256(CONFIG_PATH),
        "ground_truth_used_for_fit": False,
        "formal_runtime_modified": False,
        "result": str(result_dir / "baseline_vs_candidate.json"),
    })
    return aggregation


def replay_all() -> dict[str, Any]:
    outputs = {sample: replay_sample(sample) for sample in ("G001", "G002")}
    write_json(RESULT_ROOT / "SUMMARY.candidate.json", {
        "schema_version": "stage3b_geometry_candidate_summary.v1",
        "ground_truth_used_for_fit": False,
        "samples": outputs,
    })
    print_summary(outputs)
    return outputs


def print_summary(outputs: dict[str, Any] | None = None) -> None:
    if outputs is None:
        path = RESULT_ROOT / "SUMMARY.candidate.json"
        if not path.exists():
            print("CANDIDATE_STATUS = NOT_RUN")
            return
        outputs = read_json(path)["samples"]
    for sample_id in ("G001", "G002"):
        print(f"[{sample_id}]")
        for target, item in outputs[sample_id]["targets"].items():
            b, c = item["baseline"], item["candidate"]
            if c.get("available"):
                print(
                    f"{target}: baseline support={b['support_mm']:.1f} height={b['object_height_mm']:.1f}; "
                    f"candidate support={c['support_mm']:.1f} height={c['object_height_mm']:.1f} "
                    f"valid={item['candidate_valid_frame_count']}/{item['frame_count']}"
                )
            else:
                print(f"{target}: candidate unavailable")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("replay-all", help="replay G001 Primary and G002 Robustness")
    one = sub.add_parser("replay", help="replay one registered sample")
    one.add_argument("sample_id", choices=("G001", "G002"))
    sub.add_parser("summary", help="print the latest compact comparison")
    sub.add_parser("status", help="show runner and interpreter status")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "status":
        print(f"PYTHON_EXECUTABLE = {sys.executable}")
        print(f"EXPECTED_PYTHON = {EXPECTED_PYTHON}")
        print(f"EXACT_INTERPRETER = {Path(sys.executable).resolve() == EXPECTED_PYTHON.resolve()}")
        print(f"CONFIG = {CONFIG_PATH}")
        print(f"SUMMARY_EXISTS = {(RESULT_ROOT / 'SUMMARY.candidate.json').exists()}")
        return 0
    if args.command == "summary":
        print_summary()
        return 0
    if Path(sys.executable).resolve() != EXPECTED_PYTHON.resolve():
        raise RuntimeError(f"candidate replay requires exact interpreter: {EXPECTED_PYTHON}")
    if args.command == "replay-all":
        replay_all()
    else:
        replay_sample(args.sample_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
