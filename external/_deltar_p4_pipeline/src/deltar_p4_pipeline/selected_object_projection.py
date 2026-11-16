from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from .geometry_contracts import ARTIFACT_FILENAME, validate_geometry_document
from .dual_gate_selection import (
    SELECTION_POLICY_ID,
    validate_dual_gate_document,
)
from .scan_contracts import (
    ScanContractError,
    atomic_write_json,
    canonical_sha256,
    load_strict_json,
    sha256_file,
)
from .stage8b4a_contracts import WHITELIST_CLASSES


SELECTED_OBJECTS_SCHEMA = "stage8b4a_selected_objects.v2"
SELECTED_OBJECTS_FILENAME = "stage8b4a_selected_objects.v2.json"
METADATA_ADAPTER_SCHEMA = "stage8b_exact_metadata_adapter.v1"

_DISPLAY_NAMES_ZH = {
    "cup": "水杯",
    "bottle": "瓶子",
    "book": "书",
    "sports ball": "运动球",
}
_GEOMETRY_VALUE_FIELDS = (
    "object_height_mm",
    "support_height_mm",
    "top_height_above_floor_mm",
    "distance_mm",
)
_PACKET_GEOMETRY_FIELDS = (
    "valid",
    *_GEOMETRY_VALUE_FIELDS,
    "distance_status",
    "geometry_confidence",
)
_VISIBILITY_FIELDS = ("full_extent_visible", "touches_border")
_FRAME_ID = re.compile(r"^frame_(\d{6})$")


def _fail(message: str, field: str, details: Any = None) -> None:
    raise ScanContractError("E_STAGE8B4A_SELECTION_PROJECTION", message, field, details)


def _stable_object_id(class_name: str, frame_id: str, logical_object_id: str) -> str:
    identity = f"{class_name}\n{frame_id}\n{logical_object_id}".encode("utf-8")
    return f"scene_object_{class_name}_{hashlib.sha256(identity).hexdigest()[:16]}"


def _frame_sequence(frame_id: Any) -> int | None:
    if not isinstance(frame_id, str):
        return None
    match = _FRAME_ID.fullmatch(frame_id)
    if match is None:
        return None
    sequence = int(match.group(1))
    return sequence if 0 <= sequence <= 10 else None


def _strict_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    if any(
        isinstance(item, bool)
        or not isinstance(item, (int, float))
        or not math.isfinite(float(item))
        for item in value
    ):
        return None
    bbox = [float(item) for item in value]
    if not (
        0.0 <= bbox[0] < bbox[2] <= 640.0
        and 0.0 <= bbox[1] < bbox[3] <= 360.0
    ):
        return None
    return bbox


def load_exact_metadata_adapter(
    result_root: Path,
    geometry_document: dict[str, Any],
) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], dict[str, Any]]:
    """Load non-geometry metadata using exact source identity only.

    This is the sole compatibility boundary for current logical-object metadata.
    It never ranks, substitutes, compares IoU, or copies geometry.
    """

    validate_geometry_document(geometry_document)
    root = result_root.resolve()
    rows: list[dict[str, Any]] = []
    files: list[dict[str, str]] = []
    for frame in sorted(
        geometry_document["frames"], key=lambda item: (int(item["sequence"]), item["frame_id"])
    ):
        frame_id = str(frame["frame_id"])
        frame_path = root / "frame_results" / f"{frame_id}.json"
        if not frame_path.is_file():
            continue
        frame_result = load_strict_json(frame_path, code="E_STAGE8B_METADATA_ADAPTER")
        files.append(
            {
                "path": str(frame_path.relative_to(root)).replace("\\", "/"),
                "sha256": sha256_file(frame_path),
            }
        )
        for item in frame_result.get("objects", []):
            logical_object_id = item.get("logical_object_id")
            if not isinstance(logical_object_id, str) or not logical_object_id:
                continue
            visibility = item.get("visibility") or {}
            rows.append(
                {
                    "source_frame_id": frame_id,
                    "logical_object_id": logical_object_id,
                    "class_name": " ".join(str(item.get("class_name", "")).strip().lower().split()),
                    "bbox_xyxy": deepcopy(item.get("bbox_xyxy")),
                    "confidence": item.get("confidence"),
                    "visibility": {
                        "full_extent_visible": visibility.get("full_extent_visible"),
                        "touches_border": visibility.get("touches_image_border"),
                    },
                    "warnings": deepcopy(item.get("warnings", [])),
                }
            )
    rows.sort(key=lambda item: (item["source_frame_id"], item["logical_object_id"]))
    by_identity: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row["source_frame_id"], row["logical_object_id"])
        by_identity.setdefault(key, []).append(row)
    source = {
        "schema_version": METADATA_ADAPTER_SCHEMA,
        "sha256": canonical_sha256(rows),
        "files": files,
        "row_count": len(rows),
        "join": "exact_source_frame_id_and_logical_object_id",
    }
    return by_identity, source


