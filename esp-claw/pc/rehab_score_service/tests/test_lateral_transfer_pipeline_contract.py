from __future__ import annotations

import unittest
from pathlib import Path

from feature_extractors.lateral_transfer_profile import (
    EXPECTED_SESSIONS,
    EXCLUDED_TASK_IDS,
    LF,
    LF_EXTRA_PENALTY,
    LF_VERSION,
    MOTION_FAMILY,
    SEGMENTATION_CORE_VERSION,
    SEGMENTATION_VERSION,
    SENSOR_MAPPING_VERSION,
    TEMPLATE_PATH,
    TEMPLATE_VERSION,
    TREMOR,
    TREMOR_PENALTY,
    TREMOR_VERSION,
    DERIVED_ROOT,
    expected_label_counts,
    expected_rep_count,
    expected_session_count,
)


ROOT = Path(__file__).resolve().parents[1]


class LateralTransferDrinkReuseContractTest(unittest.TestCase):
    def test_dataset_contract_counts_and_exclusions(self) -> None:
        self.assertEqual(expected_session_count(), 33)
        self.assertEqual(expected_rep_count(), 61)
        self.assertEqual(expected_label_counts(), {"normal": 50, "lf": 6, "tremor": 5})
        self.assertIn("20260807_TASK036", EXPECTED_SESSIONS)
        self.assertIn("20260807_TASK039", EXPECTED_SESSIONS)
        self.assertEqual(EXCLUDED_TASK_IDS, {"20260807_TASK036", "20260807_TASK039"})

    def test_profile_versions_use_final_drink_reuse_wrapper(self) -> None:
        self.assertEqual(MOTION_FAMILY, "lateral_transfer")
        self.assertEqual(SENSOR_MAPPING_VERSION, "rehab_sensor_mapping_20260806")
        self.assertEqual(SEGMENTATION_VERSION, "lateral_transfer_segmentation_drink_reuse_v1")
        self.assertEqual(SEGMENTATION_CORE_VERSION, "drink_hybrid_segmentation_v2_1_1")
        self.assertEqual(TEMPLATE_VERSION, "lateral_transfer_healthy_template_v1")
        self.assertEqual(LF_VERSION, "drink_water_lf_v2")
        self.assertEqual(TREMOR_VERSION, "drink_water_tremor_v1_1")
        self.assertEqual(TEMPLATE_PATH.name, "lateral_transfer_healthy_template_v1.json")

    def test_final_lateral_profile_uses_approved_parameters(self) -> None:
        self.assertEqual(TREMOR.frequency_band_hz, (3.5, 6.0))
        self.assertEqual(TREMOR.window_seconds, 2.0)
        self.assertEqual(TREMOR.step_seconds, 0.5)
        self.assertEqual(TREMOR.band_ratio_threshold, 0.18)
        self.assertEqual(TREMOR.band_rms_threshold, 5.0)
        self.assertEqual(TREMOR.fraction_threshold, 0.35)
        self.assertEqual(LF.threshold_deg, 10.0)
        self.assertEqual(LF.min_duration_s, 1.0)

    def test_wrapper_directly_calls_frozen_drink_authority(self) -> None:
        source = (ROOT / "tools" / "lateral_transfer_segmentation_v1.py").read_text(encoding="utf-8")
        self.assertIn("from tools import drink_water_v1_pipeline as drink_pipeline", source)
        self.assertIn("drink_pipeline.segment_file(csv_path)", source)
        self.assertIn("drink_pipeline.make_json_safe", source)
        self.assertIn("LATERAL_SEGMENTATION_STRATEGY = \"REUSE_DRINK_HYBRID_SEGMENTATION_V2_1_1\"", source)

    def test_wrapper_has_no_independent_split_algorithm(self) -> None:
        source = (ROOT / "tools" / "lateral_transfer_segmentation_v1.py").read_text(encoding="utf-8")
        for token in (
            "detect_lateral_transfer_cycles_from_frame",
            "_motion_envelope",
            "active_bout",
            "STATE_RETURN_CANDIDATE",
            "dynamic_per_cycle_neutral",
            "return_candidate",
            "major_active_bout",
        ):
            self.assertNotIn(token, source)

    def test_production_segmenter_does_not_reference_expected_or_tasks(self) -> None:
        source = (ROOT / "tools" / "lateral_transfer_segmentation_v1.py").read_text(encoding="utf-8")
        self.assertNotIn("EXPECTED_SESSIONS", source)
        self.assertNotIn("expected_rep_count", source)
        self.assertNotIn("20260807_TASK", source)

    def test_downstream_tools_use_shared_wrapper(self) -> None:
        for filename in (
            "lateral_transfer_segmentation_analysis.py",
            "lateral_transfer_segmentation_gate.py",
            "build_lateral_transfer_healthy_template_v1.py",
            "lateral_transfer_lf_calibration_v1.py",
            "lateral_transfer_tremor_calibration_v1.py",
            "lateral_transfer_session_scoring_v1.py",
            "lateral_transfer_final_detector_regression_v1.py",
        ):
            source = (ROOT / "tools" / filename).read_text(encoding="utf-8")
            self.assertIn("segment_lateral_transfer_file", source, filename)

    def test_lateral_detectors_are_thin_drink_authority_adapters(self) -> None:
        source = (ROOT / "feature_extractors" / "lateral_transfer_detectors.py").read_text(encoding="utf-8")
        self.assertIn("from feature_extractors import lf_detector, tremor_detector", source)
        self.assertIn("lf_detector.detect_drink_water_lf_from_rows(frame)", source)
        self.assertIn("tremor_detector.detect_drink_water_tremor_from_rows(frame)", source)
        self.assertIn("TREMOR_BAND_RMS_THRESHOLD = TREMOR.band_rms_threshold", source)
        self.assertIn("TREMOR_FRACTION_THRESHOLD = TREMOR.fraction_threshold", source)
        self.assertNotIn("np.fft.rfft", source)
        self.assertNotIn("np.unwrap", source)

    def test_raw_pool_gate_is_separate_from_approved_manifest(self) -> None:
        gate = (ROOT / "tools" / "lateral_transfer_segmentation_gate.py").read_text(encoding="utf-8")
        self.assertIn("evaluate_raw_pool_counts", gate)
        self.assertIn("raw_pool_evaluation", gate)
        self.assertIn("blocks_calibration", gate)
        self.assertIn("APPROVED_CALIBRATION_MANIFEST_MISSING", gate)
        self.assertIn("approved_for_calibration", gate)
        self.assertIn("APPROVED_SESSION_REP_COUNT_MISMATCH", gate)
        self.assertIn("actual_count != expected_count", gate)
        self.assertIn("approved_count_validation_passed", gate)

    def test_calibration_script_does_not_hard_block_on_raw_33_of_33(self) -> None:
        source = (ROOT / "tools" / "lateral_transfer_calibration_v1.ps1").read_text(encoding="utf-8")
        self.assertIn("RAW_POOL_BLOCKS_CALIBRATION=False", source)
        self.assertIn("APPROVED_CALIBRATION_MANIFEST_REQUIRED=True", source)
        self.assertNotIn("SEGMENTATION_NOT_READY", source)
        self.assertNotIn("matched_session_count -ne 33", source)

    def test_real_data_validation_prints_wrapper_and_core_versions(self) -> None:
        source = (ROOT / "tools" / "lateral_transfer_real_data_validation_v1.ps1").read_text(encoding="utf-8")
        self.assertIn("LATERAL_WRAPPER_VERSION=lateral_transfer_segmentation_drink_reuse_v1", source)
        self.assertIn("SEGMENTATION_CORE_VERSION=drink_hybrid_segmentation_v2_1_1", source)
        self.assertNotIn("lateral_transfer_segmentation_v1_3_active_bout", source)

    def test_final_validation_uses_approved_manifest_not_raw_33_pool(self) -> None:
        source = (ROOT / "tools" / "lateral_transfer_real_data_validation_v1.ps1").read_text(encoding="utf-8")
        self.assertIn("APPROVED_FINAL_VALIDATION", source)
        self.assertIn("approved_calibration_manifest", source)
        self.assertIn("APPROVED_EXPECTED_REP_TOTAL", source)
        self.assertIn("ACTUAL_SCORE_REP_TOTAL", source)
        self.assertNotIn("REP_COUNT_TOTAL=61", source)
        self.assertNotIn("20260807_TASK024", source)

    def test_approved_manifest_excludes_only_user_approved_mismatches(self) -> None:
        manifest_path = DERIVED_ROOT / "approved_calibration_manifest" / "lateral_transfer_approved_calibration_manifest.json"
        source = manifest_path.read_text(encoding="utf-8")
        self.assertIn('"approval_status": "APPROVED_FOR_LATERAL_FINALIZATION"', source)
        self.assertIn('"approved_session_count": 31', source)
        self.assertIn('"approved_rep_count": 59', source)
        self.assertIn('"healthy_normal_rep_count": 48', source)
        self.assertIn('"session_id": "20260807_TASK036"', source)
        self.assertIn('"session_id": "20260807_TASK039"', source)
        self.assertIn('"exclusion_reason": "segmentation_count_mismatch_raw_pool"', source)
        self.assertIn('"manual_review_status": "excluded_from_calibration_by_user"', source)

    def test_segmentation_gate_enforces_final_approved_counts(self) -> None:
        source = (ROOT / "tools" / "lateral_transfer_segmentation_gate.py").read_text(encoding="utf-8")
        self.assertIn("APPROVED_CALIBRATION_MANIFEST_NOT_FINAL", source)
        self.assertIn("APPROVED_SESSION_COUNT_MISMATCH:expected=31", source)
        self.assertIn("APPROVED_REP_TOTAL_MISMATCH:expected=59", source)
        self.assertIn('"normal": 48, "lf": 6, "tremor": 5', source)
        self.assertIn("APPROVED_EXCLUSION_REASON_MISMATCH", source)

    def test_healthy_template_uses_manual_normal_labels_only(self) -> None:
        source = (ROOT / "tools" / "build_lateral_transfer_healthy_template_v1.py").read_text(encoding="utf-8")
        self.assertIn('if expected_label != "normal"', source)
        self.assertIn('reason": f"label={expected_label}"', source)
        self.assertIn("healthy_normal_rep_count_mismatch:expected=48", source)
        self.assertIn('"approved_session_count": gate["approved_session_count"]', source)
        self.assertIn('"healthy_normal_rep_count": len(records)', source)
        self.assertNotIn("detect_lateral_transfer_lf", source)
        self.assertNotIn("detect_lateral_transfer_tremor", source)

    def test_exact_reuse_evaluator_uses_drink_lf_and_tremor_authorities(self) -> None:
        source = (ROOT / "tools" / "lateral_transfer_drink_lf_tremor_reuse_evaluation.py").read_text(encoding="utf-8")
        self.assertIn("from feature_extractors.lf_detector import DRINK_WATER_LF_VERSION, detect_drink_water_lf_from_rows", source)
        self.assertIn("from feature_extractors.tremor_detector import TREMOR_DETECTOR_VERSION, detect_drink_water_tremor_from_rows", source)
        self.assertIn("segment_lateral_transfer_file(csv_path)", source)
        self.assertIn("detect_drink_water_lf_from_rows(rep_df)", source)
        self.assertIn("detect_drink_water_tremor_from_rows(rep_df)", source)
        self.assertNotIn("detect_lateral_transfer_lf", source)
        self.assertNotIn("detect_lateral_transfer_tremor", source)

    def test_exact_reuse_evaluator_classifies_only_matched_sessions(self) -> None:
        source = (ROOT / "tools" / "lateral_transfer_drink_lf_tremor_reuse_evaluation.py").read_text(encoding="utf-8")
        self.assertIn('reason": "SEGMENTATION_EXCLUDED_FROM_CLASSIFICATION"', source)
        self.assertIn("len(valid_reps) != len(labels)", source)
        self.assertIn("segmentation_skipped_sessions.csv", source)
        self.assertIn("reuse_per_rep.csv", source)

    def test_pretest_gate_does_not_use_exit_to_close_host(self) -> None:
        delivery_script = (ROOT / "tools" / "lateral_transfer_final_pretest_gate_template.ps1")
        if delivery_script.exists():
            source = delivery_script.read_text(encoding="utf-8")
            self.assertNotIn("exit $LASTEXITCODE", source)

    def test_penalty_contract_is_product_consistent(self) -> None:
        self.assertEqual(LF_EXTRA_PENALTY, 10.0)
        self.assertEqual(TREMOR_PENALTY, 15.0)
        self.assertEqual(LF_EXTRA_PENALTY + TREMOR_PENALTY, 25.0)

    def test_final_validation_locks_detector_regression_and_penalty_contract(self) -> None:
        source = (ROOT / "tools" / "lateral_transfer_real_data_validation_v1.ps1").read_text(encoding="utf-8")
        self.assertIn("TREMOR_TP=$tremorTp", source)
        self.assertIn("LF_TP=$lfTp", source)
        self.assertIn("FINAL_DETECTOR_REGRESSION=FAIL:TREMOR", source)
        self.assertIn("FINAL_DETECTOR_REGRESSION=FAIL:LF", source)
        self.assertIn("FINAL_DETECTOR_REGRESSION=PASS", source)
        self.assertIn("PENALTY_CONSISTENCY=PASS", source)
        self.assertIn("SCORE_CONTRACT=PASS", source)
        self.assertIn('Write-ScoreStats -Rows $rows -Label "normal" -Prefix "NORMAL"', source)
        self.assertIn("SCORE_MEAN", source)
        self.assertIn('Write-ScoreStats -Rows $rows -Label "tremor" -Prefix "TREMOR"', source)
        self.assertIn("SCORE_MEDIAN", source)

    def test_final_detector_regression_tool_uses_lateral_profile_and_expected_confusion(self) -> None:
        source = (ROOT / "tools" / "lateral_transfer_final_detector_regression_v1.py").read_text(encoding="utf-8")
        self.assertIn("assert_lateral_segmentation_ready", source)
        self.assertIn("detect_lateral_transfer_lf(rep_df)", source)
        self.assertIn("detect_lateral_transfer_tremor(rep_df)", source)
        self.assertIn('"expected_tremor_confusion": {"TP": 5, "FN": 0, "FP": 0, "TN": 54}', source)
        self.assertIn('"expected_lf_confusion": {"TP": 6, "FN": 0, "FP": 2, "TN": 51}', source)
        self.assertIn("FINAL_DETECTOR_REGRESSION", source)

    def test_drink_production_files_are_not_targeted_by_lateral_wrapper(self) -> None:
        for path in (
            ROOT / "feature_extractors" / "lateral_transfer_profile.py",
            ROOT / "tools" / "lateral_transfer_segmentation_v1.py",
            ROOT / "tools" / "lateral_transfer_lf_calibration_v1.py",
            ROOT / "tools" / "lateral_transfer_tremor_calibration_v1.py",
        ):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("DRINK_WATER_LF_VERSION =", source)
            self.assertNotIn("DRINK_WATER_TREMOR_VERSION =", source)


if __name__ == "__main__":
    unittest.main()

