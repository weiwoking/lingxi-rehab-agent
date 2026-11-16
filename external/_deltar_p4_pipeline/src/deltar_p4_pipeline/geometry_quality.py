from __future__ import annotations

from typing import Any


def compute_absolute_geometry_valid(
    *,
    calibration_status: str,
    depth_semantics_status: str,
    selected_geometry_status: str,
    canvas_selection_status: str,
    automatic_geometry_valid: bool,
    critical_geometry_rejections: list[str] | tuple[str, ...] | None = None,
) -> bool:
    """Return true only when every absolute-geometry prerequisite is proven."""
    return bool(
        calibration_status == "measured"
        and depth_semantics_status == "PROVEN"
        and selected_geometry_status == "success"
        and canvas_selection_status == "agreed"
        and automatic_geometry_valid is True
        and not critical_geometry_rejections
    )


def experimental_ranking_score(inputs: dict[str, Any]) -> float:
    """Transparent, uncalibrated internal ordering only; never geometry confidence."""
    valid = max(0.0, min(1.0, float(inputs.get("valid_depth_ratio", 0.0))))
    retention = max(0.0, min(1.0, float(inputs.get("point_retention_ratio", 0.0))))
    count_term = min(1.0, float(inputs.get("point_count", 0)) / 1000.0)
    compact = 1.0 / (1.0 + 10.0 * max(0.0, float(inputs.get("depth_mad", 0.0))))
    non_multimodal = 0.0 if inputs.get("multimodal") else 1.0
    return round(0.25 * valid + 0.20 * retention + 0.15 * count_term + 0.25 * compact + 0.15 * non_multimodal, 6)
