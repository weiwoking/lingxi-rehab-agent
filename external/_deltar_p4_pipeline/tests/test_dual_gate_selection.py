from __future__ import annotations

from copy import deepcopy

import pytest
import deltar_p4_pipeline.selected_object_projection as projection_module

from deltar_p4_pipeline.dual_gate_selection import (
    SELECTION_POLICY_ID,
    build_dual_gate_document,
    validate_dual_gate_document,
)
from deltar_p4_pipeline.scan_contracts import ScanContractError, canonical_sha256
from deltar_p4_pipeline.selected_object_projection import (
    build_selected_objects_document,
    project_selected_items,
    sanitize_gate_candidates,
    validate_projection_invariants,
    validate_selected_objects_document,
)
from deltar_p4_pipeline.stage8b4a_contracts import validate_p4_package
from deltar_p4_pipeline.stage8b4a_package import build_p4_package


HASH = "0" * 64
PROFILE = {
    "schema_version": "stage8b_p4_object_whitelist.v1",
    "profile_id": "test_whitelist",
    "selection_policy_id": SELECTION_POLICY_ID,
    "classes": ["book", "cup", "bottle"],
}


def authority_object(
    logical_id: str,
    class_name: str,
    status: str,
    *,
    geometry: dict | None = None,
    confidence: float = 0.2,
) -> dict:
    if geometry is None:
        geometry = (
            {
                "distance_mm": 1000.0,
                "object_height_mm": 100.0,
                "support_height_mm": 700.0,
                "top_height_above_floor_mm": 800.0,
            }
            if status in {"ok", "degraded"}
            else {
                "distance_mm": None,
                "object_height_mm": None,
                "support_height_mm": None,
                "top_height_above_floor_mm": None,
            }
        )
    return {
        "logical_object_id": logical_id,
        "class_name": class_name,
        "numeric_available": status in {"ok", "degraded"},
        "geometry_status": status,
        "geometry": geometry,
        "geometry_confidence": 0.8 if status in {"ok", "degraded"} else None,
        "degradation_reason": "fixture_degraded" if status == "degraded" else None,
        "failure_reason": None if status in {"ok", "degraded"} else "fixture_failure",
        "confidence": confidence,
        "bbox_xyxy": [10.0, 20.0, 40.0, 80.0],
        "diagnostics": {"region_source": "mask"} if status in {"ok", "degraded"} else {},
    }


def geometry_document() -> dict:
    objects = [
        authority_object("bottle-ok", " Bottle ", "ok"),
        authority_object("bottle-fail", "bottle", "unavailable"),
        authority_object("cup-fail-a", "cup", "unavailable", confidence=0.01),
        authority_object("cup-fail-b", "cup", "unavailable"),
        authority_object("book-ok", "book", "ok"),
        authority_object("chair-ok", "chair", "ok"),
    ]
    return {
        "schema_version": "stage8b_all_object_geometry.v1",
        "scan_id": "scan_fixture",
        "frame_count": 11,
        "generated_at": "2026-07-31T00:00:00Z",
        "frames": [
            {
                "frame_id": "frame_000000",
                "sequence": 0,
                "source_image_path": None,
                "objects": objects,
            }
        ],
        "summary": {
            "object_count": len(objects),
            "geometry_success_count": 3,
            "geometry_failure_count": 3,
        },
    }


def metadata_for(document: dict) -> dict[tuple[str, str], list[dict]]:
    rows = {}
    for item in document["frames"][0]["objects"]:
        identity = ("frame_000000", item["logical_object_id"])
        rows[identity] = [
            {
                "source_frame_id": identity[0],
                "logical_object_id": identity[1],
                "class_name": item["class_name"].strip().lower(),
                "bbox_xyxy": list(item["bbox_xyxy"]),
                "confidence": item["confidence"],
                "visibility": {
                    "full_extent_visible": False,
                    "touches_border": True,
                },
                "warnings": ["W_LOW_CONFIDENCE_ALLOWED"],
            }
        ]
    return rows


def gate_document() -> dict:
    return build_dual_gate_document(
        geometry_document(), PROFILE, geometry_sha256=HASH, whitelist_sha256=HASH
    )


def geometry_with(objects: list[dict], *, frame_id: str = "frame_000000", sequence: int = 0) -> dict:
    return {
        "schema_version": "stage8b_all_object_geometry.v1",
        "scan_id": "scan_custom",
        "frame_count": 11,
        "generated_at": "2026-07-31T00:00:00Z",
        "frames": [{"frame_id": frame_id, "sequence": sequence, "source_image_path": None, "objects": objects}],
        "summary": {
            "object_count": len(objects),
            "geometry_success_count": sum(item["numeric_available"] for item in objects),
            "geometry_failure_count": sum(item["geometry_status"] == "unavailable" for item in objects),
        },
    }


