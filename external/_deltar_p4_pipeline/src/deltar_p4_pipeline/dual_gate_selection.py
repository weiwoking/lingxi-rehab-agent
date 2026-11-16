from __future__ import annotations

from pathlib import Path
from typing import Any

from .geometry_contracts import ARTIFACT_FILENAME, validate_geometry_document
from .scan_contracts import (
    ScanContractError,
    atomic_write_json,
    canonical_sha256,
    load_strict_json,
    sha256_file,
)


DUAL_GATE_SCHEMA = "stage8b_dual_gate_selection.v1"
DUAL_GATE_FILENAME = "stage8b_dual_gate_selection.v1.json"
WHITELIST_SCHEMA = "stage8b_p4_object_whitelist.v1"
SELECTION_POLICY_ID = "class_geometry_fallback_then_p4_whitelist_v1"
GATE1_OUTCOMES = frozenset(
    {"pass_success", "pass_failure_fallback", "blocked_failure", "blocked_success"}
)


def _fail(message: str, field: str, details: Any = None) -> None:
    raise ScanContractError("E_STAGE8B_DUAL_GATE", message, field, details)


def normalize_class(value: Any) -> str:
    if not isinstance(value, str):
        _fail("class_name must be a string", "class_name")
    normalized = " ".join(value.strip().lower().split())
    if not normalized:
        _fail("class_name must not be empty", "class_name")
    return normalized


def validate_whitelist_profile(profile: dict[str, Any]) -> dict[str, Any]:
    expected = {"schema_version", "profile_id", "selection_policy_id", "classes"}
    if type(profile) is not dict or set(profile) != expected:
        _fail("whitelist profile keys mismatch", "whitelist_profile")
    if profile["schema_version"] != WHITELIST_SCHEMA:
        _fail("whitelist schema mismatch", "whitelist_profile.schema_version")
    if not isinstance(profile["profile_id"], str) or not profile["profile_id"]:
        _fail("profile_id must be a non-empty string", "whitelist_profile.profile_id")
    if profile["selection_policy_id"] != SELECTION_POLICY_ID:
        _fail("selection policy mismatch", "whitelist_profile.selection_policy_id")
    classes = profile["classes"]
    if not isinstance(classes, list):
        _fail("classes must be an array", "whitelist_profile.classes")
    normalized = [normalize_class(item) for item in classes]
    if not normalized or len(normalized) != len(set(normalized)):
        _fail("whitelist classes must be non-empty and unique", "whitelist_profile.classes")
    return profile


