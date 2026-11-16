"""Mask-first, gravity-constrained cup/bottle extent estimator."""
from __future__ import annotations

from typing import Any

import numpy as np

from .upright_slender import estimate_upright_slender


def _width_profile(mask: np.ndarray) -> dict[str, Any]:
    source = np.asarray(mask, dtype=bool)
    rows = np.flatnonzero(np.any(source, axis=1))
    if rows.size < 3:
        return {"available": False, "reason": "insufficient_silhouette_rows"}
    widths = np.asarray([np.count_nonzero(source[row]) for row in rows], dtype=np.float64)
    median = float(np.median(widths))
    return {
        "available": True,
        "visible_row_count": int(rows.size),
        "median_width_px": median,
        "width_iqr_fraction": float((np.percentile(widths, 75) - np.percentile(widths, 25)) / max(median, 1.0)),
        "nonempty_row_fraction": float(rows.size / max(1, rows[-1] - rows[0] + 1)),
    }


def estimate_upright_container(
    mask: np.ndarray,
    support_mm: float,
    camera: dict[str, Any],
    gravity: dict[str, Any],
    axial_mm: np.ndarray,
    selected_valid: np.ndarray,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Estimate extent only; environment support is read-only input."""
    profile = _width_profile(mask)
    if not profile.get("available"):
        return {"available": False, "reason": profile["reason"], "width_profile": profile}
    if profile["nonempty_row_fraction"] < float(cfg["minimum_nonempty_row_fraction"]):
        return {"available": False, "reason": "fragmented_container_silhouette", "width_profile": profile}
    if profile["width_iqr_fraction"] > float(cfg["maximum_width_iqr_fraction"]):
        return {"available": False, "reason": "inconsistent_container_width_profile", "width_profile": profile}

    silhouette_cfg = {
        key: cfg[key]
        for key in (
            "base_anchor_edge_trim_fraction", "minimum_anchor_count", "maximum_anchor_count",
            "height_min_mm", "height_max_mm", "height_step_mm", "mask_hole_tolerance_px",
            "maximum_candidate_iqr_mm",
        )
    }
    estimate = estimate_upright_slender(mask, support_mm, camera, gravity, silhouette_cfg)
    if not estimate.get("available"):
        return {**estimate, "width_profile": profile}

    depth = np.asarray(axial_mm)[np.asarray(selected_valid, dtype=bool)]
    depth = depth[np.isfinite(depth) & (depth > 0.0)]
    depth_iqr = None if not depth.size else float(np.percentile(depth, 75) - np.percentile(depth, 25))
    depth_reliable = bool(depth_iqr is not None and depth.size >= int(cfg["minimum_depth_points"]) and depth_iqr <= float(cfg["maximum_depth_iqr_mm"]))
    # Transparent observations remain valid as silhouette estimates; their
    # contaminated depth is explicitly rejected instead of silently fused.
    confidence = float(estimate["confidence"])
    confidence = min(0.93, confidence + (0.04 if depth_reliable else -0.05))
    return {
        **estimate,
        "method": "upright_container_gravity_silhouette",
        "confidence": max(0.0, confidence),
        "support_authority": "read_only_environment_support",
        "width_profile": profile,
        "depth_gate": {
            "accepted": depth_reliable,
            "point_count": int(depth.size),
            "iqr_mm": depth_iqr,
            "reason": None if depth_reliable else "background_or_transparency_depth_rejected",
        },
    }
