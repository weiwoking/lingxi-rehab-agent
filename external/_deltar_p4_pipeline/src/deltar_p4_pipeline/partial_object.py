from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "deltar_partial_object_visibility.v1"


def border_flags(bbox_xyxy: list[float] | tuple[float, float, float, float], image_width: int,
                 image_height: int, tolerance_px: float = 1.0) -> dict[str, bool]:
    x1, y1, x2, y2 = (float(value) for value in bbox_xyxy)
    flags = {
        "touches_top": y1 <= tolerance_px,
        "touches_bottom": y2 >= image_height - tolerance_px,
        "touches_left": x1 <= tolerance_px,
        "touches_right": x2 >= image_width - tolerance_px,
    }
    flags["touches_image_border"] = any(flags.values())
    return flags


def border_group(flags: dict[str, bool]) -> str:
    active = [name for name in ("top", "bottom", "left", "right") if flags.get(f"touches_{name}")]
    if not active:
        return "no_border"
    if len(active) > 1:
        return "multiple_borders"
    return f"touch_{active[0]}"


def visibility_contract(*, mask_touches_border: bool, bbox_touches_border: bool, top_visible: bool,
                        bottom_visible: bool, obvious_occlusion: bool, depth_continuous: bool,
                        large_truncation: bool, point_support_top: bool, point_support_bottom: bool,
                        distance_valid: bool, visible_mask_valid: bool, ground_transform_valid: bool,
                        calibration_measured: bool, bottom_occluded_or_truncated: bool = False,
                        occlusion_ratio_estimate: float | None = None,
                        border: dict[str, bool] | None = None) -> dict[str, Any]:
    touches = bool(mask_touches_border or bbox_touches_border)
    complete = all((not touches, top_visible, bottom_visible, not obvious_occlusion, depth_continuous,
                    not large_truncation, point_support_top, point_support_bottom))
    known_partial = any((touches, not top_visible, not bottom_visible, obvious_occlusion, large_truncation))
    status = "complete" if complete else ("partial" if known_partial else "unknown")
    full_extent_visible = complete
    visible_height_valid = bool(visible_mask_valid and ground_transform_valid)
    physical_height_valid = bool(complete and calibration_measured and ground_transform_valid)
    bottom_height_valid = bool(bottom_visible and point_support_bottom and ground_transform_valid and
                               calibration_measured and not bottom_occluded_or_truncated)
    warnings: list[str] = []
    if status != "complete":
        warnings.append("partial_or_unknown_extent")
    if distance_valid and status != "complete":
        warnings.append("distance_is_visible_surface_only")
    if visible_height_valid and not physical_height_valid:
        warnings.append("visible_height_is_not_physical_height")
    if not bottom_height_valid:
        warnings.append("bottom_height_unavailable")
    supplied = border or {}
    result = {
        "schema_version": SCHEMA_VERSION,
        "partial_object": status != "complete",
        "visibility_status": status,
        "touches_image_border": touches,
        "touches_top": bool(supplied.get("touches_top", False)),
        "touches_bottom": bool(supplied.get("touches_bottom", False)),
        "touches_left": bool(supplied.get("touches_left", False)),
        "touches_right": bool(supplied.get("touches_right", False)),
        "top_visible": bool(top_visible),
        "bottom_visible": bool(bottom_visible),
        "full_extent_visible": full_extent_visible,
        "occlusion_ratio_estimate": occlusion_ratio_estimate,
        "distance_valid": bool(distance_valid),
        "distance_semantics": "visible_surface_distance" if distance_valid else None,
        "visible_height_valid": visible_height_valid,
        "physical_height_valid": physical_height_valid,
        "bottom_height_valid": bottom_height_valid,
        "physical_height_m": None,
        "bottom_height_m": None,
        "warnings": warnings,
    }
    return result


def contract_definition() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "visibility_status_values": ["complete", "partial", "unknown"],
        "complete_requires_all": [
            "mask_not_on_any_image_border", "bbox_not_on_any_image_border", "stable_top_mask_support",
            "stable_bottom_mask_support", "no_obvious_occlusion", "continuous_object_depth",
            "no_large_truncation", "sufficient_stage4_top_and_bottom_point_support",
        ],
        "partial_if_any": [
            "touches_image_border", "top_not_visible", "bottom_not_visible", "obvious_occlusion",
            "mask_only_partly_covers_object", "object_outside_camera_fov", "tof_valid_area_only_partly_covers_object",
        ],
        "outputs": {
            "class_name": "Valid YOLO class/region; complete extent is not required.",
            "distance_m": "Allowed for partial objects only as visible_surface_distance when depth/canvas gates pass.",
            "visible_height_m": "Allowed when visible mask and Ground transform are valid; never synonymous with physical height.",
            "physical_height_m": "Only complete + top/bottom/full extent visible + measured calibration; otherwise null.",
            "bottom_height_m": "Only visible/unoccluded bottom + sufficient points + valid Ground transform + measured calibration; otherwise null.",
            "placement_level": "ground|low|tabletop|elevated|unknown; thresholds belong to the Stage 6 profile.",
        },
        "reject_conditions": [
            "invalid_or_unstable_depth", "canvas_rule_failure", "invalid_class_or_region", "invalid_required_calibration",
            "consumer_requests_physical_height_for_non_complete_object",
        ],
        "object_example": {
            "partial_object": True,
            "visibility_status": "partial",
            "touches_image_border": True,
            "touches_top": False,
            "touches_bottom": True,
            "touches_left": False,
            "touches_right": False,
            "top_visible": False,
            "bottom_visible": True,
            "full_extent_visible": False,
            "occlusion_ratio_estimate": None,
            "distance_valid": True,
            "distance_semantics": "visible_surface_distance",
            "visible_height_valid": True,
            "physical_height_valid": False,
            "bottom_height_valid": True,
            "physical_height_m": None,
            "warnings": ["partial_or_unknown_extent", "distance_is_visible_surface_only"],
        },
    }