def _authority_index(
    geometry_document: dict[str, Any],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for frame in geometry_document["frames"]:
        frame_id = str(frame["frame_id"])
        for item in frame["objects"]:
            index.setdefault((frame_id, str(item["logical_object_id"])), []).append(item)
    return index


def _geometry_from_authority(item: dict[str, Any]) -> dict[str, Any] | None:
    geometry = item.get("geometry")
    if not isinstance(geometry, dict):
        return None
    values: dict[str, float | None] = {}
    for name in _GEOMETRY_VALUE_FIELDS:
        value = geometry.get(name)
        if value is None:
            values[name] = None
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            return None
        else:
            values[name] = float(value)
    status = item.get("geometry_status")
    if status not in {"ok", "degraded", "unavailable"}:
        return None
    confidence = item.get("geometry_confidence")
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not 0.0 <= float(confidence) <= 1.0
    ):
        return None
    numeric_available = status in {"ok", "degraded"}
    if numeric_available and confidence is None:
        return None
    if numeric_available and any(values[name] is None for name in _GEOMETRY_VALUE_FIELDS):
        return None
    return {
        # The frozen P4 package has no separate degraded enum.  `valid` means
        # numeric metric geometry is present; quality remains in confidence
        # and distance_status.
        "valid": numeric_available,
        **values,
        "distance_status": (
            f"all_object_geometry_{status}"
            if numeric_available
            else str(item.get("failure_reason") or "all_object_geometry_unavailable")
        ),
        "geometry_confidence": (
            float(confidence) if confidence is not None else None
        ),
    }


