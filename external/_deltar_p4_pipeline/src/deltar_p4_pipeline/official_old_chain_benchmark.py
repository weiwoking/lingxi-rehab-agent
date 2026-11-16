from __future__ import annotations

from typing import Any

import numpy as np

from .official_zone_mapping import hardcoded_flip_index, zones_for_bbox


def _mad(values: np.ndarray, scale: float = 3.0) -> tuple[np.ndarray, float]:
    if values.size < 3:
        return values, float(np.median(np.abs(values - np.median(values)))) if values.size else 0.0
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad == 0:
        return values, mad
    kept = values[np.abs(values - median) <= scale * 1.4826 * mad]
    return (kept if kept.size else values), mad


def estimate_old_distance(
    bbox: list[float], hist_data: np.ndarray, fr: np.ndarray, mask: np.ndarray, *, legacy_flip: bool = False
) -> dict[str, Any]:
    canonical = zones_for_bbox(bbox, fr)
    source_indices = [hardcoded_flip_index(index) for index in canonical] if legacy_flip else list(canonical)
    hist = np.asarray(hist_data, dtype=np.float64)
    valid_mask = np.asarray(mask, dtype=bool)
    accepted: list[float] = []
    rejected: list[dict[str, Any]] = []
    for index in source_indices:
        mu, sigma = hist[index]
        reasons = []
        if not valid_mask[index]:
            reasons.append("official_mask_false")
        if not np.isfinite(mu) or not 0.1 <= mu <= 4.0:
            reasons.append("range_filter")
        if not np.isfinite(sigma) or sigma <= 0:
            reasons.append("sigma_nonpositive")
        if reasons:
            rejected.append({"zone_index": int(index), "reasons": reasons})
        else:
            accepted.append(float(mu))
    before_mad = np.asarray(accepted, dtype=np.float64)
    after_mad, mad = _mad(before_mad)
    base = {
        "used_zone_indices": [int(index) for index in source_indices],
        "canonical_zone_indices": [int(index) for index in canonical],
        "raw_zone_count": len(source_indices),
        "valid_zone_count_before_mad": int(before_mad.size),
        "valid_zone_count": int(after_mad.size),
        "mad_m": mad,
        "sigma_status_filtering": "official mask true; mu 0.1..4.0m; sigma finite and >0",
        "filtered_zones": rejected,
        "orientation": "legacy_hardcoded_flip_horizontal_and_vertical" if legacy_flip else "official_identity_fr",
        "gt_used": False,
        "segment_used": False,
    }
    if after_mad.size == 0:
        return {**base, "status": "rejected", "old_distance_m": None, "rejection_reason": "no_valid_tof_zone", "warnings": []}
    return {
        **base,
        "status": "valid",
        "old_distance_m": float(np.median(after_mad)),
        "rejection_reason": None,
        "warnings": [],
    }


def raw_zone_ablations(bbox: list[float], hist_data: np.ndarray, fr: np.ndarray, mask: np.ndarray) -> dict[str, float | None]:
    indices = zones_for_bbox(bbox, fr)
    values = np.asarray(hist_data, dtype=np.float64)[indices, 0]
    good = np.asarray(mask, dtype=bool)[indices] & np.isfinite(values) & (values >= 0.1) & (values <= 4.0)
    values = values[good]
    if not values.size:
        return {"nearest_m": None, "median_m": None, "dominant_m": None}
    rounded = np.round(values / 0.10).astype(int)
    unique, counts = np.unique(rounded, return_counts=True)
    dominant_bin = unique[int(np.argmax(counts))]
    dominant = values[np.abs(values / 0.10 - dominant_bin) <= 0.5]
    return {"nearest_m": float(values.min()), "median_m": float(np.median(values)), "dominant_m": float(np.median(dominant))}
