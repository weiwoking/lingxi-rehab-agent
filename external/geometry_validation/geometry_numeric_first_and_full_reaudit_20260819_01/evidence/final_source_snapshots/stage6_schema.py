from __future__ import annotations

import json
import math
import re
from typing import Any

from .scan_contracts import ScanContractError, _assert_finite

FRAME_RESULT_SCHEMA = "deltar_scan_frame_result.v1.1"
SCAN_RESULT_SCHEMA = "deltar_scan_result.v1.1"
JOB_REPORT_SCHEMA = "deltar_pc_job_report.v1.1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CODE_RE = re.compile(r"^[EW]_[A-Z0-9_]+$")
ABSOLUTE_WINDOWS_RE = re.compile(r"^[A-Za-z]:[\\/]")
FRAME_DIAGNOSTIC_FALLBACK = "frame diagnostic reported without message"
FRAME_DIAGNOSTIC_CODE = "E_FRAME_DIAGNOSTIC"
FRAME_DIAGNOSTIC_DROPPED_WARNING = "W_FRAME_DIAGNOSTIC_DROPPED"
ERROR_FIELDS = {
    "status", "code", "error_code", "phase", "message", "field", "details"
}


def _fail(code: str, message: str, field: str) -> None:
    raise ScanContractError(code, message, field)


def _object(value: Any, field: str, code: str, *, required: set[str], allowed: set[str] | None = None) -> dict[str, Any]:
    if type(value) is not dict:
        _fail(code, "expected object", field)
    missing = required - set(value)
    if missing:
        _fail(code, f"missing fields: {sorted(missing)}", field)
    accepted = required if allowed is None else allowed
    extra = set(value) - accepted
    if extra:
        _fail(code, f"unknown fields: {sorted(extra)}", field)
    return value


def _list(value: Any, field: str, code: str) -> list[Any]:
    if type(value) is not list:
        _fail(code, "expected array", field)
    return value


def _string(value: Any, field: str, code: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if type(value) is not str or not value or len(value) > 512:
        _fail(code, "expected bounded non-empty string", field)
    return value


def _bool(value: Any, field: str, code: str) -> bool:
    if type(value) is not bool:
        _fail(code, "expected boolean", field)
    return value


def _int(value: Any, field: str, code: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail(code, f"expected integer >= {minimum}", field)
    return value


def _number(value: Any, field: str, code: str, *, nullable: bool = False, minimum: float | None = None) -> float | None:
    if value is None and nullable:
        return None
    if type(value) not in {int, float}:
        _fail(code, "expected finite number", field)
    converted = float(value)
    if minimum is not None and converted < minimum:
        _fail(code, f"number must be >= {minimum}", field)
    return converted


def _enum(value: Any, choices: set[str], field: str, code: str) -> str:
    if value not in choices:
        _fail(code, f"expected one of {sorted(choices)}", field)
    return value


def _sha(value: Any, field: str, code: str) -> None:
    if type(value) is not str or not SHA256_RE.fullmatch(value):
        _fail(code, "expected lowercase SHA-256", field)


def _warnings(values: Any, field: str, code: str) -> None:
    for index, value in enumerate(_list(values, field, code)):
        if type(value) is not str or not CODE_RE.fullmatch(value) or not value.startswith("W_"):
            _fail(code, "invalid warning code", f"{field}[{index}]")


def _diagnostic_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value[:512] if value.strip() else None
    if value is None or isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (bool, int, float)):
        return str(value)[:512]
    if isinstance(value, (list, dict)) and value:
        try:
            text = json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError):
            return None
        return text[:512] if text.strip() else None
    return None


