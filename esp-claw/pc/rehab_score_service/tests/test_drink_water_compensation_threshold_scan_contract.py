from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools import scan_drink_water_compensation_thresholds_v1 as scan


class DrinkWaterCompensationThresholdScanContractTests(unittest.TestCase):
    def _write_fixture(self, root: Path) -> Path:
        input_dir = root / "input"
        input_dir.mkdir()
        path = input_dir / "01_all_40_rep_feature_diagnosis.csv"
        fieldnames = [
            "source_file",
            "source_category",
            "ground_truth_has_compensation",
            "rep_index",
            *scan.SCAN_FEATURES,
            "final_score",
        ]
        rows = []
        for index in range(1, 41):
            is_mixed = 30 <= index <= 33 or index >= 39
            is_comp = index >= 34
            if is_mixed:
                category = "mixed"
            elif is_comp:
                category = "compensation"
            else:
                category = "normal"
            base = 10.0 + index * 0.01
            trunk_ratio = 0.04 + index * 0.0001
            trunk_rom = base
            if is_comp:
                trunk_ratio = 0.15 + index * 0.0001
                trunk_rom = 25.0 + index * 0.01
            row = {
                "source_file": f"source_{index // 3:02d}.csv",
                "source_category": category,
                "ground_truth_has_compensation": str(is_comp),
                "rep_index": str(index),
                "TrunkCompensationRatio": str(trunk_ratio),
                "TrunkROM": str(trunk_rom),
                "imu2_roll_range": str(base),
                "imu2_yaw_range": str(base + 1.0),
                "imu2_gyro_norm_p95": str(base + 2.0),
                "imu2_pitch_positive_excursion": str(base + 3.0),
                "imu2_positive_excursion_duration_ms": str(base + 4.0),
                "imu2_pitch_absolute_excursion": str(base + 5.0),
                "final_score": "1.0",
            }
            rows.append(row)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return input_dir

    def test_01_version_is_fixed(self) -> None:
        self.assertEqual(scan.SCAN_VERSION, "drink_water_compensation_threshold_scan_v1")

    def test_02_scan_features_are_whitelisted(self) -> None:
        self.assertEqual(scan.PRIMARY_FEATURES, ("TrunkCompensationRatio", "TrunkROM"))
        self.assertNotIn("final_score", scan.SCAN_FEATURES)
        self.assertNotIn("ROM_score", scan.SCAN_FEATURES)

    def test_03_threshold_candidates_use_midpoints_and_edges(self) -> None:
        self.assertEqual(scan.threshold_candidates([1.0, 3.0, 5.0]), [0.0, 2.0, 4.0, 6.0])

    def test_04_load_records_enforces_40_33_7_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            records = scan.load_rep_records(self._write_fixture(Path(tmp)))
            self.assertEqual(len(records), 40)
            self.assertEqual(sum(1 for record in records if record.is_compensation), 7)

    def test_05_wrong_label_count_fails(self) -> None:
        records = [
            scan.RepRecord(str(i), "s.csv", "normal", i, False, {"TrunkROM": 1.0})
            for i in range(40)
        ]
        with self.assertRaises(ValueError):
            scan.validate_rep_contract(records)

    def test_06_confusion_matrix_counts_are_correct(self) -> None:
        records = [
            scan.RepRecord("tp", "a.csv", "compensation", 1, True, {"x": 1.0}),
            scan.RepRecord("fn", "b.csv", "compensation", 1, True, {"x": 0.0}),
            scan.RepRecord("fp", "c.csv", "normal", 1, False, {"x": 1.0}),
            scan.RepRecord("tn", "d.csv", "normal", 1, False, {"x": 0.0}),
        ]
        result = scan.evaluate_rule(records, "r", "x >= 1", ["x"], {"x": 1.0}, lambda r: r.features["x"] >= 1.0)
        self.assertEqual((result["TP"], result["FN"], result["FP"], result["TN"]), (1, 1, 1, 1))

    def test_07_and_rule_requires_both_features(self) -> None:
        records = [
            scan.RepRecord("a", "a.csv", "normal", 1, False, {"TrunkCompensationRatio": 2, "TrunkROM": 0}),
            scan.RepRecord("b", "b.csv", "compensation", 1, True, {"TrunkCompensationRatio": 2, "TrunkROM": 2}),
        ]
        rule = scan.ratio_rom_scan(records, "AND")[0]
        self.assertIn("AND", rule["rule_expression"])

    def test_08_or_rule_accepts_either_feature(self) -> None:
        records = [
            scan.RepRecord("a", "a.csv", "normal", 1, False, {"TrunkCompensationRatio": 2, "TrunkROM": 0}),
            scan.RepRecord("b", "b.csv", "compensation", 1, True, {"TrunkCompensationRatio": 0, "TrunkROM": 2}),
        ]
        rule = scan.ratio_rom_scan(records, "OR")[0]
        self.assertIn("OR", rule["rule_expression"])

    def test_09_mixed_compensation_is_counted_separately(self) -> None:
        records = [
            scan.RepRecord("m", "m.csv", "mixed", 2, True, {"x": 1.0}),
            scan.RepRecord("p", "p.csv", "compensation", 1, True, {"x": 1.0}),
        ]
        result = scan.evaluate_rule(records, "r", "x >= 1", ["x"], {"x": 1.0}, lambda _r: True)
        self.assertEqual(result["mixed_compensation_true_positive_count"], 1)
        self.assertEqual(result["pure_compensation_true_positive_count"], 1)

    def test_10_mixed_normal_false_positive_is_counted(self) -> None:
        records = [scan.RepRecord("m", "m.csv", "mixed", 1, False, {"x": 1.0})]
        result = scan.evaluate_rule(records, "r", "x >= 1", ["x"], {"x": 1.0}, lambda _r: True)
        self.assertEqual(result["mixed_normal_false_positive_count"], 1)

    def test_11_safety_candidate_prefers_low_false_positive(self) -> None:
        rules = [
            {"rule_id": "a", "normal_false_positive_count": 2, "recall": 1, "specificity": 0, "feature_names": ["TrunkROM"]},
            {"rule_id": "b", "normal_false_positive_count": 0, "recall": 0, "specificity": 1, "feature_names": ["TrunkROM"]},
        ]
        self.assertEqual(min(rules, key=scan.safety_sort_key)["rule_id"], "b")

    def test_12_recommended_requires_recall_one_and_fp_at_most_one(self) -> None:
        rule = self._rule("good", recall=1.0, fp=1)
        selected = scan.select_candidates([rule])
        self.assertEqual(selected["recommended_candidate"]["rule_id"], "good")

    def test_13_no_recommended_when_contract_not_met(self) -> None:
        selected = scan.select_candidates([self._rule("bad", recall=0.9, fp=0)])
        self.assertIsNone(selected["recommended_candidate"])

    def test_14_manual_decision_is_always_required(self) -> None:
        self.assertTrue(scan.select_candidates([self._rule("good", recall=1.0, fp=0)])["manual_decision_required"])

    def test_15_run_scan_writes_no_production_switch_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "out"
            scan.run_scan(self._write_fixture(root), output)
            summary = json.loads((output / "08_threshold_scan_summary.json").read_text(encoding="utf-8"))
            self.assertFalse(summary["production_detector_modified"])
            self.assertTrue(summary["manual_decision_required"])

    def test_16_input_file_is_not_modified_by_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = self._write_fixture(root)
            before = (input_dir / "01_all_40_rep_feature_diagnosis.csv").read_bytes()
            scan.run_scan(input_dir, root / "out")
            after = (input_dir / "01_all_40_rep_feature_diagnosis.csv").read_bytes()
            self.assertEqual(before, after)

    def test_17_ranked_candidates_include_required_keys(self) -> None:
        selected = scan.select_candidates([self._rule("r", recall=1.0, fp=0)])
        self.assertIn("safety_candidate", selected)
        self.assertIn("balanced_candidate", selected)
        self.assertIn("demo_candidate", selected)
        self.assertIn("recommended_candidate", selected)

    def test_18_leave_one_source_session_out_marks_exploratory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            records = scan.load_rep_records(self._write_fixture(Path(tmp)))
            result = scan.leave_one_source_session_out(records)
            self.assertTrue(result["exploratory_only"])
            self.assertTrue(result["current_dataset_only"])
            self.assertTrue(result["not_clinically_validated"])

    def test_19_error_case_csv_contains_false_positive_and_negative_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "errors.csv"
            rule = self._rule("r", recall=0.5, fp=1)
            rule["false_positive_rep_ids"] = ["fp"]
            rule["false_negative_rep_ids"] = ["fn"]
            scan.write_error_cases(path, {"safety_candidate": rule})
            text = path.read_text(encoding="utf-8-sig")
            self.assertIn("false_positive", text)
            self.assertIn("false_negative", text)

    def test_20_scan_feature_set_does_not_include_emg_or_score(self) -> None:
        forbidden = {"final_score", "speed_score", "ROM_score", "Extensor_digitorum_RMS", "Biceps_brachii_RMS"}
        self.assertFalse(forbidden.intersection(scan.SCAN_FEATURES))

    def _rule(self, rule_id: str, recall: float, fp: int) -> dict[str, object]:
        return {
            "rule_id": rule_id,
            "rule_expression": "TrunkROM >= 1",
            "feature_names": ["TrunkROM"],
            "thresholds": {"TrunkROM": 1.0},
            "TP": 7 if recall == 1.0 else 6,
            "FN": 0 if recall == 1.0 else 1,
            "FP": fp,
            "TN": 33 - fp,
            "recall": recall,
            "precision": 1.0,
            "specificity": 1.0,
            "accuracy": 1.0,
            "f1": recall,
            "balanced_accuracy": recall,
            "normal_false_positive_count": fp,
            "compensation_false_negative_count": 0 if recall == 1.0 else 1,
            "pure_normal_false_positive_count": fp,
            "mixed_normal_false_positive_count": 0,
            "pure_compensation_true_positive_count": 5,
            "mixed_compensation_true_positive_count": 2 if recall == 1.0 else 1,
            "false_positive_rep_ids": [],
            "false_negative_rep_ids": [],
        }


if __name__ == "__main__":
    unittest.main()
