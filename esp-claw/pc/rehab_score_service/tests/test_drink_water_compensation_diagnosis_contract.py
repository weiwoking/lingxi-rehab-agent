from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from tools import diagnose_drink_water_compensation_features_v1 as diag


class CompensationDiagnosisV11ContractTests(unittest.TestCase):
    def test_01_version_is_v1_1(self) -> None:
        self.assertEqual(diag.DIAGNOSIS_VERSION, "drink_water_compensation_diagnosis_v1_1")

    def test_02_sensor_mapping_is_authoritative(self) -> None:
        self.assertEqual(diag.SENSOR_MAPPING["imu1"], "upper_arm")
        self.assertEqual(diag.SENSOR_MAPPING["imu2"], "trunk_waist_back")
        self.assertEqual(diag.SENSOR_MAPPING["imu3"], "forearm")
        self.assertEqual(diag.SENSOR_MAPPING["imu4"], "wrist")

    def test_03_trial_feature_map_uses_real_source_fields(self) -> None:
        self.assertEqual(diag.TRIAL_FEATURE_MAP["WristMeanSpeed"], "WristMeanSpeed_mm_s")
        self.assertEqual(diag.TRIAL_FEATURE_MAP["ForearmRotation"], "ForearmRotationRange_deg")
        self.assertEqual(diag.TRIAL_FEATURE_MAP["UpperArmROM"], "UpperArmROM_mm")
        self.assertEqual(diag.TRIAL_FEATURE_MAP["TrunkROM"], "TrunkROM_deg")

    def test_04_no_silent_short_feature_name_for_forearm(self) -> None:
        self.assertNotIn("ForearmRotation", diag.REQUIRED_TRIAL_DIAGNOSTIC_FIELDS)

    def test_05_real_emg_names_are_required(self) -> None:
        self.assertIn("Extensor_digitorum_RMS", diag.EMG_TRIAL_FEATURES)
        self.assertIn("Biceps_brachii_MAV", diag.EMG_TRIAL_FEATURES)
        self.assertIn("Triceps_brachii_WL_mean", diag.EMG_TRIAL_FEATURES)
        self.assertIn("Extensor_carpi_ulnaris_Peak", diag.EMG_TRIAL_FEATURES)

    def test_06_labels_do_not_depend_on_scores(self) -> None:
        self.assertEqual(diag.classify_expected_label("normal_compensation_normal", 2)[1], "forward_trunk_compensation")
        self.assertEqual(diag.classify_expected_label("normal_compensation_normal", 3)[1], "normal")

    def test_07_pure_compensation_label(self) -> None:
        category, label, has_comp, _ = diag.classify_expected_label("forward_compensation_single", 1)
        self.assertEqual(category, "compensation")
        self.assertEqual(label, "forward_trunk_compensation")
        self.assertTrue(has_comp)

    def test_08_require_trial_feature_missing_fails(self) -> None:
        with self.assertRaises(ValueError):
            diag.require_trial_feature({}, "WristMeanSpeed_mm_s", "rep")

    def test_09_require_trial_feature_nan_fails(self) -> None:
        with self.assertRaises(ValueError):
            diag.require_trial_feature({"WristMeanSpeed_mm_s": float("nan")}, "WristMeanSpeed_mm_s", "rep")

    def test_10_require_all_trial_features_rejects_short_dict(self) -> None:
        with self.assertRaises(ValueError):
            diag.require_all_trial_features({"WristMeanSpeed_mm_s": 1.0}, "rep")

    def test_11_task12_template_top_level_12(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "template.json"
            task = self._task12()
            path.write_text(json.dumps({"12": task}), encoding="utf-8")
            self.assertEqual(diag.load_task12_template(path)["baseline_version"], diag.BASELINE_VERSION)

    def test_12_task12_template_missing_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "template.json"
            path.write_text(json.dumps({"task12": self._task12()}), encoding="utf-8")
            with self.assertRaises(ValueError):
                diag.load_task12_template(path)

    def test_13_wrong_baseline_version_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "template.json"
            task = self._task12()
            task["baseline_version"] = "wrong"
            path.write_text(json.dumps({"12": task}), encoding="utf-8")
            with self.assertRaises(ValueError):
                diag.load_task12_template(path)

    def test_14_wrong_sensor_mapping_version_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "template.json"
            task = self._task12()
            task["sensor_mapping_version"] = "wrong"
            path.write_text(json.dumps({"12": task}), encoding="utf-8")
            with self.assertRaises(ValueError):
                diag.load_task12_template(path)

    def test_15_missing_template_stats_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "template.json"
            task = self._task12()
            del task["ROM_mean"]
            path.write_text(json.dumps({"12": task}), encoding="utf-8")
            with self.assertRaises(ValueError):
                diag.load_task12_template(path)

    def test_16_detector_trace_lf_branch(self) -> None:
        trial = self._trial()
        trial["TrunkForwardLean_deg"] = 16.0
        conditions, reason, detected, types, _, actual = diag.detector_condition_trace(trial, self._task12())
        self.assertTrue(detected)
        self.assertEqual(reason, "detected")
        self.assertIn("LF", types)
        self.assertTrue(actual["has_compensation"])
        self.assertTrue(any(c["branch"] == "LF" for c in conditions))

    def test_17_detector_trace_tr_branch(self) -> None:
        trial = self._trial()
        trial["TrunkRotation_deg"] = 18.0
        _, _, detected, types, _, _ = diag.detector_condition_trace(trial, self._task12())
        self.assertTrue(detected)
        self.assertIn("TR", types)

    def test_18_quality_upper_arm_condition_present(self) -> None:
        trial = self._trial()
        trial["UpperArmROM_mm"] = 1.0
        conditions, _, _, _, _, _ = diag.detector_condition_trace(trial, self._task12())
        self.assertTrue(any(c["branch"] == "quality_upper_arm_rom_low" for c in conditions))

    def test_19_quality_forearm_condition_present(self) -> None:
        trial = self._trial()
        trial["ForearmRotationRange_deg"] = 999.0
        conditions, _, _, _, _, _ = diag.detector_condition_trace(trial, self._task12())
        self.assertTrue(any(c["branch"] == "quality_forearm_rotation_deviation" for c in conditions))

    def test_20_quality_peaks_condition_present(self) -> None:
        trial = self._trial()
        trial["NumSpeedPeaks"] = 9
        conditions, _, _, _, _, _ = diag.detector_condition_trace(trial, self._task12())
        self.assertTrue(any(c["branch"] == "quality_wrist_speed_peaks" for c in conditions))

    def test_21_quality_pauses_condition_present(self) -> None:
        trial = self._trial()
        trial["PauseCount"] = 4
        conditions, _, _, _, _, _ = diag.detector_condition_trace(trial, self._task12())
        self.assertTrue(any(c["branch"] == "quality_wrist_pause_count" for c in conditions))

    def test_22_four_emg_conditions_present(self) -> None:
        conditions, _, _, _, _, _ = diag.detector_condition_trace(self._trial(), self._task12())
        emg_conditions = [c for c in conditions if c["branch"].startswith("quality_emg_")]
        self.assertEqual(len(emg_conditions), 4)

    def test_23_quality_only_does_not_set_detected(self) -> None:
        trial = self._trial()
        trial["UpperArmROM_mm"] = 1.0
        _, _, detected, types, _, actual = diag.detector_condition_trace(trial, self._task12())
        self.assertFalse(detected)
        self.assertEqual(types, [])
        self.assertFalse(actual["has_compensation"])

    def test_24_imu2_diagnostics_do_not_use_forward_name(self) -> None:
        cols = ["time_ms", "imu2_roll", "imu2_pitch", "imu2_yaw", "imu2_gx", "imu2_gy", "imu2_gz"]
        df = pd.DataFrame({col: [0.0, 10.0, 20.0] for col in cols})
        out = diag.compute_imu2_diagnostics(df, Path("rep.csv"))
        self.assertIn("imu2_pitch_positive_excursion", out)
        self.assertIn("imu2_pitch_negative_excursion", out)
        self.assertIn("imu2_pitch_absolute_excursion", out)
        self.assertIsNone(out["candidate_forward_excursion"])

    def test_25_require_finite_col_rejects_missing_imu2(self) -> None:
        with self.assertRaises(ValueError):
            diag.require_finite_col(pd.DataFrame({"imu2_roll": [1.0]}), "imu2_pitch", Path("rep.csv"))

    def test_26_require_finite_col_rejects_nan(self) -> None:
        with self.assertRaises(ValueError):
            diag.require_finite_col(pd.DataFrame({"imu2_pitch": [1.0, np.nan]}), "imu2_pitch", Path("rep.csv"))

    def test_27_empirical_percentile_uses_order_count(self) -> None:
        self.assertEqual(diag.empirical_percentile([1, 3, 5, 7], 5), 75.0)

    def test_28_empirical_percentile_empty_is_none(self) -> None:
        self.assertIsNone(diag.empirical_percentile([], 5))

    def test_29_safe_z_zero_std_none(self) -> None:
        self.assertIsNone(diag.safe_z(1, 1, 0))

    def test_30_robust_z_zero_iqr_none(self) -> None:
        self.assertIsNone(diag.robust_z(1, 1, 1, 1))

    def test_31_distribution_counts_do_not_mark_mixed_normal_missing(self) -> None:
        rows = [
            {"source_category": "normal", "ground_truth_has_compensation": False, "final_score": 90},
            {"source_category": "mixed", "ground_truth_has_compensation": False, "ground_truth_rep_label": "normal", "final_score": 80},
            {"source_category": "mixed", "ground_truth_has_compensation": True, "final_score": 70},
            {"source_category": "compensation", "ground_truth_has_compensation": True, "final_score": 60},
        ]
        summary = next(item for item in diag.feature_distribution_summary(rows) if item["feature"] == "final_score")
        self.assertEqual(summary["finite_count"], 4)
        self.assertEqual(summary["missing_count"], 0)

    def test_32_candidate_features_have_no_final_threshold(self) -> None:
        rows = [{"source_category": "normal", "ground_truth_has_compensation": False, "final_score": 90}]
        item = next(x for x in diag.feature_distribution_summary(rows) if x["feature"] == "final_score")
        self.assertTrue(item["exploratory_only"])
        self.assertTrue(item["current_dataset_only"])
        self.assertTrue(item["not_clinically_validated"])
        self.assertNotIn("production_threshold", item)

    def test_33_output_dir_must_be_under_derived_root(self) -> None:
        with self.assertRaises(ValueError):
            diag.validate_output_dir(Path("C:/"))

    def test_34_output_dir_rejects_baseline_name(self) -> None:
        with self.assertRaises(ValueError):
            diag.validate_output_dir(diag.DERIVED_ROOT / "baseline_bad")

    def test_35_output_dir_refuses_existing_without_overwrite(self) -> None:
        out = diag.DERIVED_ROOT / "compensation_feature_diagnosis_v1_1_existing_unittest"
        out.mkdir(exist_ok=True)
        try:
            with self.assertRaises(FileExistsError):
                diag.prepare_output_dir(out, overwrite=False)
        finally:
            import shutil

            shutil.rmtree(out, ignore_errors=True)

    def test_36_prepare_output_dir_uses_tmp(self) -> None:
        out = diag.DERIVED_ROOT / "compensation_feature_diagnosis_v1_1_unittest"
        if out.exists():
            self.skipTest("temporary diagnosis output already exists")
        temp = diag.prepare_output_dir(out, overwrite=False)
        try:
            self.assertIn(".tmp_", temp.name)
        finally:
            if temp.exists():
                import shutil

                shutil.rmtree(temp)

    def test_37_summary_contract_counts(self) -> None:
        rows = []
        for _ in range(29):
            rows.append({"source_category": "normal", "ground_truth_has_compensation": False, "rep_index": 1, "final_score": 1, "detector_detected": False})
        for _ in range(5):
            rows.append({"source_category": "compensation", "ground_truth_has_compensation": True, "rep_index": 1, "final_score": 1, "detector_detected": False})
        for rep in (1, 2, 3, 1, 2, 3):
            rows.append({"source_category": "mixed", "ground_truth_has_compensation": rep == 2, "rep_index": rep, "final_score": diag.MIXED_EXPECTED_SCORES.get(rep, 1), "detector_detected": False})
        diag.assert_diagnosis_contracts(rows, rows[-3:])

    def test_38_summary_contract_rejects_detector_hit(self) -> None:
        rows = [{"source_category": "normal", "ground_truth_has_compensation": False, "rep_index": 1, "final_score": 1, "detector_detected": True}] * 40
        with self.assertRaises(ValueError):
            diag.assert_diagnosis_contracts(rows, rows[:3])

    def test_39_mixed_scores_contract_values(self) -> None:
        self.assertEqual(diag.MIXED_EXPECTED_SCORES, {1: 75.04, 2: 74.02, 3: 86.20})

    def test_40_json_safe_nan_inf_to_null(self) -> None:
        out = diag.json_safe({"a": float("nan"), "b": float("inf")})
        self.assertIsNone(out["a"])
        self.assertIsNone(out["b"])

    def test_41_sha_manifest_excludes_itself(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("a", encoding="utf-8")
            diag.sha_manifest(root)
            self.assertNotIn("SHA256SUMS.txt", (root / "SHA256SUMS.txt").read_text(encoding="utf-8"))

    def test_42_generate_diagnosis_cleans_tmp_on_failure(self) -> None:
        out = diag.DERIVED_ROOT / "compensation_feature_diagnosis_v1_1_failure_unittest"
        args = mock.Mock(
            rep_root=str(diag.DERIVED_ROOT / "missing_reps"),
            source_root=str(diag.DERIVED_ROOT / "missing_source"),
            manifest=str(diag.DERIVED_ROOT / "missing_manifest.json"),
            validation=str(diag.DERIVED_ROOT / "missing_validation.json"),
            baseline_template=str(diag.DERIVED_ROOT / "missing_template.json"),
            mixed_session_dir=str(diag.DERIVED_ROOT / "missing_session"),
            mixed_source="",
            output_dir=str(out),
            overwrite=True,
        )
        with self.assertRaises(FileNotFoundError):
            diag.generate_diagnosis(args)
        self.assertFalse(list(diag.DERIVED_ROOT.glob("compensation_feature_diagnosis_v1_1_failure_unittest.tmp_*")))

    def test_43_contract_json_serializable(self) -> None:
        json.dumps(diag.json_safe(diag.DETECTOR_CONTRACT), ensure_ascii=False)

    def test_44_main_table_excludes_session_fatigue_summary(self) -> None:
        self.assertNotIn("session_fatigue_summary", diag.SCORING_FEATURE_FIELDS)

    def test_45_low_score_decomposition_sorts_subscores(self) -> None:
        rows = [{
            "source_category": "normal",
            "ground_truth_has_compensation": False,
            **{field: 10 for field in diag.SUBSCORE_FIELDS},
        }]
        dist = diag.feature_distribution_summary(rows)
        mixed = [{**{f: i for i, f in enumerate(diag.SUBSCORE_FIELDS)}, "rep_index": 1, "final_score": 1, "ground_truth_rep_label": "normal"}]
        mixed += [{**mixed[0], "rep_index": 2}, {**mixed[0], "rep_index": 3}]
        out = diag.low_score_decomposition(rows, mixed, dist)
        self.assertEqual(out[0]["subscores_ranked_low_to_high"][0]["name"], diag.SUBSCORE_FIELDS[0])
        self.assertTrue(out[0]["not_causal_proof"])

    def test_46_source_csv_manifest_requires_22_unique(self) -> None:
        records = [
            mock.Mock(source_sha256=f"sha{i}", source_relative_path=f"f{i}.csv", source_absolute_path=Path(f"f{i}.csv"), source_file_size_bytes=1)
            for i in range(21)
        ]
        with self.assertRaises(ValueError):
            diag.source_csv_manifest(records)

    def test_47_mixed_source_missing_fails(self) -> None:
        with self.assertRaises(FileNotFoundError):
            diag.validate_mixed_source(str(Path("missing.csv")), diag.DERIVED_ROOT, {"source_sha256": "x"})

    def test_48_mixed_source_outside_root_fails(self) -> None:
        with tempfile.NamedTemporaryFile(delete=True) as f:
            with self.assertRaises(ValueError):
                diag.validate_mixed_source(f.name, diag.DERIVED_ROOT, {"source_sha256": "x"})

    def test_49_mixed_source_sha_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "a.csv"
            source.write_text("x", encoding="utf-8")
            with self.assertRaises(ValueError):
                diag.validate_mixed_source(str(source), root, {"source_sha256": "wrong"})

    def test_50_detector_quality_findings_match_trace(self) -> None:
        trial = self._trial()
        trial["UpperArmROM_mm"] = 1.0
        conditions, _, _, _, _, actual = diag.detector_condition_trace(trial, self._task12())
        expected = sorted({c["quality_finding_name"] for c in conditions if c.get("quality_finding_name") and c.get("passed")})
        self.assertEqual(expected, sorted(actual["quality_findings"]))

    def test_51_detector_compensation_score_matches_trace(self) -> None:
        trial = self._trial()
        trial["TrunkForwardLean_deg"] = 20.0
        _, _, _, types, _, actual = diag.detector_condition_trace(trial, self._task12())
        self.assertEqual(actual["compensation_score"], min(100, len(set(types)) * 20))

    def test_52_all_imu2_distribution_fields_present(self) -> None:
        rows = [{
            "source_category": "normal",
            "ground_truth_has_compensation": False,
            **{field: 1 for field in diag.IMU2_DISTRIBUTION_FIELDS},
        }]
        features = {item["feature"] for item in diag.feature_distribution_summary(rows)}
        self.assertTrue(set(diag.IMU2_DISTRIBUTION_FIELDS).issubset(features))

    def test_53_all_boundary_distribution_fields_present(self) -> None:
        rows = [{
            "source_category": "normal",
            "ground_truth_has_compensation": False,
            **{field: 1 for field in diag.BOUNDARY_DISTRIBUTION_FIELDS},
        }]
        features = {item["feature"] for item in diag.feature_distribution_summary(rows)}
        self.assertTrue(set(diag.BOUNDARY_DISTRIBUTION_FIELDS).issubset(features))

    def test_54_output_dir_rejects_nested_child(self) -> None:
        with self.assertRaises(ValueError):
            diag.validate_output_dir(diag.DERIVED_ROOT / "compensation_feature_diagnosis_v1_1_nested" / "child")

    def test_55_output_dir_rejects_arbitrary_name(self) -> None:
        with self.assertRaises(ValueError):
            diag.validate_output_dir(diag.DERIVED_ROOT / "random_output")

    def test_56_markdown_report_accepts_separate_mixed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            exact = [
                {"rep_index": 1, "ground_truth_rep_label": "normal", "final_score": 75.04, "detector_detected": False, "detector_non_hit_reason": "x"}
            ]
            low = [{"rep_index": 1, "lowest_three_subscores": [{"name": "speed_score", "value": 1, "pure_normal_mean": 2, "pure_normal_median": 2, "z_score": 0, "robust_z_score": 0, "normal_percentile": 50}]}]
            diag.write_markdown_reports(out, [], {"total_rep_count": 40}, exact, [{"feature_name": "imu2_pitch_range", "rep_01_value": 1}], low)
            self.assertIn("rep_01", (out / "05_mixed_session_rep_diagnosis.md").read_text(encoding="utf-8"))

    def test_57_condition_has_margin_semantics(self) -> None:
        conditions, _, _, _, _, _ = diag.detector_condition_trace(self._trial(), self._task12())
        self.assertTrue(all("margin_to_pass" in c and "signed_margin" in c for c in conditions))

    def _task12(self) -> dict:
        task = {
            "baseline_version": diag.BASELINE_VERSION,
            "sensor_mapping_version": diag.SENSOR_MAPPING_VERSION,
            "ROM_mean": 10.0,
            "ROM_std": 1.0,
            "forearm_rotation_mean": 10.0,
            "forearm_rotation_std": 1.0,
        }
        for muscle in diag.MUSCLES:
            task[f"{muscle}_RMS_mean"] = 1.0
            task[f"{muscle}_RMS_std"] = 1.0
        return task

    def _trial(self) -> dict:
        trial = {
            "TaskNumber": 12,
            "WristMeanSpeed_mm_s": 1.0,
            "WristMotionSpeedIndex": 1.0,
            "NumSpeedPeaks": 1,
            "PauseCount": 1,
            "ForearmRotationRange_deg": 10.0,
            "ForearmMeanAngularSpeed_deg_s": 1.0,
            "UpperArmROM_mm": 10.0,
            "UpperArmMeanAngularSpeed_deg_s": 1.0,
            "TrunkCompensationRatio": 0.1,
            "TrunkROM_deg": 1.0,
            "TrunkForwardLean_deg": 0.0,
            "TrunkRotation_deg": 0.0,
        }
        for field in diag.EMG_TRIAL_FEATURES:
            trial[field] = 1.0
        return trial


if __name__ == "__main__":
    unittest.main()