def selected_document() -> dict:
    geometry = geometry_document()
    gate = build_dual_gate_document(
        geometry, PROFILE, geometry_sha256=HASH, whitelist_sha256=HASH
    )
    return build_selected_objects_document(
        geometry,
        gate,
        metadata_for(geometry),
        gate_sha256=HASH,
        metadata_adapter_sha256=HASH,
    )


def test_gate1_success_exists_preserves_failure_with_nullable_geometry() -> None:
    gate = gate_document()
    bottle = {item["logical_object_id"]: item for item in gate["objects"] if item["class_name"] == "bottle"}
    assert bottle["bottle-ok"]["gate1_outcome"] == "pass_success"
    assert bottle["bottle-ok"]["gate1_passed"] is True
    assert bottle["bottle-fail"]["gate1_outcome"] == "pass_failure_fallback"
    assert bottle["bottle-fail"]["gate1_passed"] is True


def test_gate1_three_success_two_failure_preserves_all_five() -> None:
    geometry = geometry_with(
        [authority_object(f"success-{index}", "bottle", "ok") for index in range(3)]
        + [authority_object(f"failure-{index}", "bottle", "unavailable") for index in range(2)]
    )
    gate = build_dual_gate_document(geometry, PROFILE)
    passed = [item for item in gate["objects"] if item["gate1_passed"]]
    assert len(passed) == 5
    assert sum(item["geometry_status"] == "ok" for item in passed) == 3
    assert sum(item["geometry_status"] == "unavailable" for item in passed) == 2


def test_gate1_zero_success_four_failure_passes_all_four() -> None:
    geometry = geometry_with(
        [authority_object(f"failure-{index}", "cup", "unavailable") for index in range(4)]
    )
    gate = build_dual_gate_document(geometry, PROFILE)
    assert len(gate["objects"]) == 4
    assert all(item["gate1_outcome"] == "pass_failure_fallback" for item in gate["objects"])


def test_gate1_no_success_passes_all_failures_and_gate2_is_pure_whitelist() -> None:
    gate = gate_document()
    cups = [item for item in gate["objects"] if item["class_name"] == "cup"]
    assert len(cups) == 2
    assert all(item["gate1_outcome"] == "pass_failure_fallback" for item in cups)
    assert all(item["gate1_passed"] and item["gate2_passed"] for item in cups)
    chair = next(item for item in gate["objects"] if item["class_name"] == "chair")
    assert chair["gate1_passed"] is True
    assert chair["gate2_passed"] is False


def test_gate2_profile_change_needs_no_core_code_change() -> None:
    profile = deepcopy(PROFILE)
    profile["classes"] = ["chair"]
    gate = build_dual_gate_document(geometry_document(), profile)
    chair = next(item for item in gate["objects"] if item["class_name"] == "chair")
    bottle = next(item for item in gate["objects"] if item["class_name"] == "bottle")
    assert chair["gate2_passed"] is True
    assert bottle["gate2_passed"] is False


def test_contract_allows_blocked_success_fixture_but_core_does_not_emit_it() -> None:
    gate = gate_document()
    assert all(item["gate1_outcome"] != "blocked_success" for item in gate["objects"])
    fixture = deepcopy(gate)
    item = next(value for value in fixture["objects"] if value["logical_object_id"] == "book-ok")
    item["gate1_outcome"] = "blocked_success"
    item["gate1_passed"] = False
    assert validate_dual_gate_document(fixture) is fixture


def test_selected_v2_preserves_unavailable_geometry_and_non_gate_metadata() -> None:
    selected = selected_document()
    assert selected["schema_version"] == "stage8b4a_selected_objects.v2"
    assert selected["sanitizer"] == {
        "input_count": 5,
        "dropped_count": 0,
        "drop_reasons": {},
        "dropped": [],
        "final_count": 5,
    }
    partial = next(item for item in selected["items"] if item["logical_object_id"] == "cup-fail-b")
    assert partial["geometry"]["object_height_mm"] is None
    assert partial["geometry"]["distance_mm"] is None
    assert partial["geometry"]["geometry_confidence"] is None
    successful = next(item for item in selected["items"] if item["logical_object_id"] == "book-ok")
    assert successful["geometry"]["geometry_confidence"] == 0.8
    low = next(item for item in selected["items"] if item["logical_object_id"] == "cup-fail-a")
    assert low["confidence"] == 0.01
    assert low["visibility"] == {"full_extent_visible": False, "touches_border": True}
    forbidden = {
        "source_frame_sequence",
        "selected_" + "candidate_id",
        "selected_" + "observation_id",
        "packet_" + "object_id",
        "provenance",
    }
    assert not (forbidden & set(low))


