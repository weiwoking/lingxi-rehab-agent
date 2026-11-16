from __future__ import annotations

from typing import Any

import numpy as np

from .calibration import transform_points
from .camera_geometry import backproject_pixels
from .errors import ErrorCode, PipelineError

FILTERS = {"region_all_valid", "stage3_surface_window", "robust_mad_gate", "dominant_peak_gate", "intersection"}


def _depth_in_meters(depth_source: np.ndarray, calibration: dict[str, Any]) -> np.ndarray:
    try:
        raw_depth = np.asarray(depth_source, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise PipelineError(ErrorCode.DEPTH_SCALE, "raw depth must be numeric", "depth_source", str(exc)) from exc
    scale = calibration.get("depth", {}).get("scale_to_meter")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not np.isfinite(scale) or scale <= 0:
        raise PipelineError(ErrorCode.DEPTH_SCALE, "scale_to_meter must be finite and positive", "depth.scale_to_meter", scale)
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        depth_m = raw_depth * float(scale)
    finite_raw = np.isfinite(raw_depth)
    if np.any(finite_raw & ~np.isfinite(depth_m)):
        raise PipelineError(ErrorCode.DEPTH_SCALE, "scale_to_meter overflows finite raw depth", "depth.scale_to_meter", scale)
    if np.any(finite_raw & (raw_depth != 0.0) & (depth_m == 0.0)):
        raise PipelineError(ErrorCode.DEPTH_SCALE, "scale_to_meter underflows nonzero raw depth", "depth.scale_to_meter", scale)
    return depth_m


def bbox_mask(shape: tuple[int, int], bbox_xyxy: list[float]) -> np.ndarray:
    height, width = shape
    if len(bbox_xyxy) != 4:
        raise PipelineError(ErrorCode.STAGE4_INPUT_SCHEMA, "bbox_xyxy must have four values", "bbox_xyxy")
    x1, y1, x2, y2 = bbox_xyxy
    xa, xb = max(0, int(np.floor(x1))), min(width, int(np.ceil(x2)))
    ya, yb = max(0, int(np.floor(y1))), min(height, int(np.ceil(y2)))
    mask = np.zeros(shape, dtype=bool)
    if xb > xa and yb > ya:
        mask[ya:yb, xa:xb] = True
    return mask


def erode_mask(mask: np.ndarray, pixels: int) -> np.ndarray:
    mask = np.asarray(mask, dtype=bool)
    if pixels <= 0:
        return mask.copy()
    try:
        import cv2

        kernel = np.ones((2 * pixels + 1, 2 * pixels + 1), np.uint8)
        return cv2.erode(mask.astype(np.uint8), kernel, iterations=1, borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool)
    except ImportError:  # pragma: no cover
        out = mask.copy()
        for _ in range(pixels):
            padded = np.pad(out, 1, constant_values=False)
            out = np.logical_and.reduce([padded[dy:dy + out.shape[0], dx:dx + out.shape[1]] for dy in range(3) for dx in range(3)])
        return out


def _peak_gate(values: np.ndarray, bin_width: float, minimum_support: float) -> tuple[np.ndarray, dict[str, Any]]:
    low, high = float(values.min()), float(values.max())
    if high - low < bin_width:
        return np.ones(values.size, dtype=bool), {"multimodal": False, "dominant_peak_support": 1.0, "peak_count": 1}
    edges = np.arange(low, high + bin_width * 1.01, bin_width)
    counts, edges = np.histogram(values, bins=edges)
    order = np.argsort(counts)[::-1]
    best = int(order[0])
    support = float(counts[best] / values.size)
    significant = [int(i) for i in order if counts[i] / values.size >= minimum_support]
    multimodal = len(significant) > 1 and any(abs(int(i) - best) > 1 for i in significant[1:])
    lo = edges[max(0, best - 1)]
    hi = edges[min(len(edges) - 1, best + 2)]
    return (values >= lo) & (values <= hi), {
        "multimodal": bool(multimodal),
        "dominant_peak_support": support,
        "peak_count": len(significant),
        "dominant_peak_range_m": [float(lo), float(hi)],
    }


def build_point_cloud(
    depth_source: np.ndarray,
    region_mask: np.ndarray,
    calibration: dict[str, Any],
    profile: dict[str, Any],
    *,
    stage3_surface_depth_m: float | None = None,
    source_type: str = "segmentation",
    point_filter: str | None = None,
) -> dict[str, Any]:
    depth = _depth_in_meters(depth_source, calibration)
    if depth.shape != (360, 640):
        raise PipelineError(ErrorCode.STAGE4_DEPTH_SHAPE, "source depth must have shape [360,640]", "depth_source", list(depth.shape))
    region = np.asarray(region_mask, dtype=bool)
    if region.shape != depth.shape:
        raise PipelineError(ErrorCode.STAGE4_MASK_SHAPE, "mask must match source depth", "region_mask", list(region.shape))
    initial = int(region.sum())
    if initial == 0:
        raise PipelineError(ErrorCode.GEOMETRY_NO_POINTS, "object region is empty")
    valid = region & np.isfinite(depth) & (depth > float(profile["minimum_depth_m"])) & (depth < float(profile["maximum_depth_m"]))
    valid_count = int(valid.sum())
    if valid_count == 0:
        raise PipelineError(ErrorCode.GEOMETRY_NO_POINTS, "object region has no valid depth")
    values = depth[valid]
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    q25, q75 = np.quantile(values, [0.25, 0.75])
    iqr = float(q75 - q25)
    robust_half_width = max(float(profile["mad_multiplier"]) * 1.4826 * mad, float(profile["iqr_multiplier"]) * iqr, float(profile["minimum_gate_m"]))
    surface_center = median if stage3_surface_depth_m is None else float(stage3_surface_depth_m)
    stage3_half_width = max(robust_half_width, float(profile["stage3_surface_minimum_window_m"]))
    mad_gate = np.abs(values - median) <= robust_half_width
    surface_gate = np.abs(values - surface_center) <= stage3_half_width
    peak_gate, peak_info = _peak_gate(values, float(profile["peak_bin_width_m"]), float(profile["secondary_peak_min_support"]))
    selected_filter = point_filter or str(profile["point_filter"])
    if selected_filter not in FILTERS:
        raise PipelineError(ErrorCode.STAGE4_INPUT_SCHEMA, "unknown point filter", "point_filter", selected_filter)
    gates = {
        "region_all_valid": np.ones(values.size, dtype=bool),
        "stage3_surface_window": surface_gate,
        "robust_mad_gate": mad_gate,
        "dominant_peak_gate": peak_gate,
        "intersection": surface_gate & mad_gate,
    }
    keep = gates[selected_filter]
    if source_type.endswith("detection_fallback") and peak_info["multimodal"]:
        if peak_info["dominant_peak_support"] < float(profile["bbox_peak_min_support"]):
            raise PipelineError(ErrorCode.GEOMETRY_MULTIMODAL, "bbox depth has no stable dominant surface", details=peak_info)
        keep &= peak_gate
    kept_values = values[keep]
    min_points = int(profile["minimum_point_count"])
    if kept_values.size < min_points:
        raise PipelineError(ErrorCode.GEOMETRY_TOO_FEW_POINTS, "too few points after filtering", details={"kept": int(kept_values.size), "minimum": min_points})
    pixel_vu = np.argwhere(valid)[keep]
    pixel_uv = pixel_vu[:, [1, 0]].astype(np.float64)
    points_camera = backproject_pixels(pixel_uv, kept_values, calibration["camera_model"], calibration["depth"]["semantics"])
    points_ground = transform_points(points_camera, calibration["transforms"]["T_ground_from_camera"])
    warnings: list[str] = []
    if source_type.endswith("detection_fallback"):
        warnings.append("W_DETECTION_BBOX_BACKGROUND_CONTAMINATION")
    border = bool(region[0].any() or region[-1].any() or region[:, 0].any() or region[:, -1].any())
    if border:
        warnings.append("W_OBJECT_TOUCHES_IMAGE_BORDER")
    metrics = {
        "initial_pixel_count": initial,
        "valid_depth_count": valid_count,
        "valid_depth_ratio": valid_count / initial,
        "point_count": int(kept_values.size),
        "point_retention_ratio": float(kept_values.size / valid_count),
        "depth_min_m": float(kept_values.min()),
        "depth_max_m": float(kept_values.max()),
        "depth_mad_m": mad,
        "depth_iqr_m": iqr,
        "gate_half_width_m": robust_half_width,
        "filter_method": selected_filter,
        "stage3_surface_depth_m": stage3_surface_depth_m,
        "touches_image_border": border,
        **peak_info,
    }
    return {
        "points_camera": points_camera,
        "points_ground": points_ground,
        "pixel_uv": pixel_uv,
        "depth_values_m": kept_values,
        "depth_unit": "meter",
        "filter_metrics": metrics,
        "warnings": warnings,
    }
