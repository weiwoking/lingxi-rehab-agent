from __future__ import annotations

import json
from pathlib import Path

from deltar_p4_pipeline.dual_gate_selection import (
    SELECTION_POLICY_ID,
    build_dual_gate_document,
)
from deltar_p4_pipeline.scene_candidate_contracts import object_role_for_class
from deltar_p4_pipeline.selected_object_projection import (
    build_selected_objects_document,
)
from deltar_p4_pipeline.stage8b4a_contracts import (
    WHITELIST_CLASSES,
    validate_p4_package,
)
from deltar_p4_pipeline.stage8b4a_package import build_p4_package
from tests.test_dual_gate_selection import HASH, authority_object, geometry_with


ROOT = Path(__file__).resolve().parents[1]
PROFILE = {
    "schema_version": "stage8b_p4_object_whitelist.v1",
    "profile_id": "sports_ball_test_whitelist",
    "selection_policy_id": SELECTION_POLICY_ID,
    "classes": ["book", "cup", "bottle", "sports ball"],
}


def test_sports_ball_flows_gate2_selected_package_and_json_schema() -> None:
    geometry = geometry_with([
        authority_object("ball-ok", "sports ball", "success", confidence=0.265833944)
    ])
    gate = build_dual_gate_document(
        geometry, PROFILE, geometry_sha256=HASH, whitelist_sha256=HASH
    )
    ball_gate = gate["objects"][0]
    assert ball_gate["gate1_passed"] is True
    assert ball_gate["gate2_passed"] is True

    identity = ("frame_000000", "ball-ok")
    metadata = {
        identity: [{
            "source_frame_id": identity[0],
            "logical_object_id": identity[1],
            "class_name": "sports ball",
            "bbox_xyxy": [195.0, 173.0, 321.0, 300.0],
            "confidence": 0.265833944,
            "visibility": {"full_extent_visible": True, "touches_border": False},
            "warnings": [],
        }]
    }
    selected = build_selected_objects_document(
        geometry,
        gate,
        metadata,
        gate_sha256=HASH,
        metadata_adapter_sha256=HASH,
    )
    assert [item["class_name"] for item in selected["items"]] == ["sports ball"]

    package = build_p4_package(
        geometry,
        selected,
        selected_objects_sha256=HASH,
        metadata_adapter_sha256=HASH,
        whitelist_profile_sha256=HASH,
    )
    assert WHITELIST_CLASSES == ("cup", "bottle", "book", "sports ball")
    assert package["objects"][0]["class_name"] == "sports ball"
    assert package["objects"][0]["display_name_zh"] == "运动球"
    assert package["class_summaries"][3]["class_name"] == "sports ball"
    assert package["class_summaries"][3]["present"] is True
    assert validate_p4_package(package) is package

    package_schema = json.loads(
        (ROOT / "schemas" / "stage8b4a_p4_decision_package.v1.schema.json").read_text(encoding="utf-8")
    )
    assert package_schema["properties"]["whitelist"]["properties"]["classes"]["const"] == list(WHITELIST_CLASSES)
    assert package_schema["$defs"]["className"]["enum"] == list(WHITELIST_CLASSES)
    assert package_schema["properties"]["class_summaries"]["minItems"] == 4
    assert "sports ball" in package_schema["$defs"]["packageObject"]["properties"]["object_id"]["pattern"]


def test_sports_ball_is_an_operable_scene_candidate() -> None:
    assert object_role_for_class("sports ball") == "operable_object"


def test_formal_whitelist_profile_and_schema_agree() -> None:
    profile = json.loads(
        (ROOT / "profiles" / "stage8b_p4_object_whitelist.json").read_text(encoding="utf-8")
    )
    schema = json.loads(
        (ROOT / "schemas" / "stage8b_p4_object_whitelist.v1.schema.json").read_text(encoding="utf-8")
    )
    assert profile["classes"] == ["book", "cup", "bottle", "sports ball"]
    assert schema["properties"]["classes"]["const"] == profile["classes"]


def test_existing_valid_package_vector_matches_expanded_v1_contract() -> None:
    vector = json.loads(
        (ROOT / "tests" / "contract_vectors" / "stage8b4a_p4_same_class_multiple.valid.json").read_text(encoding="utf-8")
    )
    assert validate_p4_package(vector) is vector