def test_sanitizer_missing_metadata_has_accurate_count_and_reason() -> None:
    geometry = geometry_document()
    gate = gate_document()
    metadata = metadata_for(geometry)
    metadata.pop(("frame_000000", "book-ok"))
    selected = build_selected_objects_document(
        geometry, gate, metadata, gate_sha256=HASH, metadata_adapter_sha256=HASH
    )
    assert selected["sanitizer"]["dropped_count"] == 1
    assert selected["sanitizer"]["drop_reasons"] == {"metadata_missing": 1}
    assert selected["sanitizer"]["final_count"] == 4


def test_sanitizer_missing_authority_is_an_input_drop() -> None:
    geometry = geometry_document()
    gate_item = next(item for item in gate_document()["objects"] if item["logical_object_id"] == "book-ok")
    identity = ("frame_000000", "book-ok")
    items, sanitizer = sanitize_gate_candidates(
        [gate_item], {}, {identity: metadata_for(geometry)[identity]}
    )
    assert items == []
    assert sanitizer["drop_reasons"] == {"authority_missing": 1}


def test_sanitizer_drops_every_member_of_source_identity_conflict() -> None:
    geometry = geometry_document()
    gate_item = next(item for item in gate_document()["objects"] if item["logical_object_id"] == "book-ok")
    identity = ("frame_000000", "book-ok")
    authority = {identity: [next(item for item in geometry["frames"][0]["objects"] if item["logical_object_id"] == "book-ok")]}
    metadata = {identity: metadata_for(geometry)[identity]}
    items, sanitizer = sanitize_gate_candidates([gate_item, deepcopy(gate_item)], authority, metadata)
    assert items == []
    assert sanitizer["input_count"] == sanitizer["dropped_count"] == 2
    assert sanitizer["drop_reasons"] == {"source_identity_conflict": 2}


def test_sanitizer_drops_every_member_of_object_id_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    geometry = geometry_document()
    gates = [
        item
        for item in gate_document()["objects"]
        if item["logical_object_id"] in {"book-ok", "cup-fail-a"}
    ]
    authority = {
        ("frame_000000", item["logical_object_id"]): [item]
        for item in geometry["frames"][0]["objects"]
        if item["logical_object_id"] in {"book-ok", "cup-fail-a"}
    }
    metadata = {
        identity: rows
        for identity, rows in metadata_for(geometry).items()
        if identity[1] in {"book-ok", "cup-fail-a"}
    }
    monkeypatch.setattr(projection_module, "_stable_object_id", lambda *_: "same-object-id")
    items, sanitizer = sanitize_gate_candidates(gates, authority, metadata)
    assert items == []
    assert sanitizer["drop_reasons"] == {"object_id_conflict": 2}


def test_invalid_frame_id_is_sanitizer_drop_and_sequence_only_appears_in_packet() -> None:
    gate_item = deepcopy(next(item for item in gate_document()["objects"] if item["logical_object_id"] == "book-ok"))
    gate_item["source_frame_id"] = "frame_000011"
    items, sanitizer = sanitize_gate_candidates([gate_item], {}, {})
    assert items == []
    assert sanitizer["drop_reasons"] == {"invalid_source_frame_id": 1}
    selected = selected_document()
    assert all("source_frame_sequence" not in item for item in selected["items"])
    packet_objects = project_selected_items(selected)
    assert all(item["source_frame_sequence"] == 0 for item in packet_objects)
    assert all(item["source_frame_id"] == f"frame_{item['source_frame_sequence']:06d}" for item in packet_objects)


def test_frame_000010_derives_sequence_10_only_at_packet_boundary() -> None:
    geometry = geometry_with(
        [authority_object("book-ten", "book", "ok")],
        frame_id="frame_000010",
        sequence=10,
    )
    gate = build_dual_gate_document(geometry, PROFILE)
    identity = ("frame_000010", "book-ten")
    metadata = {
        identity: [
            {
                "source_frame_id": identity[0],
                "logical_object_id": identity[1],
                "class_name": "book",
                "bbox_xyxy": [10.0, 20.0, 40.0, 80.0],
                "confidence": 0.2,
                "visibility": {"full_extent_visible": True, "touches_border": False},
                "warnings": [],
            }
        ]
    }
    selected = build_selected_objects_document(
        geometry, gate, metadata, gate_sha256=HASH, metadata_adapter_sha256=HASH
    )
    assert "source_frame_sequence" not in selected["items"][0]
    packet_item = project_selected_items(selected)[0]
    assert packet_item["source_frame_sequence"] == 10
    assert packet_item["source_frame_id"] == "frame_000010"


