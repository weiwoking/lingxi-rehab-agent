from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from deltar_p4_pipeline.object_depth import evaluate_object_depth
from deltar_p4_pipeline.stage3_1 import (
    aggregate_method_rows,
    frozen_candidate,
    frozen_canvas_delta,
    same_region_same_statistic,
    valid_aware_resize_depth,
)

ROOT = Path(__file__).parents[1]
OUT = ROOT / "outputs_stage3_1"


def load(name: str):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def test_valid_aware_bilinear_does_not_dilute_depth_with_zero():
    source = np.zeros((4, 4), np.float32); source[:, :2] = 2.0
    resized, valid = valid_aware_resize_depth(source, "bilinear", (8, 8))
    assert np.allclose(resized[valid], 2.0) and not np.any(resized[~valid])


def test_all_gt_resize_methods_have_source_shape():
    source = np.ones((480, 640), np.float32)
    for method in ("bilinear", "nearest", "area"):
        resized, valid = valid_aware_resize_depth(source, method)
        assert resized.shape == valid.shape == (360, 640) and valid.all()


def test_same_region_same_statistic_uses_exact_method():
    pred, _ = evaluate_object_depth([0, 0, 20, 20], np.full((360, 640), 2.0))
    gt, _ = evaluate_object_depth([0, 0, 20, 20], np.full((360, 640), 1.5))
    result = same_region_same_statistic(pred, gt, "bbox_center", "trimmed_mean")
    assert result["region_method"] == "bbox_center" and result["statistic_method"] == "trimmed_mean"
    assert result["absolute_error_m"] == 0.5


def test_frozen_candidate_reads_configuration():
    results, _ = evaluate_object_depth([0, 0, 20, 20], np.ones((360, 640)))
    candidate = frozen_candidate(results, {"region_method": "bbox_full", "statistic_method": "median"})
    assert candidate["region_method"] == "bbox_full" and candidate["statistic_method"] == "median"


def test_frozen_canvas_delta_uses_configured_method():
    direct, _ = evaluate_object_depth([0, 0, 20, 20], np.ones((360, 640)))
    letter, _ = evaluate_object_depth([0, 0, 20, 20], np.full((360, 640), 1.25))
    method = {"region_method": "bbox_full", "statistic_method": "median"}
    delta = frozen_canvas_delta(direct, letter, method)
    assert delta["absolute_delta_m"] == 0.25
    assert delta["direct_candidate"]["statistic_method"] == "median"


def test_aggregate_reports_rejections_in_denominator():
    rows = [{"region_method": "bbox_full", "statistic_method": "median", "object_id": "a", "canvas_strategy": "direct_640x360",
             "status": "valid", "absolute_error_m": 0.1, "predicted_surface_depth_m": 1.0, "small_object": False, "multimodal": False}]
    matrix = aggregate_method_rows(rows, object_count=1)
    assert matrix[0]["expected_canvas_cases"] == 2 and matrix[0]["coverage"] == 0.5 and matrix[0]["rejected_count"] == 1


def test_object_sets_are_complete_and_independent():
    manifest = load("object_set_manifest.json")
    assert (manifest["detection_native_count"], manifest["segmentation_native_count"], manifest["matched_pair_count"]) == (46, 36, 21)
    assert (manifest["unmatched_detection_count"], manifest["unmatched_segmentation_count"]) == (25, 15)
    assert manifest["sets_are_independent"] and manifest["matched_set_use"].startswith("comparison")


def test_native_object_ids_unique_and_sources_traceable():
    objects = load("object_set_manifest.json")["objects"]
    assert len({item["object_id"] for item in objects}) == 82
    assert {item["model_source"] for item in objects} == {"detect", "segment"}


def test_official_144_unmatched_cup_bottle_not_dropped():
    objects = [item for item in load("object_set_manifest.json")["objects"] if item["sample_id"] == "official_144"]
    names = {(item["class_name"], item["model_source"], item["match_status"]) for item in objects}
    assert ("cup", "detect", "unmatched_detect") in names
    assert ("bottle", "detect", "unmatched_detect") in names


def test_segmentation_matrix_uses_all_36_objects():
    matrix = load("segmentation_method_matrix.json")
    assert matrix["object_set"] == "segmentation-native" and matrix["object_count"] == 36
    assert all(row["expected_canvas_cases"] == 72 and row["region_method"].startswith("segment") for row in matrix["methods"])


def test_detection_matrix_uses_all_46_objects():
    matrix = load("detection_method_matrix.json")
    assert matrix["object_set"] == "detection-native" and matrix["object_count"] == 46
    assert all(row["expected_canvas_cases"] == 92 and row["region_method"].startswith("bbox") for row in matrix["methods"])


def test_matched_set_only_contains_21_pairs():
    matrix = load("matched_comparison_matrix.json")
    assert matrix["pair_count"] == 21 and "comparison only" in matrix["use"]


def test_gt_resize_sensitivity_has_three_methods_and_no_significant_flip():
    result = load("gt_resize_sensitivity.json")
    assert result["methods"] == ["bilinear", "nearest", "area"]
    assert result["significant_flip"] is False


def test_recommended_candidates_match_global_frozen_methods():
    frozen = load("frozen_method_selection.json")
    for sample in ("official_144", "official_004", "official_007"):
        result = load(f"{sample}/stage3_1_object_depth_results.json")
        for route, method_key in (("segmentation_native", "segmentation_primary"), ("detection_native", "detection_fallback")):
            method = frozen[method_key]
            for obj in result["object_sets"][route]:
                for candidate in obj["recommended_candidate"].values():
                    assert (candidate["region_method"], candidate["statistic_method"]) == (method["region_method"], method["statistic_method"])


def test_direct_letterbox_delta_contains_frozen_candidates():
    result = load("official_144/stage3_1_object_depth_results.json")
    for route in ("segmentation_native", "detection_native"):
        for obj in result["object_sets"][route]:
            delta = obj["direct_letterbox_delta"]
            assert {"direct_candidate", "letterbox_candidate", "absolute_delta_m", "relative_delta", "agreement_status"} <= set(delta)


def test_stage4_contract_fields_present():
    result = load("official_144/stage3_1_object_depth_results.json")
    valid = next(item for item in result["stage4_inputs"] if item.get("status") != "rejected")
    required = {"schema_version", "sample_id", "object_id", "class_id", "class_name", "class_confidence", "object_source",
                "bbox_xyxy", "mask_key", "canvas_strategy", "region_method", "statistic_method", "surface_depth_m",
                "pixel_count", "valid_pixel_count", "valid_ratio", "mad_m", "iqr_m", "dominant_peak_support", "multimodal",
                "direct_letterbox_delta_m", "warnings", "rejection_reason", "provenance"}
    assert required <= set(valid) and valid["schema_version"] == "deltar_stage4_object_input.v1"


def test_stage3_1_schema_and_gt_is_evaluation_only():
    result = load("official_004/stage3_1_object_depth_results.json")
    assert result["schema_version"] == "deltar_stage3_1_object_depth.v1"
    assert result["gt_used_for_inference"] is False
