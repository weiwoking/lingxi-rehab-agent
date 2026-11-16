from typing import Any

from .errors import VisionError


SUCCESS_KEYS = {
    "schema_version",
    "capture_id",
    "status",
    "valid",
    "pairing",
    "detections",
    "summary",
    "processing_time_ms",
}
PAIRING_KEYS = {
    "image_timestamp_ms",
    "tof_timestamp_ms",
    "offset_ms",
    "max_offset_ms",
}
SUMMARY_KEYS = {"object_count", "zxl_found", "person_count"}
TIMING_KEYS = {"decode", "yolo", "fusion", "total"}
DETECTION_KEYS = {
    "object_id",
    "class_name",
    "confidence",
    "bbox_px",
    "center_normalized",
    "horizontal_zone",
    "geometry",
}
BBOX_KEYS = {"x1", "y1", "x2", "y2"}
CENTER_KEYS = {"x", "y"}
GEOMETRY_KEYS = {
    "distance_cm",
    "object_height_cm",
    "bottom_height_from_ground_cm",
    "distance_valid",
    "object_height_valid",
    "bottom_height_valid",
    "quality",
    "tof_zone_ids",
}
NO_TOF_GEOMETRY_KEYS = GEOMETRY_KEYS | {"invalid_reason"}
ERROR_KEYS = {"schema_version", "capture_id", "status", "valid", "error"}
ERROR_DETAIL_KEYS = {"code", "message", "retryable"}


def _clean_geometry(geometry: dict[str, Any]) -> dict[str, Any]:
    allowed = NO_TOF_GEOMETRY_KEYS if geometry.get("distance_valid") is False else GEOMETRY_KEYS
    cleaned = {key: geometry.get(key) for key in allowed}
    if geometry.get("distance_valid") is False:
        cleaned["invalid_reason"] = "no_valid_tof_zone"
    return cleaned


def _clean_detection(detection: dict[str, Any]) -> dict[str, Any]:
    bbox = detection.get("bbox_px", {})
    center = detection.get("center_normalized", {})
    return {
        "object_id": detection.get("object_id"),
        "class_name": detection.get("class_name"),
        "confidence": detection.get("confidence"),
        "bbox_px": {key: bbox.get(key) for key in ("x1", "y1", "x2", "y2")},
        "center_normalized": {key: center.get(key) for key in ("x", "y")},
        "horizontal_zone": detection.get("horizontal_zone"),
        "geometry": _clean_geometry(detection.get("geometry", {})),
    }


def build_success_response(
    *,
    capture_id: str,
    image_timestamp_ms: int,
    tof_timestamp_ms: int,
    offset_ms: int,
    max_offset_ms: int,
    detections: list[dict[str, Any]],
    decode_ms: int,
    yolo_ms: int,
    fusion_ms: int,
    total_ms: int,
    person_name: str = "person",
    zxl_name: str = "zxl",
) -> dict[str, Any]:
    cleaned = [_clean_detection(item) for item in detections]
    return {
        "schema_version": "vision_fusion.v1",
        "capture_id": capture_id,
        "status": "ok",
        "valid": True,
        "pairing": {
            "image_timestamp_ms": image_timestamp_ms,
            "tof_timestamp_ms": tof_timestamp_ms,
            "offset_ms": offset_ms,
            "max_offset_ms": max_offset_ms,
        },
        "detections": cleaned,
        "summary": {
            "object_count": len(cleaned),
            "zxl_found": any(item["class_name"] == zxl_name for item in cleaned),
            "person_count": sum(1 for item in cleaned if item["class_name"] == person_name),
        },
        "processing_time_ms": {
            "decode": decode_ms,
            "yolo": yolo_ms,
            "fusion": fusion_ms,
            "total": total_ms,
        },
    }


def build_error_response(error: VisionError) -> dict[str, Any]:
    return {
        "schema_version": "vision_fusion.v1",
        "capture_id": error.capture_id,
        "status": "error",
        "valid": False,
        "error": {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
        },
    }
