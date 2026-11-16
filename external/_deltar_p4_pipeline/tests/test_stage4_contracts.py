import json
from pathlib import Path

import pytest

from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.stage4_contracts import validate_input_manifest, validate_output

ROOT = Path(__file__).resolve().parents[1]


def test_real_generated_input_manifest_contract():
    manifest = json.loads((ROOT / "outputs_stage4" / "input_groups_manifest.json").read_text())
    assert validate_input_manifest(manifest)["counts"] == {"samples": 15, "groups": 61, "matched": 21, "unmatched_segmentation": 15, "unmatched_detection": 25}


def test_matched_detection_fallback_retained():
    manifest = json.loads((ROOT / "outputs_stage4" / "input_groups_manifest.json").read_text())
    matched = [g for g in manifest["object_groups"] if g["match"]["status"] == "matched"]
    assert len(matched) == 21 and all(g["segmentation_primary"]["available"] and g["detection_fallback"]["available"] for g in matched)


def test_both_canvas_depth_refs_present():
    manifest = json.loads((ROOT / "outputs_stage4" / "input_groups_manifest.json").read_text())
    assert all(set(g["source_depth_refs"]) == {"direct_640x360", "letterbox_640x480"} for g in manifest["object_groups"])


def test_bad_manifest_rejected():
    with pytest.raises(PipelineError):
        validate_input_manifest({"schema_version": "bad"})


def test_output_schema():
    assert validate_output({"schema_version": "deltar_stage4_geometry.v1.1", "status": "success", "objects": []})
