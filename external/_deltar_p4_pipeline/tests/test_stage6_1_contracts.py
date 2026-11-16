import copy
import json
from pathlib import Path

import pytest

from deltar_p4_pipeline.scan_contracts import ScanContractError, validate_result_shape
from deltar_p4_pipeline.stage6_schema import validate_job_report_v1_1, validate_scan_result_v1_1
from scripts.run_stage6_1_acceptance import _formal_case

ROOT = Path(__file__).resolve().parents[1]
REAL_RESULT = Path(r"C:\ec\_deltar_stage6_1_probe_22f2b160e37d494998bd127afab4665d")


def test_c11_matched_detection_fallback_is_retained_and_used():
    case = _formal_case("pytest_c11", segmentation_valid=False)
    assert case["group"]["segmentation_primary"]["available"]
    assert case["group"]["detection_fallback"]["available"]
    assert case["formal"]["selected_source"] == "matched_detection_fallback"
    assert case["formal"]["fallback_used"] and case["observation"]["distance_m"] is not None


def test_c12_segmentation_success_keeps_matched_detection_as_provenance_only():
    case = _formal_case("pytest_c12")
    assert case["formal"]["selected_source"] == "segmentation"
    assert not case["formal"]["fallback_used"]
    assert case["group"]["detection_fallback"]["available"]


def test_c13_formal_3d_disagreement_fails_closed():
    result = _formal_case("pytest_c13", letterbox_mode="disagree")["formal"]
    assert result["canvas_selection_status"] == "disagreed"
    assert result["selected_geometry"] is None and not result["absolute_geometry_valid"]


def test_c14_measured_but_unproven_is_not_absolute():
    result = _formal_case("pytest_c14", calibration_status="measured", semantics_status="PLAUSIBLE_BUT_UNPROVEN")["formal"]
    assert not result["absolute_geometry_valid"]


def test_c15_single_canvas_is_provisional_and_not_absolute():
    result = _formal_case("pytest_c15", letterbox_mode="missing")["formal"]
    assert result["single_canvas_only"] and result["selection_is_provisional"]
    assert not result["absolute_geometry_valid"]


def test_c16_eroded_region_is_byte_exact_between_stage3_and_stage4():
    items = [item for item in _formal_case("pytest_c16")["region"] if item["source"] == "segmentation"]
    assert items and all(item["stage3_stage4_region_exact_equal"] for item in items)
    assert all(item["frozen_region_sha256"] == item["formal_region_sha256"] for item in items)


@pytest.mark.skipif(not (REAL_RESULT / "result.json").is_file(), reason="real v1.1 probe is not retained")
@pytest.mark.parametrize("kind,path", [
    ("result", ["objects", 0, "distance", "valid"]),
    ("result", ["objects", 0, "visibility", "status"]),
    ("result", ["objects", 0, "visible_height", "absolute_valid"]),
    ("result", ["objects", 0, "provenance", "model_hashes", "detect"]),
    ("job", ["exit_code"]),
])
def test_c17_nested_required_fields_are_rejected(kind, path):
    filename = "result.json" if kind == "result" else "job_report.json"
    value = json.loads((REAL_RESULT / filename).read_text(encoding="utf-8"))
    target = value
    for part in path[:-1]:
        target = target[part]
    del target[path[-1]]
    with pytest.raises(ScanContractError):
        (validate_scan_result_v1_1 if kind == "result" else validate_job_report_v1_1)(value)


def test_c18_legacy_v1_is_inspect_only_not_v1_1():
    legacy = {"schema_version": "deltar_scan_result.v1", "status": "success", "scan_id": "legacy",
              "job_id": "job", "objects": [], "warnings": [], "errors": []}
    validate_result_shape(copy.deepcopy(legacy))
    with pytest.raises(ScanContractError):
        validate_scan_result_v1_1(legacy)
