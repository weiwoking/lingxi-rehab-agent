from __future__ import annotations

import math
from typing import Any


HORIZONTAL_FOV_DEG = 45.0
VERTICAL_FOV_DEG = 45.0
GRID_SIZE = 8

DISTANCE_BANDS: tuple[tuple[float, float | None, str], ...] = (
    (0.0, 0.75, "0.0-0.75_m"),
    (0.75, 1.0, "0.75-1.0_m"),
    (1.0, 1.25, "1.0-1.25_m"),
    (1.25, 1.5, "1.25-1.5_m"),
    (1.5, 1.75, "1.5-1.75_m"),
    (1.75, 2.0, "1.75-2.0_m"),
    (2.0, 2.25, "2.0-2.25_m"),
    (2.25, 2.5, "2.25-2.5_m"),
    (2.5, 3.0, "2.5-3.0_m"),
    (3.0, 4.0, "3.0-4.0_m"),
    (4.0, None, ">4.0_m"),
)

TABLE_DISTANCES_M = (0.5, 0.8, 1.0, 1.2, 1.3, 1.5, 1.8, 2.0, 2.5, 3.0, 4.0)


def fov_coverage(distance_m: float, horizontal_fov_deg: float = HORIZONTAL_FOV_DEG,
                 vertical_fov_deg: float = VERTICAL_FOV_DEG, grid_size: int = GRID_SIZE) -> dict[str, float]:
    if distance_m <= 0:
        raise ValueError("distance_m must be positive")
    if not (0 < horizontal_fov_deg < 180 and 0 < vertical_fov_deg < 180):
        raise ValueError("FoV angles must be between 0 and 180 degrees")
    if grid_size <= 0:
        raise ValueError("grid_size must be positive")
    width = 2.0 * distance_m * math.tan(math.radians(horizontal_fov_deg) / 2.0)
    height = 2.0 * distance_m * math.tan(math.radians(vertical_fov_deg) / 2.0)
    return {
        "distance_m": float(distance_m),
        "coverage_width_m": width,
        "coverage_height_m": height,
        "zone_width_m": width / grid_size,
        "zone_height_m": height / grid_size,
    }


def distance_band(distance_m: float) -> str:
    if distance_m < 0:
        raise ValueError("distance_m must be non-negative")
    for lower, upper, label in DISTANCE_BANDS:
        if upper is None or lower <= distance_m < upper:
            return label
    raise AssertionError("unreachable")


def coverage_table(distances_m: tuple[float, ...] = TABLE_DISTANCES_M) -> dict[str, Any]:
    return {
        "schema_version": "deltar_tof_coverage_table.v1",
        "sensor": "VL53L5CX",
        "resolution": "8x8",
        "horizontal_fov_deg": HORIZONTAL_FOV_DEG,
        "vertical_fov_deg": VERTICAL_FOV_DEG,
        "diagonal_fov_deg": 65.0,
        "rows": [fov_coverage(value) for value in distances_m],
        "limitations": [
            "Geometric FoV coverage is not a guarantee of valid ranging.",
            "Reliability falls when a target fills only a small part of a zone.",
            "Ambient light, reflectance, cover glass and corner zones reduce usable range.",
            "Camera FoV is not frozen and cannot be inferred from 640x360 resolution.",
        ],
    }


def scan_recommendation() -> dict[str, Any]:
    return {
        "schema_version": "deltar_scan_distance_recommendation.v1",
        "mode": {"resolution": "8x8", "frequency_hz": 15, "ranging_mode": "continuous"},
        "default_scan_distance_m": 1.2,
        "recommended_range_m": [0.8, 1.5],
        "extended_range_m": [0.6, 2.5],
        "small_object_range": [0.8, 1.2],
        "medium_object_range": [1.0, 1.5],
        "large_object_range": [1.2, 1.8],
        "full_person_range": [2.0, 2.5],
        "full_person_policy": {
            "single_frame_is_conditional": True,
            "multi_frame_scan_recommended": True,
            "reason": "A 45 degree vertical FoV needs about 2.05 m to cover 1.70 m geometrically, while low-reflectance or bright-light ToF range may be much shorter.",
        },
        "range_semantics": {
            "recommended": "Normal engineering work area; object-size ranges take precedence.",
            "extended": "Conditional only: require valid status, stable depth, adequate target fill and warnings for reflectance/ambient/corner-zone risk.",
            "theoretical_sensor_range_m": [0.02, 4.0],
            "theoretical_is_not_recommended": True,
        },
        "coverage_at_default": fov_coverage(1.2),
        "why_not_closer": "Below about 0.8 m large objects are more likely to be clipped; below 0.6 m cover-glass crosstalk needs special attention.",
        "why_not_farther": "Each zone grows coarser and ST's 8x8/15 Hz range falls sharply for gray targets, ambient light and corner zones.",
        "exceptions": [
            "Use 0.8-1.2 m for cups, bottles and books so they occupy enough pixels/zones.",
            "Move to 1.2-1.8 m for chairs/tables when complete extent matters.",
            "Use about 2.0 m or a multi-frame scan for a complete adult; do not assume ToF validity from geometry alone.",
        ],
    }
