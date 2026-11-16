from __future__ import annotations

import unittest
from pathlib import Path

from feature_extractors.lateral_transfer_profile import (
    EXPECTED_SESSIONS,
    EXCLUDED_TASK_IDS,
    expected_rep_count,
    expected_session_count,
)
from tools import lateral_transfer_drink_segmentation_reuse_evaluation as evaluator


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR_SOURCE = ROOT / "tools" / "lateral_transfer_drink_segmentation_reuse_evaluation.py"


class LateralDrinkReuseEvaluatorContractTest(unittest.TestCase):
    def test_evaluator_uses_frozen_drink_segmentation_import(self) -> None:
        source = EVALUATOR_SOURCE.read_text(encoding="utf-8")
        self.assertIn("from tools import drink_water_v1_pipeline as drink_pipeline", source)
        self.assertIn("drink_pipeline.segment_file(csv_path)", source)

    def test_evaluator_does_not_import_lateral_segmenter(self) -> None:
        source = EVALUATOR_SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("lateral_transfer_segmentation_v1 import", source)
        self.assertNotIn("segment_lateral_transfer_file", source)

    def test_expected_count_is_not_passed_to_segmenter(self) -> None:
        source = EVALUATOR_SOURCE.read_text(encoding="utf-8")
        call_line = [
            line.strip()
            for line in source.splitlines()
            if "drink_pipeline.segment_file" in line
        ][0]
        self.assertEqual(call_line, "repetitions, diagnostic, _df = drink_pipeline.segment_file(csv_path)")

    def test_task_id_does_not_control_segmentation_call(self) -> None:
        source = EVALUATOR_SOURCE.read_text(encoding="utf-8")
        self.assertIn("for session_id, labels in EXPECTED_SESSIONS.items()", source)
        self.assertIn("csv_path = source_csv_for_session(session_id)", source)
        self.assertIn("drink_pipeline.segment_file(csv_path)", source)
        self.assertNotIn("if session_id", source)

    def test_no_source_csv_mutation_policy(self) -> None:
        source = EVALUATOR_SOURCE.read_text(encoding="utf-8")
        self.assertNotIn(".to_csv(csv_path", source)
        self.assertNotIn("open(csv_path", source)
        self.assertNotIn("write_text(csv_path", source)

    def test_no_fabricated_data_operations(self) -> None:
        source = EVALUATOR_SOURCE.read_text(encoding="utf-8")
        for token in ("zero_fill", "fillna", "interpolate", "ffill", "bfill", "repeat-last", "repeat_last"):
            self.assertNotIn(token, source)

    def test_dataset_contract_is_33_sessions_61_reps(self) -> None:
        self.assertEqual(expected_session_count(), 33)
        self.assertEqual(expected_rep_count(), 61)
        self.assertEqual(len(EXPECTED_SESSIONS), 33)

    def test_excluded_sessions_are_not_in_evaluation_contract(self) -> None:
        self.assertEqual(EXCLUDED_TASK_IDS, {"20260807_TASK044", "20260807_TASK051"})
        self.assertNotIn("20260807_TASK044", EXPECTED_SESSIONS)
        self.assertNotIn("20260807_TASK051", EXPECTED_SESSIONS)
        self.assertNotIn("20260807_TASK059", EXPECTED_SESSIONS)

    def test_output_schema_names_are_fixed(self) -> None:
        source = EVALUATOR_SOURCE.read_text(encoding="utf-8")
        for token in (
            "drink_reuse_summary.json",
            "drink_reuse_sessions.csv",
            "drink_reuse_repetitions.csv",
            "drink_reuse_mismatch.csv",
        ):
            self.assertIn(token, source)

    def test_group_summary_contract(self) -> None:
        self.assertEqual(evaluator.session_group("20260807_TASK024"), "normal_single")
        self.assertEqual(evaluator.session_group("20260807_TASK040"), "normal_3")
        self.assertEqual(evaluator.session_group("20260807_TASK045"), "normal_5")
        self.assertEqual(evaluator.session_group("20260807_TASK047"), "mixed_normal_lf_normal")
        self.assertEqual(evaluator.session_group("20260807_TASK054"), "tremor_single")

    def test_no_parameter_sweep_or_calibration(self) -> None:
        source = EVALUATOR_SOURCE.read_text(encoding="utf-8")
        self.assertIn('"parameter_sweep": False', source)
        self.assertNotIn("grid", source.lower())
        self.assertNotIn("calibration", source.lower())


if __name__ == "__main__":
    unittest.main()
