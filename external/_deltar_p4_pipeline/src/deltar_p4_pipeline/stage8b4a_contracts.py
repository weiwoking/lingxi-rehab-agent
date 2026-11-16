from __future__ import annotations

import math
from typing import Any

from .scan_contracts import ScanContractError


P4_PACKAGE_FILENAME = "stage8b4a_p4_decision_package.v1.json"
DIAGNOSTICS_FILENAME = "stage8b_dual_gate_diagnostics.v1.json"
WHITELIST_PROFILE_FILENAME = "stage8b_p4_object_whitelist.json"


P4_PACKAGE_SCHEMA = "stage8b4a_p4_decision_package.v1"
WHITELIST_CLASSES = ("cup", "bottle", "book", "sports ball")
SUPPORTED_SELECTION_POLICIES = frozenset(
    {
        "class_geometry_fallback_then_p4_whitelist_v1",
        "one_best_per_class_v1",
    }
)
MAX_PACKAGE_BYTES = 64 * 1024
IMAGE_WIDTH = 640
IMAGE_HEIGHT = 360


def _fail(message: str, field: str, details: Any = None) -> None:
    raise ScanContractError("E_STAGE8B4A_CONTRACT", message, field, details)


def _string(value: Any, field: str, maximum: int = 512) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        _fail(f"expected non-empty string up to {maximum} characters", field)
    return value


def _number(
    value: Any,
    field: str,
    *,
    nullable: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("expected finite number", field)
    checked = float(value)
    if not math.isfinite(checked):
        _fail("expected finite number", field)
    if minimum is not None and checked < minimum:
        _fail(f"must be >= {minimum}", field)
    if maximum is not None and checked > maximum:
        _fail(f"must be <= {maximum}", field)
    return checked


def _exact_keys(value: Any, expected: set[str], field: str) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("expected object", field)
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        _fail("object keys mismatch", field, {"missing": missing, "unknown": unknown})
    return value


def _hash(value: Any, field: str) -> str:
    checked = _string(value, field, 64)
    if len(checked) != 64 or any(character not in "0123456789abcdef" for character in checked):
        _fail("expected lowercase SHA256", field)
    return checked


def _bbox(value: Any, field: str) -> list[float]:
    if type(value) is not list or len(value) != 4:
        _fail("bbox_xyxy must contain four values", field)
    x1 = _number(value[0], f"{field}[0]", minimum=0.0, maximum=IMAGE_WIDTH)
    y1 = _number(value[1], f"{field}[1]", minimum=0.0, maximum=IMAGE_HEIGHT)
    x2 = _number(value[2], f"{field}[2]", minimum=0.0, maximum=IMAGE_WIDTH)
    y2 = _number(value[3], f"{field}[3]", minimum=0.0, maximum=IMAGE_HEIGHT)
    assert x1 is not None and y1 is not None and x2 is not None and y2 is not None
    if x1 >= x2 or y1 >= y2:
        _fail("bbox must have positive area", field)
    return [x1, y1, x2, y2]


def _geometry(value: Any, field: str) -> dict[str, Any]:
    expected = {
        "valid",
        "support_height_mm",
        "object_height_mm",
        "top_height_above_floor_mm",
        "distance_mm",
        "distance_status",
    }
    optional = {"geometry_confidence"}
    if type(value) is not dict:
        _fail("expected object", field)
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected - optional)
    if missing or unknown:
        _fail("object keys mismatch", field, {"missing": missing, "unknown": unknown})
    geometry = value
    if type(geometry["valid"]) is not bool:
        _fail("geometry.valid must be boolean", f"{field}.valid")
    for name in (
        "support_height_mm",
        "object_height_mm",
        "top_height_above_floor_mm",
        "distance_mm",
    ):
        _number(geometry[name], f"{field}.{name}", nullable=True)
    _string(geometry["distance_status"], f"{field}.distance_status")
    if "geometry_confidence" in geometry:
        _number(
            geometry["geometry_confidence"],
            f"{field}.geometry_confidence",
            nullable=True,
            minimum=0.0,
            maximum=1.0,
        )
    return geometry


def _visibility(value: Any, field: str) -> dict[str, Any]:
    expected = {"full_extent_visible", "touches_border"}
    visibility = _exact_keys(value, expected, field)
    for name in expected:
        if type(visibility[name]) is not bool:
            _fail("visibility flags must be boolean", f"{field}.{name}")
    return visibility


def _string_array(value: Any, field: str, *, unique: bool = False) -> list[str]:
    if type(value) is not list:
        _fail("expected array", field)
    for index, item in enumerate(value):
        _string(item, f"{field}[{index}]")
    if unique and len(value) != len(set(value)):
        _fail("array values must be unique", field)
    return value


