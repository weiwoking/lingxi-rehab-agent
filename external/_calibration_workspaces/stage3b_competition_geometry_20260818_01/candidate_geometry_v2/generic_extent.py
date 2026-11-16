"""Always-available, class-agnostic coherent vertical envelope."""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np


def estimate_generic_extent(valid_region: np.ndarray, world_z_mm: np.ndarray, support_mm: float, cfg: dict[str, Any]) -> dict[str, Any]:
    valid = np.asarray(valid_region, dtype=bool) & np.isfinite(world_z_mm)
    values = np.asarray(world_z_mm[valid], dtype=np.float64)
    if values.size < int(cfg["minimum_points"]):
        raise ValueError("generic extent has insufficient points")
    k = max(int(cfg["minimum_top_k"]), int(np.ceil(float(cfg["top_k_sqrt_multiplier"]) * np.sqrt(values.size))))
    k = min(k, values.size)
    threshold = float(np.partition(values, values.size - k)[values.size - k])
    floor = float(np.percentile(values, float(cfg["upper_candidate_floor_percentile"])))
    candidate_mask = valid & (world_z_mm >= min(threshold, floor))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate_mask.astype(np.uint8), 8)
    components = []
    for label in range(1, count):
        pixels = int(stats[label, cv2.CC_STAT_AREA])
        if pixels < int(cfg["minimum_component_pixels"]):
            continue
        z = world_z_mm[labels == label]
        components.append({"label": label, "pixels": pixels, "median_mm": float(np.median(z)), "max_mm": float(np.max(z))})
    if components:
        chosen = max(components, key=lambda v: (v["median_mm"], v["pixels"]))
        component_values = world_z_mm[labels == chosen["label"]]
        top = float(np.median(np.sort(component_values)[-min(k, component_values.size):]))
        method = "spatially_coherent_top_k"
    else:
        top = float(np.median(np.sort(values)[-k:]))
        method = "top_k_median_fallback"
    if top <= support_mm:
        # Safe last layer: preserve finite geometry rather than disappear.
        top = float(np.percentile(values, 99))
        method = "p99_positive_extent_fallback"
    height = max(1e-6, top - support_mm)
    confidence = min(0.8, 0.35 + 0.1 * np.log10(max(values.size, 10)) + (0.15 if components else 0.0))
    return {"top_mm": top, "object_height_mm": height, "method": method, "confidence": float(confidence), "point_count": int(values.size), "top_k": int(k), "component_count": len(components), "p95_mm": float(np.percentile(values, 95)), "p99_mm": float(np.percentile(values, 99)), "max_mm": float(np.max(values))}

