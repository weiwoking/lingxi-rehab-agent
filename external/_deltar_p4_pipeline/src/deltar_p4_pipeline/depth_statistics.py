from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .errors import ErrorCode, PipelineError


@dataclass(frozen=True)
class Peak:
    center_m: float
    count: int
    support: float
    low_m: float
    high_m: float


def _values(values: np.ndarray, min_pixels: int = 8) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64).reshape(-1)
    arr = arr[np.isfinite(arr) & (arr > 0)]
    if arr.size == 0:
        raise PipelineError(ErrorCode.DEPTH_REGION_NO_VALID_PIXELS, "depth region has no valid pixels", "depth")
    if arr.size < min_pixels:
        raise PipelineError(ErrorCode.DEPTH_REGION_TOO_FEW_PIXELS, f"depth region has fewer than {min_pixels} valid pixels", "depth")
    return arr


def find_peaks(values: np.ndarray, *, bin_width_m: float = 0.10, min_support: float = 0.08, min_pixels: int = 8) -> list[Peak]:
    arr = _values(values, min_pixels)
    width = max(0.02, float(bin_width_m))
    lo = np.floor(arr.min() / width) * width
    hi = np.ceil(arr.max() / width) * width + width
    edges = np.arange(lo, hi + width * 0.5, width)
    if edges.size < 2:
        edges = np.array([lo, lo + width])
    counts, edges = np.histogram(arr, bins=edges)
    peaks: list[Peak] = []
    for i, count in enumerate(counts):
        left = counts[i - 1] if i else -1
        right = counts[i + 1] if i + 1 < counts.size else -1
        support = float(count / arr.size)
        if count > 0 and count >= left and count >= right and support >= min_support and count >= min_pixels:
            in_bin = arr[(arr >= edges[i]) & (arr < edges[i + 1] if i + 1 < len(edges) - 1 else arr <= edges[i + 1])]
            peaks.append(Peak(float(np.median(in_bin)), int(count), support, float(edges[i]), float(edges[i + 1])))
    return sorted(peaks, key=lambda peak: (-peak.count, peak.center_m))


def peak_summary(values: np.ndarray, *, bin_width_m: float = 0.10, min_support: float = 0.08, min_pixels: int = 8) -> dict[str, Any]:
    peaks = find_peaks(values, bin_width_m=bin_width_m, min_support=min_support, min_pixels=min_pixels)
    if not peaks:
        raise PipelineError(ErrorCode.DEPTH_CLUSTER_NO_STABLE_PEAK, "no stable depth peak", "depth")
    primary = peaks[0]
    secondary = peaks[1] if len(peaks) > 1 else None
    separation = abs(primary.center_m - secondary.center_m) if secondary else None
    return {"dominant_peak_m": primary.center_m, "dominant_peak_count": primary.count,
            "dominant_peak_support": primary.support, "dominant_peak_range_m": [primary.low_m, primary.high_m],
            "secondary_peak_m": secondary.center_m if secondary else None,
            "secondary_peak_count": secondary.count if secondary else 0,
            "peak_separation_m": separation, "multimodal": bool(secondary and separation >= bin_width_m * 1.5),
            "peaks": [peak.__dict__ for peak in peaks]}


def select_peak_mask(depth: np.ndarray, region: np.ndarray, *, nearest: bool = False, bin_width_m: float = 0.10,
                     min_support: float = 0.08, min_pixels: int = 8) -> tuple[np.ndarray, dict[str, Any]]:
    values = np.asarray(depth)[region]
    peaks = find_peaks(values, bin_width_m=bin_width_m, min_support=min_support, min_pixels=min_pixels)
    if not peaks:
        raise PipelineError(ErrorCode.DEPTH_CLUSTER_NO_STABLE_PEAK, "no stable depth peak", "depth")
    peak = min(peaks, key=lambda item: item.center_m) if nearest else peaks[0]
    selected = region & np.isfinite(depth) & (depth > 0) & (depth >= peak.low_m) & (depth <= peak.high_m)
    return selected, {"selected_peak_m": peak.center_m, "selected_peak_support": peak.support,
                      "selected_peak_count": peak.count, "selection": "nearest_stable" if nearest else "dominant"}


def compute_statistics(values: np.ndarray, *, min_pixels: int = 8, trim_fraction: float = 0.10,
                       mad_sigma: float = 3.0, peak_bin_width_m: float = 0.10,
                       peak_min_support: float = 0.08) -> dict[str, Any]:
    arr = _values(values, min_pixels)
    median = float(np.median(arr)); absolute = np.abs(arr - median); mad = float(np.median(absolute))
    robust_sigma = 1.4826 * mad
    mad_values = arr[absolute <= mad_sigma * robust_sigma] if mad > 0 else arr[arr == median]
    if mad_values.size < min_pixels:
        mad_values = arr
        mad_warning = ["MAD_FILTER_FALLBACK_UNFILTERED"]
    else:
        mad_warning = []
    trim = int(np.floor(arr.size * trim_fraction))
    ordered = np.sort(arr)
    trimmed = ordered[trim:arr.size - trim] if trim > 0 and arr.size - 2 * trim >= min_pixels else ordered
    peak = peak_summary(arr, bin_width_m=peak_bin_width_m, min_support=peak_min_support, min_pixels=min_pixels)
    nearest = min((p for p in peak["peaks"] if p["support"] >= peak_min_support and p["count"] >= min_pixels), key=lambda p: p["center_m"])
    p10, p25, p50, p75, p90 = np.percentile(arr, [10, 25, 50, 75, 90])
    return {"median": median, "mad_filtered_median": float(np.median(mad_values)), "mad_m": mad,
            "robust_sigma_m": robust_sigma, "mad_zero_behavior": "exact-median-inliers" if mad == 0 else "3-robust-sigma",
            "trimmed_mean": float(np.mean(trimmed)), "trim_fraction_each_tail": trim_fraction,
            "nearest_stable_surface": float(nearest["center_m"]), "nearest_stable_reason": "nearest peak meeting support and pixel thresholds",
            "p10": float(p10), "p25": float(p25), "p50": float(p50), "p75": float(p75), "p90": float(p90),
            "iqr_m": float(p75 - p25), **peak, "warnings": mad_warning}