def test_packet_is_exact_one_to_one_projection_with_optional_geometry_confidence() -> None:
    geometry = geometry_document()
    selected = selected_document()
    package = build_p4_package(
        geometry,
        selected,
        selected_objects_sha256=HASH,
        metadata_adapter_sha256=HASH,
        whitelist_profile_sha256=HASH,
    )
    validate_p4_package(package)
    validate_projection_invariants(selected, package)
    assert package["selection_policy_id"] == SELECTION_POLICY_ID
    assert len(package["objects"]) == len(selected["items"]) == 5
    assert sum(item["class_name"] == "cup" for item in package["objects"]) == 2
    forbidden = {
        "logical_object_id",
        "gate1_outcome",
        "selected_" + "candidate_id",
    }
    assert all(not (forbidden & set(item)) for item in package["objects"])
    assert any(
        item["geometry"].get("geometry_confidence") == 0.8
        for item in package["objects"]
    )
    assert any(
        item["geometry"]["valid"] is False
        and item["geometry"]["support_height_mm"] is None
        for item in package["objects"]
    )


def test_old_packet_without_geometry_confidence_remains_valid() -> None:
    geometry = geometry_document()
    package = build_p4_package(
        geometry,
        selected_document(),
        selected_objects_sha256=HASH,
        metadata_adapter_sha256=HASH,
        whitelist_profile_sha256=HASH,
    )
    for item in package["objects"]:
        item["geometry"].pop("geometry_confidence")
    assert validate_p4_package(package) is package


def test_malformed_geometry_confidence_is_rejected() -> None:
    geometry = geometry_document()
    package = build_p4_package(
        geometry,
        selected_document(),
        selected_objects_sha256=HASH,
        metadata_adapter_sha256=HASH,
        whitelist_profile_sha256=HASH,
    )
    package["objects"][0]["geometry"]["geometry_confidence"] = 1.01
    with pytest.raises(ScanContractError, match="geometry_confidence"):
        validate_p4_package(package)


def test_empty_selected_and_empty_packet_objects_are_legal() -> None:
    geometry = geometry_with([authority_object("chair-only", "chair", "ok")])
    gate = build_dual_gate_document(geometry, PROFILE)
    identity = ("frame_000000", "chair-only")
    metadata = {
        identity: [
            {
                "source_frame_id": identity[0],
                "logical_object_id": identity[1],
                "class_name": "chair",
                "bbox_xyxy": [10.0, 20.0, 40.0, 80.0],
                "confidence": 0.2,
                "visibility": {"full_extent_visible": True, "touches_border": False},
                "warnings": [],
            }
        ]
    }
    selected = build_selected_objects_document(
        geometry, gate, metadata, gate_sha256=HASH, metadata_adapter_sha256=HASH
    )
    package = build_p4_package(
        geometry,
        selected,
        selected_objects_sha256=HASH,
        metadata_adapter_sha256=HASH,
        whitelist_profile_sha256=HASH,
    )
    assert selected["items"] == []
    assert package["objects"] == []
    assert validate_p4_package(package) is package


def test_projection_rejects_missing_extra_and_mixed_source() -> None:
    geometry = geometry_document()
    selected = selected_document()
    package = build_p4_package(
        geometry,
        selected,
        selected_objects_sha256=HASH,
        metadata_adapter_sha256=HASH,
        whitelist_profile_sha256=HASH,
    )
    package["objects"][0], package["objects"][1] = package["objects"][1], package["objects"][0]
    with pytest.raises(ScanContractError, match="exact ordered 1:1"):
        validate_projection_invariants(selected, package)


def test_pc_validator_retains_legacy_read_compatibility_only() -> None:
    geometry = geometry_document()
    selected = selected_document()
    package = build_p4_package(
        geometry,
        selected,
        selected_objects_sha256=HASH,
        metadata_adapter_sha256=HASH,
        whitelist_profile_sha256=HASH,
    )
    package["selection_policy_id"] = "one_best_per_class_v1"
    assert validate_p4_package(package) is package


def test_gate_and_selected_are_deterministic() -> None:
    first_gate = gate_document()
    second_gate = gate_document()
    assert canonical_sha256(first_gate) == canonical_sha256(second_gate)
    first_selected = selected_document()
    second_selected = selected_document()
    assert canonical_sha256(first_selected) == canonical_sha256(second_selected)
    assert validate_dual_gate_document(first_gate) is first_gate
    assert validate_selected_objects_document(first_selected) is first_selected