def _flatten_geometry(document: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for frame in sorted(
        document["frames"], key=lambda item: (int(item["sequence"]), str(item["frame_id"]))
    ):
        frame_id = str(frame["frame_id"])
        for item in sorted(frame["objects"], key=lambda value: str(value["logical_object_id"])):
            rows.append(
                {
                    "source_frame_id": frame_id,
                    "logical_object_id": str(item["logical_object_id"]),
                    "class_name": normalize_class(item["class_name"]),
                    "geometry_status": str(item["geometry_status"]),
                }
            )
    return rows


def build_dual_gate_document(
    geometry_document: dict[str, Any],
    whitelist_profile: dict[str, Any],
    *,
    geometry_sha256: str | None = None,
    whitelist_sha256: str | None = None,
) -> dict[str, Any]:
    validate_geometry_document(geometry_document)
    validate_whitelist_profile(whitelist_profile)
    rows = _flatten_geometry(geometry_document)
    profile_classes = {normalize_class(item) for item in whitelist_profile["classes"]}
    success_by_class = {
        class_name: any(
            row["class_name"] == class_name and row["geometry_status"] in {"ok", "degraded"}
            for row in rows
        )
        for class_name in {row["class_name"] for row in rows}
    }
    objects: list[dict[str, Any]] = []
    for row in rows:
        has_success = success_by_class[row["class_name"]]
        is_success = row["geometry_status"] in {"ok", "degraded"}
        if is_success:
            outcome = "pass_success"
            gate1_passed = True
        else:
            outcome = "pass_failure_fallback"
            gate1_passed = True
        objects.append(
            {
                **row,
                "class_has_geometry_success": has_success,
                "gate1_outcome": outcome,
                "gate1_passed": gate1_passed,
                "gate2_passed": row["class_name"] in profile_classes,
            }
        )

    summaries: list[dict[str, Any]] = []
    for class_name in sorted(success_by_class):
        class_objects = [item for item in objects if item["class_name"] == class_name]
        summaries.append(
            {
                "class_name": class_name,
                "object_count": len(class_objects),
                "geometry_success_count": sum(
                    item["geometry_status"] in {"ok", "degraded"} for item in class_objects
                ),
                "geometry_failure_count": sum(
                    item["geometry_status"] == "unavailable" for item in class_objects
                ),
                "class_has_geometry_success": success_by_class[class_name],
                "gate1_pass_count": sum(item["gate1_passed"] for item in class_objects),
                "gate2_pass_count": sum(item["gate2_passed"] for item in class_objects),
            }
        )
    document = {
        "schema_version": DUAL_GATE_SCHEMA,
        "scan_id": str(geometry_document["scan_id"]),
        "frame_count": int(geometry_document["frame_count"]),
        "selection_policy_id": SELECTION_POLICY_ID,
        "whitelist_profile": {
            "schema_version": WHITELIST_SCHEMA,
            "profile_id": str(whitelist_profile["profile_id"]),
            "sha256": whitelist_sha256 or canonical_sha256(whitelist_profile),
            "classes": [normalize_class(item) for item in whitelist_profile["classes"]],
        },
        "source": {
            "all_object_geometry_path": ARTIFACT_FILENAME,
            "all_object_geometry_sha256": geometry_sha256
            or canonical_sha256(geometry_document),
        },
        "class_summaries": summaries,
        "objects": objects,
    }
    return validate_dual_gate_document(document)


def validate_dual_gate_document(document: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version",
        "scan_id",
        "frame_count",
        "selection_policy_id",
        "whitelist_profile",
        "source",
        "class_summaries",
        "objects",
    }
    if type(document) is not dict or set(document) != expected:
        _fail("dual-gate document keys mismatch", "$")
    if document["schema_version"] != DUAL_GATE_SCHEMA:
        _fail("dual-gate schema mismatch", "schema_version")
    if not isinstance(document["scan_id"], str) or not document["scan_id"]:
        _fail("scan_id must be a non-empty string", "scan_id")
    if document["frame_count"] != 11:
        _fail("frame_count must be exactly 11", "frame_count")
    if document["selection_policy_id"] != SELECTION_POLICY_ID:
        _fail("selection policy mismatch", "selection_policy_id")
    profile = document["whitelist_profile"]
    if type(profile) is not dict or set(profile) != {
        "schema_version", "profile_id", "sha256", "classes"
    }:
        _fail("whitelist identity keys mismatch", "whitelist_profile")
    validate_whitelist_profile(
        {
            "schema_version": profile["schema_version"],
            "profile_id": profile["profile_id"],
            "selection_policy_id": document["selection_policy_id"],
            "classes": profile["classes"],
        }
    )
    for field in ("whitelist_profile.sha256",):
        value = profile["sha256"]
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            _fail("expected lowercase SHA256", field)
    source = document["source"]
    if type(source) is not dict or set(source) != {
        "all_object_geometry_path", "all_object_geometry_sha256"
    }:
        _fail("source keys mismatch", "source")
    if source["all_object_geometry_path"] != ARTIFACT_FILENAME:
        _fail("authority path mismatch", "source.all_object_geometry_path")
    source_hash = source["all_object_geometry_sha256"]
    if not isinstance(source_hash, str) or len(source_hash) != 64 or any(
        c not in "0123456789abcdef" for c in source_hash
    ):
        _fail("expected lowercase SHA256", "source.all_object_geometry_sha256")
    if not isinstance(document["objects"], list):
        _fail("objects must be an array", "objects")
    identities: list[tuple[str, str]] = []
    expected_item_keys = {
        "source_frame_id",
        "logical_object_id",
        "class_name",
        "geometry_status",
        "class_has_geometry_success",
        "gate1_outcome",
        "gate1_passed",
        "gate2_passed",
    }
    for index, item in enumerate(document["objects"]):
        field = f"objects[{index}]"
        if type(item) is not dict or set(item) != expected_item_keys:
            _fail("gate object keys mismatch", field)
        if item["class_name"] != normalize_class(item["class_name"]):
            _fail("class_name is not normalized", f"{field}.class_name")
        if item["geometry_status"] not in {"ok", "degraded", "unavailable"}:
            _fail("geometry_status must be ok/degraded/unavailable", f"{field}.geometry_status")
        if item["gate1_outcome"] not in GATE1_OUTCOMES:
            _fail("invalid Gate 1 outcome", f"{field}.gate1_outcome")
        if any(type(item[name]) is not bool for name in (
            "class_has_geometry_success", "gate1_passed", "gate2_passed"
        )):
            _fail("gate flags must be boolean", field)
        expected_gate2 = item["class_name"] in set(profile["classes"])
        if item["gate2_passed"] != expected_gate2:
            _fail("Gate 2 must be the pure class whitelist", f"{field}.gate2_passed")
        if item["gate1_outcome"].startswith("pass_") != item["gate1_passed"]:
            _fail("Gate 1 outcome/flag mismatch", field)
        expected_numeric = item["gate1_outcome"] in {"pass_success", "blocked_success"}
        if (item["geometry_status"] in {"ok", "degraded"}) != expected_numeric:
            _fail("Gate 1 outcome/geometry status mismatch", field)
        if item["gate1_outcome"] in {"pass_success", "blocked_failure", "blocked_success"} and not item[
            "class_has_geometry_success"
        ]:
            _fail("class success flag mismatch", field)
        identities.append((item["source_frame_id"], item["logical_object_id"]))
    if len(identities) != len(set(identities)):
        _fail("source identities must be unique", "objects")
    if not isinstance(document["class_summaries"], list):
        _fail("class_summaries must be an array", "class_summaries")
    if sum(int(item["object_count"]) for item in document["class_summaries"]) != len(
        document["objects"]
    ):
        _fail("class summary total mismatch", "class_summaries")
    return document


def build_and_write_dual_gate(
    result_root: Path,
    whitelist_profile_path: Path,
) -> tuple[dict[str, Any], Path]:
    root = result_root.resolve()
    geometry_path = root / ARTIFACT_FILENAME
    geometry_document = load_strict_json(geometry_path, code="E_STAGE8B_DUAL_GATE")
    profile = load_strict_json(whitelist_profile_path, code="E_STAGE8B_DUAL_GATE")
    document = build_dual_gate_document(
        geometry_document,
        profile,
        geometry_sha256=sha256_file(geometry_path),
        whitelist_sha256=sha256_file(whitelist_profile_path),
    )
    destination = root / DUAL_GATE_FILENAME
    atomic_write_json(destination, document)
    return document, destination
