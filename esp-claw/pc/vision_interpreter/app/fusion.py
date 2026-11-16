from statistics import median
from typing import Any

from .geometry import estimate_height
from .schemas import TofFrame


def _zone_index(
    row: int,
    col: int,
    rows: int,
    cols: int,
    flip_h: bool,
    flip_v: bool,
) -> int:
    source_row = rows - 1 - row if flip_v else row
    source_col = cols - 1 - col if flip_h else col
    return source_row * cols + source_col


def _covered_zones(
    bbox: dict[str, int],
    width: int,
    height: int,
    rows: int,
    cols: int,
    flip_h: bool,
    flip_v: bool,
) -> list[int]:
    x1 = max(0.0, min(1.0, bbox["x1"] / width))
    y1 = max(0.0, min(1.0, bbox["y1"] / height))
    x2 = max(0.0, min(1.0, bbox["x2"] / width))
    y2 = max(0.0, min(1.0, bbox["y2"] / height))

    zones: list[int] = []
    for row in range(rows):
        cy = (row + 0.5) / rows
        for col in range(cols):
            cx = (col + 0.5) / cols
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                zones.append(_zone_index(row, col, rows, cols, flip_h, flip_v))

    if not zones:
        center_x = (x1 + x2) / 2.0
        center_y = (y1 + y2) / 2.0
        col = min(cols - 1, max(0, int(center_x * cols)))
        row = min(rows - 1, max(0, int(center_y * rows)))
        zones.append(_zone_index(row, col, rows, cols, flip_h, flip_v))

    return sorted(set(zones))


def _remove_outliers(values: list[int], scale: float) -> list[int]:
    if len(values) < 3:
        return values
    med = median(values)
    mad = median([abs(v - med) for v in values])
    if mad == 0:
        return values
    threshold = scale * 1.4826 * mad
    kept = [v for v in values if abs(v - med) <= threshold]
    return kept or values


def _horizontal_zone(center_x: float, left: float, right: float) -> str:
    if center_x < left:
        return "left"
    if center_x > right:
        return "right"
    return "center"


def interpret(
    detections: list[dict[str, Any]],
    tof: TofFrame,
    config: dict[str, Any],
    pair_offset_ms: int,
) -> list[dict[str, Any]]:
    image_cfg = config["image"]
    tof_cfg = config["tof"]
    fusion_cfg = config["fusion"]

    width = int(image_cfg["width"])
    height = int(image_cfg["height"])
    rows = int(tof_cfg["rows"])
    cols = int(tof_cfg["cols"])

    invalid_status = set(int(v) for v in tof_cfg.get("invalid_status_codes", [255]))
    min_mm = int(tof_cfg.get("min_distance_mm", 100))
    max_mm = int(tof_cfg.get("max_distance_mm", 4000))
    min_zones = int(fusion_cfg.get("minimum_valid_zones", 1))

    output: list[dict[str, Any]] = []

    for object_id, detection in enumerate(detections, start=1):
        bbox = detection["bbox_px"]
        center_x = ((bbox["x1"] + bbox["x2"]) / 2.0) / width
        center_y = ((bbox["y1"] + bbox["y2"]) / 2.0) / height

        candidate_ids = _covered_zones(
            bbox,
            width,
            height,
            rows,
            cols,
            bool(tof_cfg.get("flip_horizontal", True)),
            bool(tof_cfg.get("flip_vertical", True)),
        )

        pairs: list[tuple[int, int]] = []
        for zone_id in candidate_ids:
            distance = int(tof.distance_mm[zone_id])
            status = int(tof.status_code[zone_id])
            if status in invalid_status:
                continue
            if min_mm <= distance <= max_mm:
                pairs.append((zone_id, distance))

        distances = _remove_outliers(
            [distance for _, distance in pairs],
            float(fusion_cfg.get("outlier_mad_scale", 3.0)),
        )

        remaining = list(distances)
        zone_ids: list[int] = []
        for zone_id, distance in pairs:
            if distance in remaining:
                zone_ids.append(zone_id)
                remaining.remove(distance)

        if len(distances) >= min_zones:
            distance_cm = round(float(median(distances)) / 10.0, 2)
            height_result = estimate_height(bbox, distance_cm, config)
            pairing_quality = max(
                0.0,
                1.0 - pair_offset_ms / max(1, int(tof_cfg["max_pair_offset_ms"])),
            )
            zone_quality = min(1.0, len(distances) / max(1, len(candidate_ids)))
            quality = round(
                min(
                    1.0,
                    0.5 * zone_quality
                    + 0.3 * pairing_quality
                    + 0.2 * float(detection["confidence"]),
                ),
                3,
            )
            geometry = {
                "distance_cm": distance_cm,
                "distance_valid": True,
                "quality": quality,
                "tof_zone_ids": zone_ids,
                **height_result,
            }
            geometry["invalid_reason"] = (
                None
                if height_result["object_height_valid"]
                else height_result["height_invalid_reason"]
            )
        else:
            geometry = {
                "distance_cm": None,
                "object_height_cm": None,
                "bottom_height_from_ground_cm": None,
                "distance_valid": False,
                "object_height_valid": False,
                "bottom_height_valid": False,
                "camera_depth_cm": None,
                "quality": 0.0,
                "tof_zone_ids": [],
                "geometry_method": "opencv_pinhole_backprojection",
                "height_invalid_reason": "distance_unavailable",
                "invalid_reason": "no_valid_tof_zone",
            }

        output.append(
            {
                "object_id": object_id,
                "class_name": detection["class_name"],
                "confidence": round(float(detection["confidence"]), 4),
                "bbox_px": bbox,
                "center_normalized": {
                    "x": round(center_x, 4),
                    "y": round(center_y, 4),
                },
                "horizontal_zone": _horizontal_zone(
                    center_x,
                    float(fusion_cfg.get("left_boundary", 0.35)),
                    float(fusion_cfg.get("right_boundary", 0.65)),
                ),
                "geometry": geometry,
            }
        )
    return output
