from __future__ import annotations

from typing import Any

import numpy as np

from .depth_statistics import compute_statistics
from .errors import PipelineError
from .object_regions import build_regions

STATISTIC_FIELDS = {
    "median": "median", "mad_median": "mad_filtered_median", "trimmed_mean": "trimmed_mean",
    "dominant_peak": "dominant_peak_m", "nearest_stable_surface": "nearest_stable_surface",
}


def evaluate_object_depth(bbox: list[float], depth: np.ndarray, valid_mask: np.ndarray | None = None,
                          segment_mask: np.ndarray | None = None, **profile: Any) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    regions = build_regions(bbox, depth, valid_mask, segment_mask,
                            center_fraction=float(profile.get("center_fraction", 0.5)),
                            erosion_pixels=tuple(profile.get("erosion_pixels", (1, 2, 3))),
                            min_pixels=int(profile.get("min_pixels", 8)),
                            peak_bin_width_m=float(profile.get("peak_bin_width_m", 0.10)),
                            peak_min_support=float(profile.get("peak_min_support", 0.08)))
    results: dict[str, Any] = {}; masks: dict[str, np.ndarray] = {}
    for name, region in regions.items():
        masks[name] = region.pop("mask")
        if region.get("status") == "rejected":
            results[name] = {"region": region, "statistics": {method: {"status": "rejected", **region["error"]} for method in STATISTIC_FIELDS}}
            continue
        try:
            stats = compute_statistics(depth[masks[name]], min_pixels=int(profile.get("min_pixels", 8)),
                                       trim_fraction=float(profile.get("trim_fraction", 0.10)),
                                       peak_bin_width_m=float(profile.get("peak_bin_width_m", 0.10)),
                                       peak_min_support=float(profile.get("peak_min_support", 0.08)))
            candidates = {}
            for method, field in STATISTIC_FIELDS.items():
                candidates[method] = {"status": "valid", "surface_depth_m": float(stats[field]),
                    "pixel_count": region["pixel_count"], "valid_pixel_count": region["valid_pixel_count"],
                    "valid_ratio": region["valid_ratio"], "median_m": stats["median"], "mad_m": stats["mad_m"],
                    "iqr_m": stats["iqr_m"], "dominant_peak_m": stats["dominant_peak_m"],
                    "dominant_peak_support": stats["dominant_peak_support"], "secondary_peak_m": stats["secondary_peak_m"],
                    "peak_separation_m": stats["peak_separation_m"], "multimodal": stats["multimodal"],
                    "geometry_confidence_inputs": {"region_support": region["valid_ratio"],
                        "depth_compactness": float(1.0 / (1.0 + stats["iqr_m"])),
                        "peak_dominance": stats["dominant_peak_support"], "edge_stability": None, "canvas_agreement": None},
                    "warnings": list(stats["warnings"]), "rejection_reason": None}
            results[name] = {"region": region, "statistics": candidates, "distribution": stats}
        except PipelineError as exc:
            results[name] = {"region": region, "statistics": {method: {"status": "rejected", **exc.as_dict()} for method in STATISTIC_FIELDS}}
    return results, masks


def choose_candidates(results: dict[str, Any], frozen_method: dict[str, str]) -> dict[str, Any]:
    """Compatibility entry point backed exclusively by the frozen Stage 3.1 method."""
    from .stage3_1 import frozen_candidate

    return frozen_candidate(results, frozen_method)
