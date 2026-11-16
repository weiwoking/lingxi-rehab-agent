from __future__ import annotations

from typing import Any

import numpy as np

from .depth_statistics import peak_summary
from .errors import ErrorCode, PipelineError
from .object_regions import binary_erosion, boundary_band


def depth_metrics(predicted: np.ndarray, gt: np.ndarray, region: np.ndarray) -> dict[str, Any]:
    if predicted.shape != gt.shape or predicted.shape != region.shape:
        raise PipelineError(ErrorCode.GT_SHAPE, "predicted, GT, and region shapes must match", "gt")
    gt_valid = np.isfinite(gt) & (gt > 0) & region
    overlap = gt_valid & np.isfinite(predicted) & (predicted > 0)
    count = int(gt_valid.sum()); overlap_count = int(overlap.sum())
    if overlap_count == 0:
        return {"status": "rejected", "error_code": str(ErrorCode.DEPTH_REGION_NO_VALID_PIXELS),
                "gt_valid_pixel_count": count, "valid_overlap": 0.0}
    error = np.abs(predicted[overlap].astype(np.float64) - gt[overlap].astype(np.float64))
    return {"status": "valid", "mae_m": float(error.mean()), "median_absolute_error_m": float(np.median(error)),
            "rmse_m": float(np.sqrt(np.mean(error ** 2))), "p90_absolute_error_m": float(np.percentile(error, 90)),
            "p95_absolute_error_m": float(np.percentile(error, 95)), "valid_overlap": float(overlap_count / max(1, count)),
            "gt_valid_pixel_count": count, "overlap_pixel_count": overlap_count}


def gt_references(gt: np.ndarray, mask: np.ndarray, erosion_pixels: int = 2) -> dict[str, float | None]:
    valid = np.isfinite(gt) & (gt > 0)
    raw = gt[mask & valid]
    eroded = gt[binary_erosion(mask, erosion_pixels) & valid]
    if raw.size == 0:
        raise PipelineError(ErrorCode.DEPTH_REGION_NO_VALID_PIXELS, "GT region has no valid pixels", "gt")
    try:
        dominant = peak_summary(raw, min_pixels=min(8, max(1, raw.size)))["dominant_peak_m"]
    except PipelineError:
        dominant = float(np.median(raw))
    return {"gt_mask_median_m": float(np.median(raw)), "gt_eroded_mask_median_m": float(np.median(eroded)) if eroded.size else None,
            "gt_dominant_peak_m": float(dominant)}


def boundary_metrics(depth: np.ndarray, gt: np.ndarray, mask: np.ndarray, pixels: int = 2) -> dict[str, Any]:
    band = boundary_band(mask, pixels); interior = binary_erosion(mask, pixels)
    valid = np.isfinite(depth) & (depth > 0)
    gt_valid = np.isfinite(gt) & (gt > 0)
    def median(arr: np.ndarray, selected: np.ndarray) -> float | None:
        values = arr[selected & np.isfinite(arr) & (arr > 0)]
        return float(np.median(values)) if values.size else None
    b, i = median(depth, band & valid), median(depth, interior & valid)
    gb, gi = median(gt, band & gt_valid), median(gt, interior & gt_valid)
    return {"boundary_median_m": b, "interior_median_m": i, "boundary_interior_delta_m": abs(b - i) if b is not None and i is not None else None,
            "gt_boundary_median_m": gb, "gt_interior_median_m": gi, "gt_boundary_interior_delta_m": abs(gb - gi) if gb is not None and gi is not None else None}