def validate_p4_package(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "scan_id",
        "status",
        "generated_at",
        "whitelist",
        "source",
        "selection_policy_id",
        "class_summaries",
        "objects",
    }
    optional = {"selection"}
    if type(value) is not dict:
        _fail("expected object", "$")
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing or unknown:
        _fail(
            "object keys mismatch",
            "$",
            {"missing": missing, "unknown": unknown},
        )
    package = value
    if package["schema_version"] != P4_PACKAGE_SCHEMA:
        _fail("invalid package schema version", "schema_version")
    _string(package["scan_id"], "scan_id")
    if package["status"] != "decision_ready":
        _fail("status must be decision_ready", "status")
    _string(package["generated_at"], "generated_at")
    whitelist = _exact_keys(package["whitelist"], {"classes", "profile_sha256"}, "whitelist")
    if whitelist["classes"] != list(WHITELIST_CLASSES):
        _fail("whitelist order/content mismatch", "whitelist.classes")
    _hash(whitelist["profile_sha256"], "whitelist.profile_sha256")
    source = _exact_keys(
        package["source"],
        {
            "frame_count",
            "candidate_set_sha256",
            "scene_objects_sha256",
            "scene_objects_semantic_sha256",
        },
        "source",
    )
    if source["frame_count"] != 11:
        _fail("frame_count must be exactly 11", "source.frame_count")
    for name in (
        "candidate_set_sha256",
        "scene_objects_sha256",
        "scene_objects_semantic_sha256",
    ):
        _hash(source[name], f"source.{name}")
    if package["selection_policy_id"] not in SUPPORTED_SELECTION_POLICIES:
        _fail("invalid selection policy", "selection_policy_id")
    if type(package["class_summaries"]) is not list or len(package["class_summaries"]) != len(WHITELIST_CLASSES):
        _fail("class_summaries must contain all frozen classes", "class_summaries")
    summary_classes: list[str] = []
    summary_selected: list[tuple[str, str]] = []
    for index, summary_value in enumerate(package["class_summaries"]):
        field = f"class_summaries[{index}]"
        summary = _exact_keys(
            summary_value,
            {
                "class_name",
                "present",
                "observation_count",
                "fused_object_count",
                "selected_object_id",
            },
            field,
        )
        if summary["class_name"] not in WHITELIST_CLASSES:
            _fail("summary class outside whitelist", f"{field}.class_name")
        summary_classes.append(summary["class_name"])
        if type(summary["present"]) is not bool:
            _fail("present must be boolean", f"{field}.present")
        for name in ("observation_count", "fused_object_count"):
            if type(summary[name]) is not int or summary[name] < 0:
                _fail("count must be non-negative integer", f"{field}.{name}")
        if summary["selected_object_id"] is not None:
            summary_selected.append(
                (
                    summary["class_name"],
                    _string(
                        summary["selected_object_id"],
                        f"{field}.selected_object_id",
                    ),
                )
            )
        if summary["present"] != (summary["fused_object_count"] > 0):
            _fail("present does not match fused_object_count", f"{field}.present")
    if summary_classes != list(WHITELIST_CLASSES):
        _fail("class summaries must follow frozen whitelist order", "class_summaries")
    if type(package["objects"]) is not list:
        _fail("objects must be an array", "objects")
    package_ids: set[str] = set()
    package_classes: dict[str, str] = {}
    for index, value_object in enumerate(package["objects"]):
        field = f"objects[{index}]"
        item = _exact_keys(
            value_object,
            {
                "object_id",
                "class_name",
                "display_name_zh",
                "confidence",
                "observation_count",
                "source_frame_id",
                "source_frame_sequence",
                "bbox_xyxy",
                "geometry",
                "visibility",
                "warnings",
            },
            field,
        )
        object_id = _string(item["object_id"], f"{field}.object_id")
        package_ids.add(object_id)
        if item["class_name"] not in WHITELIST_CLASSES:
            _fail("class outside whitelist", f"{field}.class_name")
        package_classes[object_id] = item["class_name"]
        _string(item["display_name_zh"], f"{field}.display_name_zh")
        _number(item["confidence"], f"{field}.confidence", minimum=0.0, maximum=1.0)
        if type(item["observation_count"]) is not int or item["observation_count"] <= 0:
            _fail("observation_count must be positive", f"{field}.observation_count")
        _string(item["source_frame_id"], f"{field}.source_frame_id")
        if (
            type(item["source_frame_sequence"]) is not int
            or not 0 <= item["source_frame_sequence"] < source["frame_count"]
        ):
            _fail("frame sequence outside scan", f"{field}.source_frame_sequence")
        _bbox(item["bbox_xyxy"], f"{field}.bbox_xyxy")
        _geometry(item["geometry"], f"{field}.geometry")
        _visibility(item["visibility"], f"{field}.visibility")
        _string_array(item["warnings"], f"{field}.warnings")
    if len(package_ids) != len(package["objects"]):
        _fail("package object IDs must be unique", "objects")
    for class_name, object_id in summary_selected:
        if package_classes.get(object_id) != class_name:
            _fail(
                "summary selected object must reference a packaged object of the same class",
                "class_summaries",
            )
    if "selection" in package:
        selection = _exact_keys(
            package["selection"],
            {"recommended_target_object_id"},
            "selection",
        )
        recommended = selection["recommended_target_object_id"]
        if recommended is not None:
            recommended = _string(
                recommended,
                "selection.recommended_target_object_id",
            )
            if recommended not in package_ids:
                _fail(
                    "recommended target must reference package objects",
                    "selection.recommended_target_object_id",
                )
    return package