def _safe_diagnostic_value(value: Any) -> bool:
    if isinstance(value, str):
        return len(value) <= 512 and not ABSOLUTE_WINDOWS_RE.match(value)
    if value is None or isinstance(value, (bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return len(value) <= 64 and all(_safe_diagnostic_value(item) for item in value)
    if isinstance(value, dict):
        return (
            len(value) <= 64
            and all(
                isinstance(key, str)
                and len(key) <= 128
                and _safe_diagnostic_value(item)
                for key, item in value.items()
            )
        )
    return False


def _normalize_diagnostic(
    value: Any, location: str
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    item = value if isinstance(value, dict) else {}
    raw_code = item.get("code", item.get("error_code"))
    code = (
        raw_code
        if isinstance(raw_code, str)
        and CODE_RE.fullmatch(raw_code)
        and raw_code.startswith("E_")
        else None
    )
    message = _diagnostic_text(item.get("message"))
    fallback = False
    if code is None and message is None:
        return None, {
            "location": location,
            "action": "dropped",
            "diagnostic_code": None,
            "message_fallback": False,
        }
    if code is None:
        code = FRAME_DIAGNOSTIC_CODE
    if message is None:
        message = FRAME_DIAGNOSTIC_FALLBACK
        fallback = True
    code_key = (
        "error_code"
        if "error_code" in item and "code" not in item
        else "code"
    )
    normalized: dict[str, Any] = {code_key: code, "message": message}
    if item.get("status") == "rejected":
        normalized["status"] = "rejected"
    for key in ("phase", "field"):
        text = item.get(key)
        if (
            isinstance(text, str)
            and text.strip()
            and len(text) <= 512
            and not ABSOLUTE_WINDOWS_RE.match(text)
        ):
            normalized[key] = text
    if "details" in item and _safe_diagnostic_value(item["details"]):
        normalized["details"] = item["details"]
    action = "fallback" if fallback else (
        "normalized" if normalized != item else "preserved"
    )
    return normalized, {
        "location": location,
        "action": action,
        "diagnostic_code": code,
        "message_fallback": fallback,
        "message_before": _diagnostic_text(item.get("message")),
        "message_after": message,
    }


def normalize_frame_diagnostics(value: Any) -> list[dict[str, Any]]:
    """Normalize non-decision diagnostics in place and return a bounded audit."""
    if not isinstance(value, dict):
        return []
    audit: list[dict[str, Any]] = []
    dropped = False
    containers: list[tuple[dict[str, Any], str]] = [(value, "errors")]
    objects = value.get("objects")
    if isinstance(objects, list):
        containers.extend(
            (item, f"objects[{index}].errors")
            for index, item in enumerate(objects)
            if isinstance(item, dict)
        )
    for container, location in containers:
        raw = container.get("errors", [])
        entries = raw if isinstance(raw, list) else []
        if not isinstance(raw, list):
            audit.append({
                "location": location,
                "action": "dropped",
                "diagnostic_code": None,
                "message_fallback": False,
            })
            dropped = True
        normalized_entries: list[dict[str, Any]] = []
        for index, item in enumerate(entries):
            normalized, event = _normalize_diagnostic(
                item, f"{location}[{index}]"
            )
            audit.append(event)
            if normalized is None:
                dropped = True
            else:
                normalized_entries.append(normalized)
        container["errors"] = normalized_entries
    if dropped and isinstance(value.get("warnings"), list):
        value["warnings"] = sorted(
            set(value["warnings"]) | {FRAME_DIAGNOSTIC_DROPPED_WARNING}
        )
    return audit


def _errors(values: Any, field: str, code: str) -> None:
    for index, value in enumerate(_list(values, field, code)):
        if type(value) is not dict: _fail(code, "error must be an object", f"{field}[{index}]")
        extra = set(value) - {"status", "code", "error_code", "phase", "message", "field", "details"}
        if extra: _fail(code, f"unknown error fields: {sorted(extra)}", f"{field}[{index}]")
        error_code = value.get("code", value.get("error_code"))
        if type(error_code) is not str or not CODE_RE.fullmatch(error_code) or not error_code.startswith("E_"):
            _fail(code, "invalid error code", f"{field}[{index}]")
        if "message" in value: _string(value["message"], f"{field}[{index}].message", code)
        if "status" in value and value["status"] != "rejected": _fail(code, "error status must be rejected", f"{field}[{index}].status")


def _no_absolute_paths(value: Any, field: str, code: str) -> None:
    if type(value) is str and ABSOLUTE_WINDOWS_RE.match(value):
        _fail(code, "absolute path is forbidden in public contract", field)
    if isinstance(value, dict):
        for key, item in value.items():
            _no_absolute_paths(item, f"{field}.{key}", code)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _no_absolute_paths(item, f"{field}[{index}]", code)


def _validate_models(models: Any, field: str, code: str) -> None:
    obj = _object(models, field, code, required={"depth", "detect", "segment"})
    for task in ("depth", "detect", "segment"):
        entry = _object(obj[task], f"{field}.{task}", code, required={"task", "sha256"})
        if entry["task"] != task:
            _fail(code, "model task mismatch", f"{field}.{task}.task")
        _sha(entry["sha256"], f"{field}.{task}.sha256", code)


def _validate_hash_map(value: Any, field: str, code: str, keys: set[str]) -> None:
    obj = _object(value, field, code, required=keys)
    for key in keys:
        _sha(obj[key], f"{field}.{key}", code)


def _validate_profiles(value: Any, field: str, code: str) -> None:
    obj = _object(value, field, code, required={"ids", "sha256", "calibration_status"})
    id_keys = {"camera_profile_id", "tof_profile_id", "calibration_profile_id", "algorithm_profile_id", "scan_strategy_profile_id"}
    ids = _object(obj["ids"], f"{field}.ids", code, required=id_keys)
    for key in id_keys:
        _string(ids[key], f"{field}.ids.{key}", code)
    hash_keys = {"algorithm_profile.json", "formal_stage2_frame_profile.json", "model_manifest.json",
                 "scan_strategy_profile.json"}
    _validate_hash_map(obj["sha256"], f"{field}.sha256", code, hash_keys)
    _enum(obj["calibration_status"], {"measured", "unverified", "synthetic"}, f"{field}.calibration_status", code)


def _validate_instance(value: Any, field: str, code: str, *, segment: bool) -> None:
    keys = {"instance_id", "class_id", "class_name", "confidence", "bbox_xyxy", "bbox_normalized", "mask_key"}
    obj = _object(value, field, code, required=keys)
    _int(obj["instance_id"], f"{field}.instance_id", code); _int(obj["class_id"], f"{field}.class_id", code)
    _string(obj["class_name"], f"{field}.class_name", code)
    confidence = _number(obj["confidence"], f"{field}.confidence", code, minimum=0.0)
    if confidence is not None and confidence > 1.0: _fail(code, "confidence must be <= 1", f"{field}.confidence")
    for bbox_name in ("bbox_xyxy", "bbox_normalized"):
        bbox = _list(obj[bbox_name], f"{field}.{bbox_name}", code)
        if len(bbox) != 4: _fail(code, "bbox must contain four numbers", f"{field}.{bbox_name}")
        for index, item in enumerate(bbox): _number(item, f"{field}.{bbox_name}[{index}]", code)
    if segment: _string(obj["mask_key"], f"{field}.mask_key", code)
    elif obj["mask_key"] is not None: _fail(code, "detect mask_key must be null", f"{field}.mask_key")


def _validate_region(value: Any, field: str, code: str) -> None:
    if type(value) is not dict: _fail(code, "region audit must be object", field)
    source = value.get("source")
    if source == "detection":
        obj = _object(value, field, code, required={"object_id", "source", "region_method", "statistic_method"})
        if obj["region_method"] != "bbox_full" or obj["statistic_method"] != "median":
            _fail(code, "detection frozen method mismatch", field)
        return
    keys = {"object_id", "source", "region_method", "statistic_method", "raw_mask_pixel_count",
            "frozen_region_pixel_count", "frozen_region_sha256", "formal_region_sha256",
            "stage3_stage4_region_exact_equal", "stage3_valid_region_pixel_count"}
    obj = _object(value, field, code, required=keys)
    if source != "segmentation" or obj["region_method"] != "segment_eroded_3px" or obj["statistic_method"] != "mad_median":
        _fail(code, "segmentation frozen method mismatch", field)
    for key in ("raw_mask_pixel_count", "frozen_region_pixel_count"): _int(obj[key], f"{field}.{key}", code)
    for key in ("frozen_region_sha256", "formal_region_sha256"): _sha(obj[key], f"{field}.{key}", code)
    if not _bool(obj["stage3_stage4_region_exact_equal"], f"{field}.stage3_stage4_region_exact_equal", code):
        _fail(code, "Stage 3.1/4.1 regions differ", field)
    if obj["frozen_region_sha256"] != obj["formal_region_sha256"]: _fail(code, "region hashes differ", field)
    counts = _object(obj["stage3_valid_region_pixel_count"], f"{field}.stage3_valid_region_pixel_count", code,
                     required={"direct_640x360", "letterbox_640x480"})
    for canvas in counts: _int(counts[canvas], f"{field}.stage3_valid_region_pixel_count.{canvas}", code)


def _validate_height(value: Any, field: str, code: str) -> None:
    obj = _object(value, field, code, required={"value_m", "available", "absolute_valid"})
    _number(obj["value_m"], f"{field}.value_m", code, nullable=True, minimum=0.0)
    available = _bool(obj["available"], f"{field}.available", code)
    absolute = _bool(obj["absolute_valid"], f"{field}.absolute_valid", code)
    if absolute and (not available or obj["value_m"] is None):
        _fail(code, "absolute-valid height must be available and non-null", field)


def _validate_visibility(value: Any, field: str, code: str, *, frame: bool) -> None:
    if not frame:
        obj = _object(value, field, code, required={"status", "top_visible", "bottom_visible", "full_extent_visible"})
        _enum(obj["status"], {"complete", "partial", "unknown"}, f"{field}.status", code)
        for key in ("top_visible", "bottom_visible", "full_extent_visible"):
            _bool(obj[key], f"{field}.{key}", code)
        return
    keys = {"schema_version", "partial_object", "visibility_status", "touches_image_border", "touches_top", "touches_bottom",
            "touches_left", "touches_right", "top_visible", "bottom_visible", "full_extent_visible", "occlusion_ratio_estimate",
            "distance_valid", "distance_semantics", "visible_height_valid", "physical_height_valid", "bottom_height_valid",
            "physical_height_m", "bottom_height_m", "warnings"}
    obj = _object(value, field, code, required=keys)
    if obj["schema_version"] != "deltar_partial_object_visibility.v1":
        _fail(code, "invalid visibility schema", f"{field}.schema_version")
    _enum(obj["visibility_status"], {"complete", "partial", "unknown"}, f"{field}.visibility_status", code)
    for key in keys - {"schema_version", "visibility_status", "occlusion_ratio_estimate", "distance_semantics", "physical_height_m", "bottom_height_m", "warnings"}:
        _bool(obj[key], f"{field}.{key}", code)
    _number(obj["occlusion_ratio_estimate"], f"{field}.occlusion_ratio_estimate", code, nullable=True, minimum=0.0)
    _string(obj["distance_semantics"], f"{field}.distance_semantics", code, nullable=True)
    _number(obj["physical_height_m"], f"{field}.physical_height_m", code, nullable=True, minimum=0.0)
    _number(obj["bottom_height_m"], f"{field}.bottom_height_m", code, nullable=True)
    if type(obj["warnings"]) is not list:
        _fail(code, "visibility warnings must be array", f"{field}.warnings")


def _validate_stage4_provenance(value: Any, field: str, code: str) -> None:
    provenance = _object(value, field, code,
                         required={"frame_id", "stage3_schema", "stage4_group_schema", "stage4_function", "segmentation_object_id", "detection_object_id", "model_hashes", "gt_used_for_inference"})
    _string(provenance["frame_id"], f"{field}.frame_id", code)
    if provenance["stage3_schema"] != "deltar_stage3_1_object_depth.v1" or provenance["stage4_group_schema"] != "deltar_stage4_object_group_input.v1.1" or provenance["stage4_function"] != "all_object_geometry.measure_logical_object_v2":
        _fail(code, "formal pipeline provenance mismatch", field)
    if _bool(provenance["gt_used_for_inference"], f"{field}.gt_used_for_inference", code):
        _fail(code, "GT provenance must be false", f"{field}.gt_used_for_inference")
    _validate_hash_map(provenance["model_hashes"], f"{field}.model_hashes", code, {"depth", "detect", "segment"})


def _validate_formal_geometry(value: Any, field: str, code: str) -> None:
    keys = {"schema_version", "status", "canvas_selection_status", "selected_canvas", "selection_is_provisional",
            "single_canvas_only", "automatic_geometry_valid", "absolute_geometry_valid", "selected_source", "fallback_used",
            "fallback_reason", "critical_geometry_rejections", "attempts", "selected_geometry", "provenance"}
    obj = _object(value, field, code, required=keys)
    if obj["schema_version"] != "deltar_stage4_geometry.v1.1":
        _fail(code, "invalid formal geometry schema", f"{field}.schema_version")
    _enum(obj["status"], {"success", "rejected"}, f"{field}.status", code)
    selection = _enum(obj["canvas_selection_status"], {"agreed", "disagreed", "direct_only", "letterbox_only", "both_rejected"}, f"{field}.canvas_selection_status", code)
    _string(obj["selected_canvas"], f"{field}.selected_canvas", code, nullable=True)
    for key in ("selection_is_provisional", "single_canvas_only", "automatic_geometry_valid", "absolute_geometry_valid", "fallback_used"):
        _bool(obj[key], f"{field}.{key}", code)
    _string(obj["selected_source"], f"{field}.selected_source", code, nullable=True)
    _string(obj["fallback_reason"], f"{field}.fallback_reason", code, nullable=True)
    for index, rejection in enumerate(_list(obj["critical_geometry_rejections"], f"{field}.critical_geometry_rejections", code)):
        _string(str(rejection), f"{field}.critical_geometry_rejections[{index}]", code)
    for index, attempt in enumerate(_list(obj["attempts"], f"{field}.attempts", code)):
        attempt_obj = _object(attempt, f"{field}.attempts[{index}]", code,
                              required={"source", "success", "canvas_selection_status", "selected_canvas", "single_canvas_only", "automatic_geometry_valid", "critical_geometry_rejections"})
        _string(attempt_obj["source"], f"{field}.attempts[{index}].source", code)
        _bool(attempt_obj["success"], f"{field}.attempts[{index}].success", code)
    if obj["selected_geometry"] is not None:
        selected = _object(obj["selected_geometry"], f"{field}.selected_geometry", code,
                           required={"distance_m", "object_height_m", "bottom_height_m", "point_count", "mask_support"})
        for key in ("distance_m", "object_height_m"):
            _number(selected[key], f"{field}.selected_geometry.{key}", code, minimum=0.0)
        _number(selected["bottom_height_m"], f"{field}.selected_geometry.bottom_height_m", code)
        _int(selected["point_count"], f"{field}.selected_geometry.point_count", code)
        _int(selected["mask_support"], f"{field}.selected_geometry.mask_support", code)
    if selection in {"disagreed", "both_rejected"} and obj["selected_geometry"] is not None:
        _fail(code, "disagreed/rejected canvas cannot select geometry", f"{field}.selected_geometry")
    if obj["absolute_geometry_valid"] and not (selection == "agreed" and obj["automatic_geometry_valid"] and obj["selected_geometry"] is not None and not obj["critical_geometry_rejections"]):
        _fail(code, "absolute geometry prerequisites are not met", f"{field}.absolute_geometry_valid")
    _validate_stage4_provenance(obj["provenance"], f"{field}.provenance", code)


def _validate_frame_object(value: Any, field: str, code: str) -> None:
    keys = {"frame_object_id", "logical_object_id", "class_id", "class_name", "class_scope", "object_role", "object_source",
            "confidence", "bbox_xyxy", "distance_m", "distance_semantics", "centroid_3d_m", "geometry_point_count",
            "depth_compactness", "formal_geometry", "visibility", "visible_height", "physical_height", "bottom_height",
            "placement_level", "warnings", "errors", "rejection_reason", "provenance"}
    obj = _object(value, field, code, required=keys)
    _string(obj["frame_object_id"], f"{field}.frame_object_id", code); _string(obj["logical_object_id"], f"{field}.logical_object_id", code)
    _int(obj["class_id"], f"{field}.class_id", code); _string(obj["class_name"], f"{field}.class_name", code)
    _enum(obj["class_scope"], {"core_now", "supported_but_not_core", "future_custom_class"}, f"{field}.class_scope", code)
    _number(obj["confidence"], f"{field}.confidence", code, minimum=0.0)
    bbox = _list(obj["bbox_xyxy"], f"{field}.bbox_xyxy", code)
    if len(bbox) != 4:
        _fail(code, "bbox must contain four numbers", f"{field}.bbox_xyxy")
    _number(obj["distance_m"], f"{field}.distance_m", code, nullable=True, minimum=0.0)
    _string(obj["distance_semantics"], f"{field}.distance_semantics", code, nullable=True)
    _int(obj["geometry_point_count"], f"{field}.geometry_point_count", code)
    _validate_formal_geometry(obj["formal_geometry"], f"{field}.formal_geometry", code)
    _validate_visibility(obj["visibility"], f"{field}.visibility", code, frame=True)
    for name in ("visible_height", "physical_height", "bottom_height"):
        _validate_height(obj[name], f"{field}.{name}", code)
    absolute = obj["formal_geometry"]["absolute_geometry_valid"]
    if obj["visible_height"]["absolute_valid"] != bool(absolute and obj["visible_height"]["available"]):
        _fail(code, "visible height absolute validity differs from formal Stage 4.1", f"{field}.visible_height.absolute_valid")
    if (obj["physical_height"]["absolute_valid"] or obj["bottom_height"]["absolute_valid"]) and not absolute:
        _fail(code, "derived absolute height requires formal Stage 4.1 absolute validity", field)
    _enum(obj["placement_level"], {"ground", "low", "tabletop", "elevated", "unknown"}, f"{field}.placement_level", code)
    _warnings(obj["warnings"], f"{field}.warnings", code); _errors(obj["errors"], f"{field}.errors", code)
    _string(obj["rejection_reason"], f"{field}.rejection_reason", code, nullable=True)
    provenance = _object(obj["provenance"], f"{field}.provenance", code, required={"frame_id", "model_hashes", "stage4_function"})
    for task in ("depth", "detect", "segment"):
        _sha(provenance["model_hashes"].get(task), f"{field}.provenance.model_hashes.{task}", code)


def validate_frame_result_v1_1(value: dict[str, Any]) -> None:
    code = "E_FRAME_RESULT_SCHEMA"
    normalize_frame_diagnostics(value)
    keys = {"schema_version", "status", "frame_id", "sequence", "timestamps", "decoded_image", "tof_summary", "models", "profiles",
            "deltar_repeatability", "detections", "logical_object_groups", "region_validation", "objects", "warnings", "rejections",
            "errors", "gt_used_for_inference", "processing_ms"}
    obj = _object(value, "$", code, required=keys, allowed=keys | {"processing_status"})
    if obj["schema_version"] != FRAME_RESULT_SCHEMA:
        _fail(code, f"expected {FRAME_RESULT_SCHEMA}", "schema_version")
    _enum(obj["status"], {"success", "rejected"}, "status", code); _string(obj["frame_id"], "frame_id", code); _int(obj["sequence"], "sequence", code)
    timestamps = _object(obj["timestamps"], "timestamps", code, required={"camera_timestamp_us", "tof_timestamp_us", "pair_delta_us"})
    for key in timestamps: _int(timestamps[key], f"timestamps.{key}", code)
    image = _object(obj["decoded_image"], "decoded_image", code, required={"width", "height", "pixel_format", "payload_bytes", "payload_sha256"})
    if (image["width"], image["height"], image["pixel_format"], image["payload_bytes"]) != (640, 360, "RGB565_LE", 460800):
        _fail(code, "decoded image contract mismatch", "decoded_image")
    _sha(image["payload_sha256"], "decoded_image.payload_sha256", code)
    tof = _object(obj["tof_summary"], "tof_summary", code, required={"valid_zone_count", "median_distance_mm"})
    if not 0 <= _int(tof["valid_zone_count"], "tof_summary.valid_zone_count", code) <= 64: _fail(code, "valid_zone_count > 64", "tof_summary.valid_zone_count")
    _number(tof["median_distance_mm"], "tof_summary.median_distance_mm", code, nullable=True, minimum=0.0)
    _validate_models(obj["models"], "models", code); _validate_profiles(obj["profiles"], "profiles", code)
    detections = _object(obj["detections"], "detections", code, required={"detect_count", "segment_count", "matched_count", "detect_instances", "segment_instances"})
    for key in ("detect_count", "segment_count", "matched_count"): _int(detections[key], f"detections.{key}", code)
    for index, instance in enumerate(_list(detections["detect_instances"], "detections.detect_instances", code)):
        _validate_instance(instance, f"detections.detect_instances[{index}]", code, segment=False)
    for index, instance in enumerate(_list(detections["segment_instances"], "detections.segment_instances", code)):
        _validate_instance(instance, f"detections.segment_instances[{index}]", code, segment=True)
    if detections["detect_count"] != len(detections["detect_instances"]) or detections["segment_count"] != len(detections["segment_instances"]):
        _fail(code, "detection counts differ from arrays", "detections")
    groups = _list(obj["logical_object_groups"], "logical_object_groups", code)
    logical_ids = []
    for index, group in enumerate(groups):
        group_obj = _object(group, f"logical_object_groups[{index}]", code,
                            required={"logical_object_id", "class_id", "class_name", "segmentation_primary", "detection_fallback", "match", "provenance"})
        logical_ids.append(group_obj["logical_object_id"])
        for kind in ("segmentation_primary", "detection_fallback"):
            candidate = group_obj[kind]
            required = {"available", "segment_instance_id" if kind == "segmentation_primary" else "detect_instance_id", "class_id", "class_name", "confidence", "bbox_xyxy", "mask_key", "region_method", "statistic_method", "model_sha256"}
            candidate_obj = _object(candidate, f"logical_object_groups[{index}].{kind}", code, required=required)
            available = _bool(candidate_obj["available"], f"logical_object_groups[{index}].{kind}.available", code)
            if available:
                _sha(candidate["model_sha256"], f"logical_object_groups[{index}].{kind}.model_sha256", code)
                _int(candidate["class_id"], f"logical_object_groups[{index}].{kind}.class_id", code)
                _string(candidate["class_name"], f"logical_object_groups[{index}].{kind}.class_name", code)
                expected_region, expected_stat = (("segment_eroded_3px", "mad_median") if kind == "segmentation_primary" else ("bbox_full", "median"))
                if candidate["region_method"] != expected_region or candidate["statistic_method"] != expected_stat:
                    _fail(code, "candidate frozen method mismatch", f"logical_object_groups[{index}].{kind}")
            elif any(candidate[key] is not None for key in ("class_id", "class_name", "confidence", "model_sha256")) or candidate["bbox_xyxy"]:
                _fail(code, "unavailable candidate must be empty", f"logical_object_groups[{index}].{kind}")
        match = _object(group_obj["match"], f"logical_object_groups[{index}].match", code, required={"status", "iou"})
        _enum(match["status"], {"matched", "unmatched_segmentation", "unmatched_detection"}, f"logical_object_groups[{index}].match.status", code)
        _number(match["iou"], f"logical_object_groups[{index}].match.iou", code, nullable=True, minimum=0.0)
        _validate_stage4_provenance(group_obj["provenance"], f"logical_object_groups[{index}].provenance", code)
    if len(logical_ids) != len(set(logical_ids)):
        _fail(code, "duplicate logical_object_id", "logical_object_groups")
    for index, item in enumerate(_list(obj["objects"], "objects", code)):
        _validate_frame_object(item, f"objects[{index}]", code)
    for index, item in enumerate(_list(obj["region_validation"], "region_validation", code)):
        _validate_region(item, f"region_validation[{index}]", code)
    _warnings(obj["warnings"], "warnings", code); _errors(obj["errors"], "errors", code)
    if "processing_status" in obj:
        status = _object(
            obj["processing_status"], "processing_status", code,
            required={"capture_status", "tof_status", "deltar_status", "detection_status",
                      "geometry_status", "degradation_mode", "reason_codes", "tof_diagnostics"},
        )
        _enum(status["capture_status"], {"CAPTURE_OK", "CAPTURE_FAILURE"}, "processing_status.capture_status", code)
        _enum(status["tof_status"], {"TOF_NORMAL", "TOF_DEGRADED", "TOF_DEGRADED_RAW", "TOF_BORROWED", "TOF_UNAVAILABLE"}, "processing_status.tof_status", code)
        _enum(status["deltar_status"], {"DELTAR_OK", "DELTAR_UNAVAILABLE"}, "processing_status.deltar_status", code)
        _enum(status["detection_status"], {"DETECTION_OK", "DETECTION_UNAVAILABLE"}, "processing_status.detection_status", code)
        _enum(status["geometry_status"], {"GEOMETRY_OK", "GEOMETRY_DEGRADED", "GEOMETRY_UNAVAILABLE"}, "processing_status.geometry_status", code)
        _string(status["degradation_mode"], "processing_status.degradation_mode", code)
        for index, reason in enumerate(_list(status["reason_codes"], "processing_status.reason_codes", code)):
            _string(reason, f"processing_status.reason_codes[{index}]", code)
        if type(status["tof_diagnostics"]) is not dict:
            _fail(code, "expected object", "processing_status.tof_diagnostics")
    _bool(obj["gt_used_for_inference"], "gt_used_for_inference", code)
    _assert_finite(obj, code=code); _no_absolute_paths(obj, "$", code)


def _validate_scan_object(value: Any, field: str, code: str) -> None:
    keys = {"scan_object_id", "class_id", "class_name", "class_scope", "object_role", "observation_count", "frame_ids",
            "best_observation_frame_id", "best_observation_score", "best_observation_reason", "best_observation",
            "distance_observations_m", "visibility_observations", "visibility", "distance", "visible_height", "physical_height",
            "bottom_height", "placement_level", "warnings", "rejection_reason", "aggregation_status", "provenance"}
    obj = _object(value, field, code, required=keys)
    _string(obj["scan_object_id"], f"{field}.scan_object_id", code); _int(obj["class_id"], f"{field}.class_id", code)
    _string(obj["class_name"], f"{field}.class_name", code); _int(obj["observation_count"], f"{field}.observation_count", code, minimum=1)
    frame_ids = _list(obj["frame_ids"], f"{field}.frame_ids", code)
    if len(frame_ids) != len(set(frame_ids)) or len(frame_ids) != obj["observation_count"]:
        _fail(code, "frame_ids must be unique and match observation_count", f"{field}.frame_ids")
    _validate_visibility(obj["visibility"], f"{field}.visibility", code, frame=False)
    distance = _object(obj["distance"], f"{field}.distance", code, required={"value_m", "semantics", "valid", "source_frame_id"})
    _bool(distance["valid"], f"{field}.distance.valid", code); _number(distance["value_m"], f"{field}.distance.value_m", code, nullable=True, minimum=0.0)
    for name in ("visible_height", "physical_height", "bottom_height"): _validate_height(obj[name], f"{field}.{name}", code)
    best = _object(obj["best_observation"], f"{field}.best_observation", code,
                   required={"frame_id", "frame_object_id", "selected_source", "canvas_selection_status", "formal_stage4_selected"})
    if not _bool(best["formal_stage4_selected"], f"{field}.best_observation.formal_stage4_selected", code):
        _fail(code, "best observation must have formal Stage 4 geometry", f"{field}.best_observation")
    provenance = _object(obj["provenance"], f"{field}.provenance", code,
                         required={"source_frame_object_ids", "best_observation_only_for_geometry", "model_hashes", "stage4_function"})
    for task in ("depth", "detect", "segment"): _sha(provenance["model_hashes"].get(task), f"{field}.provenance.model_hashes.{task}", code)
    _warnings(obj["warnings"], f"{field}.warnings", code)


def validate_scan_result_v1_1(value: dict[str, Any]) -> None:
    code = "E_SCAN_RESULT_SCHEMA"
    keys = {"schema_version", "status", "scan_id", "job_id", "input", "models", "profiles", "objects", "warnings", "errors", "aggregation", "provenance", "processing"}
    obj = _object(value, "$", code, required=keys, allowed=keys | {"frame_processing_status"})
    if obj["schema_version"] != SCAN_RESULT_SCHEMA: _fail("E_RESULT_SCHEMA_VERSION", f"expected {SCAN_RESULT_SCHEMA}", "schema_version")
    _enum(obj["status"], {"success", "partial_success", "rejected", "failed"}, "status", code)
    _string(obj["scan_id"], "scan_id", code); _string(obj["job_id"], "job_id", code)
    input_obj = _object(obj["input"], "input", code, required={"frame_count", "valid_frame_count", "rejected_frame_count", "rejected_frames"})
    for key in ("frame_count", "valid_frame_count", "rejected_frame_count"): _int(input_obj[key], f"input.{key}", code)
    _validate_models(obj["models"], "models", code); _validate_profiles(obj["profiles"], "profiles", code)
    scan_ids = []
    for index, item in enumerate(_list(obj["objects"], "objects", code)):
        _validate_scan_object(item, f"objects[{index}]", code); scan_ids.append(item["scan_object_id"])
    if len(scan_ids) != len(set(scan_ids)): _fail(code, "duplicate scan_object_id", "objects")
    _warnings(obj["warnings"], "warnings", code); _errors(obj["errors"], "errors", code)
    prov = obj["provenance"]
    prov = _object(prov, "provenance", code, required={"pipeline_version", "package", "models", "profiles", "upstream_commits", "runtime", "gt_used_for_inference", "local_debug_paths_recorded"})
    _string(prov["pipeline_version"], "provenance.pipeline_version", code)
    package = _object(prov["package"], "provenance.package", code, required={"files", "sha256"})
    if type(package["files"]) is not dict or not package["files"]: _fail(code, "package files must be a non-empty object", "provenance.package.files")
    for name, digest in package["files"].items(): _sha(digest, f"provenance.package.files.{name}", code)
    _sha(package["sha256"], "provenance.package.sha256", code)
    models = _object(prov["models"], "provenance.models", code, required={"depth", "detect", "segment"})
    for task in ("depth", "detect", "segment"):
        model = _object(models[task], f"provenance.models.{task}", code, required={"sha256", "upstream_commit"})
        _sha(model["sha256"], f"provenance.models.{task}.sha256", code)
        _string(model["upstream_commit"], f"provenance.models.{task}.upstream_commit", code, nullable=True)
    _validate_profiles(prov["profiles"], "provenance.profiles", code)
    commits = _object(prov["upstream_commits"], "provenance.upstream_commits", code, required={"esp_claw", "deltar", "calib_deltar"})
    for name, digest in commits.items():
        if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{40}", digest): _fail(code, "expected Git commit", f"provenance.upstream_commits.{name}")
    runtime = _object(prov["runtime"], "provenance.runtime", code, required={"python", "platform", "pytorch", "compute", "ultralytics"})
    for name, item in runtime.items(): _string(item, f"provenance.runtime.{name}", code)
    if _bool(prov["gt_used_for_inference"], "provenance.gt_used_for_inference", code): _fail(code, "GT must not be used", "provenance.gt_used_for_inference")
    if _bool(prov["local_debug_paths_recorded"], "provenance.local_debug_paths_recorded", code): _fail(code, "debug paths must not enter result", "provenance.local_debug_paths_recorded")
    aggregation = _object(obj["aggregation"], "aggregation", code, required={"input_observation_count", "association_group_count", "output_object_count", "filtered_non_core_count", "gt_used_for_association"})
    for name in ("input_observation_count", "association_group_count", "output_object_count", "filtered_non_core_count"): _int(aggregation[name], f"aggregation.{name}", code)
    if _bool(aggregation["gt_used_for_association"], "aggregation.gt_used_for_association", code): _fail(code, "GT must not be used for association", "aggregation.gt_used_for_association")
    processing = _object(obj["processing"], "processing", code, required={"frame_processing_ms", "aggregation_ms", "total_ms", "started_at", "finished_at", "frames_processed_in_sequence", "dense_depth_retained"})
    if type(processing["frame_processing_ms"]) is not dict: _fail(code, "expected frame timing map", "processing.frame_processing_ms")
    _number(processing["aggregation_ms"], "processing.aggregation_ms", code, minimum=0.0); _number(processing["total_ms"], "processing.total_ms", code, minimum=0.0)
    _string(processing["started_at"], "processing.started_at", code); _string(processing["finished_at"], "processing.finished_at", code)
    if not _bool(processing["frames_processed_in_sequence"], "processing.frames_processed_in_sequence", code): _fail(code, "frames must be processed in sequence", "processing.frames_processed_in_sequence")
    if _bool(processing["dense_depth_retained"], "processing.dense_depth_retained", code): _fail(code, "dense depth must not be retained", "processing.dense_depth_retained")
    if "frame_processing_status" in obj:
        statuses = _list(obj["frame_processing_status"], "frame_processing_status", code)
        if len(statuses) != input_obj["frame_count"]:
            _fail(code, "frame status count must match input frame_count", "frame_processing_status")
        for index, raw in enumerate(statuses):
            field = f"frame_processing_status[{index}]"
            status = _object(raw, field, code, required={"frame_id", "sequence", "capture_status", "tof_status", "deltar_status", "detection_status", "geometry_status", "degradation_mode", "reason_codes", "tof_diagnostics"})
            if status["sequence"] != index or status["frame_id"] != f"frame_{index:06d}":
                _fail(code, "frame statuses must be ordered and canonical", field)
            _enum(status["capture_status"], {"CAPTURE_OK", "CAPTURE_FAILURE"}, f"{field}.capture_status", code)
            _enum(status["tof_status"], {"TOF_NORMAL", "TOF_DEGRADED", "TOF_DEGRADED_RAW", "TOF_BORROWED", "TOF_UNAVAILABLE"}, f"{field}.tof_status", code)
            _enum(status["deltar_status"], {"DELTAR_OK", "DELTAR_UNAVAILABLE"}, f"{field}.deltar_status", code)
            _enum(status["detection_status"], {"DETECTION_OK", "DETECTION_UNAVAILABLE"}, f"{field}.detection_status", code)
            _enum(status["geometry_status"], {"GEOMETRY_OK", "GEOMETRY_DEGRADED", "GEOMETRY_UNAVAILABLE"}, f"{field}.geometry_status", code)
            _string(status["degradation_mode"], f"{field}.degradation_mode", code)
            for reason_index, reason in enumerate(_list(status["reason_codes"], f"{field}.reason_codes", code)):
                _string(reason, f"{field}.reason_codes[{reason_index}]", code)
            if type(status["tof_diagnostics"]) is not dict:
                _fail(code, "expected object", f"{field}.tof_diagnostics")
    _assert_finite(obj, code=code); _no_absolute_paths(obj, "$", code)


def validate_job_report_v1_1(value: dict[str, Any]) -> None:
    code = "E_JOB_REPORT_SCHEMA"
    keys = {"schema_version", "job_id", "scan_id", "status", "exit_code", "started_at", "finished_at", "result_status",
            "input", "output", "processed_frames", "rejected_frames", "warnings", "errors", "provenance", "processing",
            "resume_supported", "persistent_queue"}
    obj = _object(value, "$", code, required=keys)
    if obj["schema_version"] != JOB_REPORT_SCHEMA: _fail(code, f"expected {JOB_REPORT_SCHEMA}", "schema_version")
    _string(obj["job_id"], "job_id", code); _string(obj["scan_id"], "scan_id", code)
    _enum(obj["status"], {"validated", "running", "success", "partial_success", "rejected", "failed"}, "status", code)
    exit_code = _int(obj["exit_code"], "exit_code", code)
    if exit_code not in {0, 2, 3, 4, 5, 6, 7}: _fail(code, "invalid exit code", "exit_code")
    _string(obj["started_at"], "started_at", code); _string(obj["finished_at"], "finished_at", code)
    _string(obj["result_status"], "result_status", code, nullable=True)
    input_obj = _object(obj["input"], "input", code, required={"frame_count"}); _int(input_obj["frame_count"], "input.frame_count", code, minimum=1)
    output = _object(obj["output"], "output", code, required={"result_schema", "frame_result_schema", "result_written"})
    if output["result_schema"] != SCAN_RESULT_SCHEMA or output["frame_result_schema"] != FRAME_RESULT_SCHEMA: _fail(code, "output schema mismatch", "output")
    _bool(output["result_written"], "output.result_written", code)
    processed = _list(obj["processed_frames"], "processed_frames", code)
    if len(processed) != len(set(processed)): _fail(code, "processed frame IDs must be unique", "processed_frames")
    for index, frame_id in enumerate(processed): _string(frame_id, f"processed_frames[{index}]", code)
    for index, rejected in enumerate(_list(obj["rejected_frames"], "rejected_frames", code)):
        rejected_obj = _object(rejected, f"rejected_frames[{index}]", code, required={"frame_id", "sequence", "error"})
        _string(rejected_obj["frame_id"], f"rejected_frames[{index}].frame_id", code); _int(rejected_obj["sequence"], f"rejected_frames[{index}].sequence", code)
        _errors([rejected_obj["error"]], f"rejected_frames[{index}].error", code)
    _warnings(obj["warnings"], "warnings", code); _errors(obj["errors"], "errors", code)
    prov = _object(obj["provenance"], "provenance", code, required={"pipeline_version", "model_hashes", "profile_hashes"})
    for task in ("depth", "detect", "segment"): _sha(prov["model_hashes"].get(task), f"provenance.model_hashes.{task}", code)
    if type(prov["profile_hashes"]) is not dict or not prov["profile_hashes"]: _fail(code, "profile_hashes must be non-empty", "provenance.profile_hashes")
    for name, digest in prov["profile_hashes"].items(): _sha(digest, f"provenance.profile_hashes.{name}", code)
    processing = _object(obj["processing"], "processing", code, required={"total_ms"}); _number(processing["total_ms"], "processing.total_ms", code, minimum=0.0)
    _bool(obj["resume_supported"], "resume_supported", code); _bool(obj["persistent_queue"], "persistent_queue", code)
    _assert_finite(obj, code=code); _no_absolute_paths(obj, "$", code)