def sanitize_gate_candidates(
    gate_objects: list[dict[str, Any]],
    authority_by_identity: dict[tuple[str, str], list[dict[str, Any]]],
    metadata_by_identity: dict[tuple[str, str], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    gated = [
        item
        for item in gate_objects
        if item.get("gate1_passed") is True and item.get("gate2_passed") is True
    ]
    accepted: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []

    def drop(item: dict[str, Any], reason: str) -> None:
        dropped.append(
            {
                "source_frame_id": str(item.get("source_frame_id", "")),
                "logical_object_id": str(item.get("logical_object_id", "")),
                "reason": reason,
            }
        )

    for gate_item in gated:
        frame_id = gate_item.get("source_frame_id")
        logical_id = gate_item.get("logical_object_id")
        identity = (str(frame_id), str(logical_id))
        if _frame_sequence(frame_id) is None:
            drop(gate_item, "invalid_source_frame_id")
            continue
        authority_matches = authority_by_identity.get(identity, [])
        if len(authority_matches) != 1:
            drop(gate_item, "authority_missing" if not authority_matches else "authority_identity_conflict")
            continue
        metadata_matches = metadata_by_identity.get(identity, [])
        if len(metadata_matches) != 1:
            drop(gate_item, "metadata_missing" if not metadata_matches else "metadata_identity_conflict")
            continue
        authority = authority_matches[0]
        metadata = metadata_matches[0]
        class_name = str(gate_item.get("class_name", ""))
        authority_class = " ".join(str(authority.get("class_name", "")).strip().lower().split())
        metadata_class = " ".join(str(metadata.get("class_name", "")).strip().lower().split())
        if class_name not in WHITELIST_CLASSES or authority_class != class_name or metadata_class != class_name:
            drop(gate_item, "class_identity_mismatch")
            continue
        bbox = _strict_bbox(metadata.get("bbox_xyxy"))
        if bbox is None:
            drop(gate_item, "invalid_bbox_xyxy")
            continue
        confidence = metadata.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            drop(gate_item, "invalid_confidence")
            continue
        visibility = metadata.get("visibility")
        if not isinstance(visibility, dict) or any(
            type(visibility.get(name)) is not bool for name in _VISIBILITY_FIELDS
        ):
            drop(gate_item, "invalid_visibility")
            continue
        warnings = metadata.get("warnings")
        if not isinstance(warnings, list) or any(
            not isinstance(value, str) or not value for value in warnings
        ):
            drop(gate_item, "invalid_warnings")
            continue
        geometry = _geometry_from_authority(authority)
        if geometry is None:
            drop(gate_item, "invalid_authority_geometry")
            continue
        accepted.append(
            {
                "object_id": _stable_object_id(class_name, str(frame_id), str(logical_id)),
                "class_name": class_name,
                "source_frame_id": str(frame_id),
                "logical_object_id": str(logical_id),
                "bbox_xyxy": bbox,
                "geometry": geometry,
                "visibility": {name: visibility[name] for name in _VISIBILITY_FIELDS},
                "confidence": float(confidence),
                "warnings": list(warnings),
                "gate1_outcome": str(gate_item["gate1_outcome"]),
            }
        )

    identity_counts = Counter(
        (item["source_frame_id"], item["logical_object_id"]) for item in accepted
    )
    object_id_counts = Counter(item["object_id"] for item in accepted)
    final_items: list[dict[str, Any]] = []
    for item in accepted:
        identity = (item["source_frame_id"], item["logical_object_id"])
        reason = None
        if identity_counts[identity] > 1:
            reason = "source_identity_conflict"
        elif object_id_counts[item["object_id"]] > 1:
            reason = "object_id_conflict"
        if reason is not None:
            drop(item, reason)
        else:
            final_items.append(item)
    final_items.sort(
        key=lambda item: (
            _frame_sequence(item["source_frame_id"]),
            item["logical_object_id"],
            item["class_name"],
        )
    )
    reasons = Counter(item["reason"] for item in dropped)
    sanitizer = {
        "input_count": len(gated),
        "dropped_count": len(dropped),
        "drop_reasons": dict(sorted(reasons.items())),
        "dropped": sorted(
            dropped,
            key=lambda item: (
                item["source_frame_id"], item["logical_object_id"], item["reason"]
            ),
        ),
        "final_count": len(final_items),
    }
    if sanitizer["input_count"] != sanitizer["dropped_count"] + sanitizer["final_count"]:
        _fail("sanitizer count invariant failed", "sanitizer")
    return final_items, sanitizer


def build_selected_objects_document(
    geometry_document: dict[str, Any],
    gate_document: dict[str, Any],
    metadata_by_identity: dict[tuple[str, str], list[dict[str, Any]]],
    *,
    gate_sha256: str,
    metadata_adapter_sha256: str,
) -> dict[str, Any]:
    validate_geometry_document(geometry_document)
    validate_dual_gate_document(gate_document)
    if geometry_document["scan_id"] != gate_document["scan_id"]:
        _fail("geometry and gate scan IDs differ", "scan_id")
    items, sanitizer = sanitize_gate_candidates(
        gate_document["objects"], _authority_index(geometry_document), metadata_by_identity
    )
    document = {
        "schema_version": SELECTED_OBJECTS_SCHEMA,
        "scan_id": str(geometry_document["scan_id"]),
        "selection_policy_id": SELECTION_POLICY_ID,
        "whitelist_profile": deepcopy(gate_document["whitelist_profile"]),
        "source": {
            "all_object_geometry_sha256": gate_document["source"]["all_object_geometry_sha256"],
            "dual_gate_selection_sha256": gate_sha256,
            "metadata_adapter_sha256": metadata_adapter_sha256,
        },
        "sanitizer": sanitizer,
        "items": items,
    }
    return validate_selected_objects_document(document)


def validate_selected_objects_document(value: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version", "scan_id", "selection_policy_id", "whitelist_profile",
        "source", "sanitizer", "items",
    }
    if type(value) is not dict or set(value) != expected:
        _fail("selected-object contract keys mismatch", "$")
    if value["schema_version"] != SELECTED_OBJECTS_SCHEMA:
        _fail("selected-object schema version mismatch", "schema_version")
    if value["selection_policy_id"] != SELECTION_POLICY_ID:
        _fail("selected-object policy mismatch", "selection_policy_id")
    if not isinstance(value["scan_id"], str) or not value["scan_id"]:
        _fail("scan_id must be a non-empty string", "scan_id")
    if not isinstance(value["items"], list):
        _fail("items must be an array", "items")
    source = value["source"]
    if type(source) is not dict or set(source) != {
        "all_object_geometry_sha256", "dual_gate_selection_sha256", "metadata_adapter_sha256"
    }:
        _fail("selected source keys mismatch", "source")
    for name, source_hash in source.items():
        if not isinstance(source_hash, str) or len(source_hash) != 64 or any(
            char not in "0123456789abcdef" for char in source_hash
        ):
            _fail("expected lowercase SHA256", f"source.{name}")
    sanitizer = value["sanitizer"]
    if type(sanitizer) is not dict or set(sanitizer) != {
        "input_count", "dropped_count", "drop_reasons", "dropped", "final_count"
    }:
        _fail("sanitizer keys mismatch", "sanitizer")
    if sanitizer["final_count"] != len(value["items"]):
        _fail("sanitizer final_count mismatch", "sanitizer.final_count")
    if sanitizer["input_count"] != sanitizer["dropped_count"] + sanitizer["final_count"]:
        _fail("sanitizer count mismatch", "sanitizer")
    if sum(sanitizer["drop_reasons"].values()) != sanitizer["dropped_count"]:
        _fail("sanitizer reason count mismatch", "sanitizer.drop_reasons")
    item_keys = {
        "object_id", "class_name", "source_frame_id", "logical_object_id", "bbox_xyxy",
        "geometry", "visibility", "confidence", "warnings", "gate1_outcome",
    }
    identities: list[tuple[str, str]] = []
    object_ids: list[str] = []
    for index, item in enumerate(value["items"]):
        field = f"items[{index}]"
        if type(item) is not dict or set(item) != item_keys:
            _fail("selected item keys mismatch", field)
        if item["class_name"] not in WHITELIST_CLASSES:
            _fail("selected class outside whitelist", f"{field}.class_name")
        if _frame_sequence(item["source_frame_id"]) is None:
            _fail("invalid source frame identity", f"{field}.source_frame_id")
        if not isinstance(item["logical_object_id"], str) or not item["logical_object_id"]:
            _fail("logical_object_id is required", f"{field}.logical_object_id")
        expected_id = _stable_object_id(item["class_name"], item["source_frame_id"], item["logical_object_id"])
        if item["object_id"] != expected_id:
            _fail("object_id is not deterministic", f"{field}.object_id")
        if _strict_bbox(item["bbox_xyxy"]) is None:
            _fail("invalid bbox_xyxy", f"{field}.bbox_xyxy")
        if type(item["geometry"]) is not dict or set(item["geometry"]) != set(_PACKET_GEOMETRY_FIELDS):
            _fail("geometry keys mismatch", f"{field}.geometry")
        for name in _GEOMETRY_VALUE_FIELDS:
            number = item["geometry"][name]
            if number is not None and (
                isinstance(number, bool)
                or not isinstance(number, (int, float))
                or not math.isfinite(float(number))
            ):
                _fail("geometry values must be finite or null", f"{field}.geometry.{name}")
        if type(item["geometry"]["valid"]) is not bool:
            _fail("geometry.valid must be boolean", f"{field}.geometry.valid")
        if not isinstance(item["geometry"]["distance_status"], str) or not item["geometry"]["distance_status"]:
            _fail("distance_status is required", f"{field}.geometry.distance_status")
        geometry_confidence = item["geometry"]["geometry_confidence"]
        if geometry_confidence is not None and (
            isinstance(geometry_confidence, bool)
            or not isinstance(geometry_confidence, (int, float))
            or not math.isfinite(float(geometry_confidence))
            or not 0.0 <= float(geometry_confidence) <= 1.0
        ):
            _fail(
                "geometry_confidence must be finite in [0,1] or null",
                f"{field}.geometry.geometry_confidence",
            )
        if type(item["visibility"]) is not dict or set(item["visibility"]) != set(_VISIBILITY_FIELDS) or any(
            type(item["visibility"][name]) is not bool for name in _VISIBILITY_FIELDS
        ):
            _fail("visibility keys/values mismatch", f"{field}.visibility")
        confidence = item["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(float(confidence)) or not 0.0 <= float(confidence) <= 1.0:
            _fail("confidence must be finite in [0,1]", f"{field}.confidence")
        if not isinstance(item["warnings"], list) or any(
            not isinstance(warning, str) or not warning for warning in item["warnings"]
        ):
            _fail("warnings must be strings", f"{field}.warnings")
        if item["gate1_outcome"] not in {"pass_success", "pass_failure_fallback"}:
            _fail("selected item must have passed Gate 1", f"{field}.gate1_outcome")
        identities.append((item["source_frame_id"], item["logical_object_id"]))
        object_ids.append(item["object_id"])
    if len(identities) != len(set(identities)):
        _fail("selected source identities must be unique", "items")
    if len(object_ids) != len(set(object_ids)):
        _fail("selected object_id values must be unique", "items")
    return value


def project_selected_items(selected_document: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure one-item-to-one-object packet projection with strict frame derivation."""

    validate_selected_objects_document(selected_document)
    projected: list[dict[str, Any]] = []
    for item in selected_document["items"]:
        sequence = _frame_sequence(item["source_frame_id"])
        if sequence is None:
            _fail("invalid frame identity reached packet projection", "source_frame_id")
        projected.append(
            {
                "object_id": item["object_id"],
                "class_name": item["class_name"],
                "display_name_zh": _DISPLAY_NAMES_ZH[item["class_name"]],
                "confidence": item["confidence"],
                "observation_count": 1,
                "source_frame_id": item["source_frame_id"],
                "source_frame_sequence": sequence,
                "bbox_xyxy": list(item["bbox_xyxy"]),
                "geometry": {name: item["geometry"][name] for name in _PACKET_GEOMETRY_FIELDS},
                "visibility": {name: item["visibility"][name] for name in _VISIBILITY_FIELDS},
                "warnings": list(item["warnings"]),
            }
        )
    return projected


def validate_projection_invariants(
    selected_document: dict[str, Any],
    package: dict[str, Any],
) -> None:
    validate_selected_objects_document(selected_document)
    expected = project_selected_items(selected_document)
    actual = package.get("objects")
    if not isinstance(actual, list):
        _fail("packet objects must be an array", "objects")
    expected_ids = [item["object_id"] for item in expected]
    actual_ids = [item.get("object_id") for item in actual if isinstance(item, dict)]
    details = {
        "selected_count": len(expected),
        "packet_count": len(actual),
        "missing_ids": sorted(set(expected_ids) - set(actual_ids)),
        "extra_ids": sorted(set(actual_ids) - set(expected_ids)),
        "duplicate_selected_ids": len(expected_ids) - len(set(expected_ids)),
        "duplicate_packet_ids": len(actual_ids) - len(set(actual_ids)),
    }
    if expected != actual:
        _fail(
            "packet objects are not the exact ordered 1:1 projection of selected items",
            "objects",
            details,
        )
    for index, (source, target) in enumerate(zip(selected_document["items"], actual)):
        if source["object_id"] != target["object_id"] or source["class_name"] != target["class_name"]:
            _fail("mixed-source A-to-B projection detected", f"objects[{index}]")


def build_and_write_selected_objects(
    result_root: Path,
    gate_document: dict[str, Any],
    gate_path: Path,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    root = result_root.resolve()
    geometry_path = root / ARTIFACT_FILENAME
    geometry_document = load_strict_json(geometry_path, code="E_STAGE8B4A_INPUT")
    metadata_by_identity, metadata_source = load_exact_metadata_adapter(root, geometry_document)
    selected = build_selected_objects_document(
        geometry_document,
        gate_document,
        metadata_by_identity,
        gate_sha256=sha256_file(gate_path),
        metadata_adapter_sha256=metadata_source["sha256"],
    )
    path = root / SELECTED_OBJECTS_FILENAME
    atomic_write_json(path, selected)
    return selected, path, metadata_source
