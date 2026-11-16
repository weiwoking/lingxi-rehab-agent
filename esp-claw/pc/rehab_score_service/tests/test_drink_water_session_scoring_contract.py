import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from cloud_interface import raw_log_to_trial
from tools import rebuild_drink_water_v2_1_2_baseline as baseline_v212
from tools import drink_water_session_scoring_v1 as orchestrator


class DrinkWaterSessionScoringContractTests(unittest.TestCase):
    def _rep(self, index, start, end, start_ms, end_ms, valid=True, invalid_reason=None):
        return {
            "rep_index": index,
            "start_index": start,
            "end_index": end,
            "start_time_ms": start_ms,
            "end_time_ms": end_ms,
            "duration_ms": end_ms - start_ms,
            "valid": valid,
            "invalid_reason": invalid_reason,
            "merged_candidate": False if valid else True,
            "incomplete_candidate": False if valid else True,
            "start_boundary": {"boundary_index": start, "boundary_time_ms": start_ms},
            "end_boundary": {"boundary_index": end, "boundary_time_ms": end_ms},
        }

    def _frame(self, rows=30):
        data = {}
        for column in raw_log_to_trial.EXPECTED_FUSED_COLUMNS:
            if column == "log_seq":
                data[column] = list(range(rows))
            elif column == "time_ms":
                data[column] = [idx * 20 for idx in range(rows)]
            elif column == "output_rate_hz":
                data[column] = [50.0] * rows
            elif column.endswith("_valid") or column in {"emg_valid"}:
                data[column] = [1] * rows
            elif column.endswith("_age_ms"):
                data[column] = [10.0] * rows
            elif column.startswith("node"):
                data[column] = [1] * rows
            elif column.startswith("emg_"):
                data[column] = [100.0] * rows
            else:
                data[column] = [0.1] * rows
        return pd.DataFrame(data, columns=raw_log_to_trial.EXPECTED_FUSED_COLUMNS)

    def _source(self, root: Path):
        path = root / "session.csv"
        self._frame(30).to_csv(path, index=False, encoding="utf-8")
        return path

    def _template(self, root: Path):
        path = root / "baseline.json"
        path.write_text(
            json.dumps(
                {
                    "12": {
                        "baseline_version": orchestrator.BASELINE_VERSION,
                        "segmentation_version": orchestrator.SEGMENTATION_VERSION,
                        "sensor_mapping_version": orchestrator.SENSOR_MAPPING_VERSION,
                        "sensor_mapping": {
                            "imu1": "upper_arm",
                            "imu2": "trunk_lower_back",
                            "imu3": "forearm",
                            "imu4": "wrist",
                            "emg1": "extensor_digitorum",
                            "emg2": "biceps_brachii",
                            "emg3": "triceps_brachii",
                            "emg4": "extensor_carpi_ulnaris",
                        },
                        "speed_mean": 1,
                        "speed_std": 1,
                        "num_speed_peaks_mean": 1,
                        "num_speed_peaks_std": 1,
                        "pause_count_mean": 1,
                        "pause_count_std": 1,
                        "ROM_mean": 1,
                        "ROM_std": 1,
                        "forearm_rotation_mean": 1,
                        "forearm_rotation_std": 1,
                        "forearm_angular_speed_mean": 1,
                        "forearm_angular_speed_std": 1,
                        "Extensor_digitorum_RMS_mean": 1,
                        "Extensor_digitorum_RMS_std": 1,
                        "Extensor_digitorum_MAV_mean": 1,
                        "Extensor_digitorum_MAV_std": 1,
                        "Extensor_digitorum_WL_mean": 1,
                        "Extensor_digitorum_WL_std": 1,
                        "Biceps_brachii_RMS_mean": 1,
                        "Biceps_brachii_RMS_std": 1,
                        "Biceps_brachii_MAV_mean": 1,
                        "Biceps_brachii_MAV_std": 1,
                        "Biceps_brachii_WL_mean": 1,
                        "Biceps_brachii_WL_std": 1,
                        "Triceps_brachii_RMS_mean": 1,
                        "Triceps_brachii_RMS_std": 1,
                        "Triceps_brachii_MAV_mean": 1,
                        "Triceps_brachii_MAV_std": 1,
                        "Triceps_brachii_WL_mean": 1,
                        "Triceps_brachii_WL_std": 1,
                        "Extensor_carpi_ulnaris_RMS_mean": 1,
                        "Extensor_carpi_ulnaris_RMS_std": 1,
                        "Extensor_carpi_ulnaris_MAV_mean": 1,
                        "Extensor_carpi_ulnaris_MAV_std": 1,
                        "Extensor_carpi_ulnaris_WL_mean": 1,
                        "Extensor_carpi_ulnaris_WL_std": 1,
                    }
                }
            ),
            encoding="utf-8",
        )
        return path

    def _read_json_inside_temp(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def _score(self, value=80.0, valid=True, comp=False, fatigue=None):
        return {
            "score_version": "v1.0",
            "valid": valid,
            "final_score": value,
            "subscores": {
                "speed_score": 10,
                "ROM_score": 20,
                "smoothness_score": 20,
                "coordination_score": 15,
                "muscle_score": 25,
            },
            "compensation": {"detected": comp},
            "fatigue": fatigue if fatigue is not None else {"state": "core_returned", "need_rest": False, "penalty": 0},
            "brs": None,
            "feedback": ["core_feedback"],
        }

    def test_three_rep_session_writes_three_scores_in_time_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            reps = [
                self._rep(2, 10, 14, 200, 280),
                self._rep(1, 0, 4, 0, 80),
                self._rep(3, 20, 24, 400, 480),
            ]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame())), \
                 patch.object(orchestrator, "build_trial_from_raw_log", return_value={"TaskNumber": 12}), \
                 patch.object(orchestrator, "score_trial_json", side_effect=[self._score(81), self._score(82), self._score(83)]):
                summary = orchestrator.orchestrate_session(source, template, root / "out", "S3")
            self.assertEqual(summary["segmented_candidate_count"], 3)
            self.assertEqual(summary["segmentation_valid_repetition_count"], 3)
            self.assertEqual(summary["scored_repetition_count"], 3)
            self.assertEqual([item["rep_index"] for item in summary["repetitions"]], [1, 2, 3])

    def test_five_rep_session_writes_five_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            reps = [self._rep(i, i * 5, i * 5 + 2, i * 100, i * 100 + 40) for i in range(1, 6)]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame(40))), \
                 patch.object(orchestrator, "build_trial_from_raw_log", return_value={"TaskNumber": 12}), \
                 patch.object(orchestrator, "score_trial_json", return_value=self._score(80)):
                summary = orchestrator.orchestrate_session(source, template, root / "out", "S5")
            self.assertEqual(summary["segmentation_valid_repetition_count"], 5)
            self.assertEqual(len(list((root / "out" / "S5" / "per_rep_scores").glob("*_score.json"))), 5)

    def test_rep_score_json_has_only_frozen_top_level_fields_and_rom_case(self):
        frozen = orchestrator.validate_frozen_score_result(self._score(80))
        self.assertEqual(list(frozen.keys()), orchestrator.FROZEN_SCORE_FIELDS)
        self.assertEqual(list(frozen["subscores"].keys()), orchestrator.FROZEN_SUBSCORE_FIELDS)
        self.assertIn("ROM_score", frozen["subscores"])

    def test_missing_frozen_top_level_field_fails(self):
        payload = self._score(80)
        del payload["fatigue"]
        with self.assertRaises(ValueError):
            orchestrator.validate_frozen_score_result(payload)

    def test_extra_frozen_top_level_field_fails(self):
        payload = self._score(80)
        payload["session_id"] = "bad"
        with self.assertRaises(ValueError):
            orchestrator.validate_frozen_score_result(payload)

    def test_missing_subscore_field_fails(self):
        payload = self._score(80)
        del payload["subscores"]["ROM_score"]
        with self.assertRaises(ValueError):
            orchestrator.validate_frozen_score_result(payload)

    def test_rom_score_wrong_case_fails(self):
        payload = self._score(80)
        payload["subscores"]["rom_score"] = payload["subscores"].pop("ROM_score")
        with self.assertRaises(ValueError):
            orchestrator.validate_frozen_score_result(payload)

    def test_fatigue_is_preserved_from_score_core_without_override(self):
        fatigue = {"state": "core_specific", "need_rest": True, "penalty": 7}
        frozen = orchestrator.validate_frozen_score_result(self._score(80, fatigue=fatigue))
        self.assertEqual(frozen["fatigue"], fatigue)

    def test_compensation_feedback_and_brs_are_preserved(self):
        payload = self._score(80, comp=True)
        payload["brs"] = {"stage": 4}
        payload["feedback"] = ["a", "b"]
        frozen = orchestrator.validate_frozen_score_result(payload)
        self.assertEqual(frozen["compensation"], {"detected": True})
        self.assertEqual(frozen["brs"], {"stage": 4})
        self.assertEqual(frozen["feedback"], ["a", "b"])

    def test_session_average_is_recomputed_from_valid_rep_scores_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "a.json"
            c = root / "c.json"
            a.write_text(json.dumps(self._score(90, True)), encoding="utf-8")
            c.write_text(json.dumps(self._score(70, True)), encoding="utf-8")
            summary = orchestrator.summarize_scores(
                "S",
                root / "s.csv",
                [{"rep_index": 1}, {"rep_index": 3}],
                [],
                [{"rep_index": 2, "invalid_reason": "score_invalid"}],
                [a, c],
                "source_sha",
                10,
                86,
                root / "baseline.json",
                "baseline_sha",
                0,
                100,
                3,
                3,
            )
        self.assertEqual(summary["average_score"], 80.0)
        self.assertEqual(summary["score_invalid_repetition_count"], 1)

    def test_session_summary_has_no_fatigue_summary_or_trend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "score.json"
            p.write_text(json.dumps(self._score(90)), encoding="utf-8")
            summary = orchestrator.summarize_scores("S", root / "s.csv", [{"rep_index": 1}], [], [], [p], "source_sha", 10, 86, root / "baseline.json", "baseline_sha", 0, 100, 1, 1)
        self.assertNotIn("fatigue_summary", summary)
        self.assertNotIn("fatigue_trend", summary)

    def test_no_offline_fatigue_context_class_exists(self):
        self.assertFalse(hasattr(orchestrator, "OfflineFatigueContext"))

    def test_invalid_repetition_is_rejected_before_score_core(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            reps = [self._rep(1, 0, 4, 0, 80, valid=False, invalid_reason="not_a_complete_drink_cycle")]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame())), \
                 patch.object(orchestrator, "build_trial_from_raw_log", side_effect=AssertionError("should not build trial")), \
                 patch.object(orchestrator, "score_trial_json", side_effect=AssertionError("should not score")):
                summary = orchestrator.orchestrate_session(source, template, root / "out", "S_INVALID")
            session_dir = root / "out" / "S_INVALID"
            self.assertEqual(summary["rejected_repetition_count"], 1)
            self.assertEqual(summary["scored_repetition_count"], 0)
            self.assertEqual(len(list((session_dir / "per_rep_scores").glob("*_score.json"))), 0)
            self.assertEqual(len(list((session_dir / "rejected_repetitions").glob("*.metadata.json"))), 1)

    def test_invalid_rep_not_in_average(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            reps = [self._rep(1, 0, 4, 0, 80), self._rep(2, 5, 7, 100, 140, valid=False, invalid_reason="short")]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame())), \
                 patch.object(orchestrator, "build_trial_from_raw_log", return_value={"TaskNumber": 12}), \
                 patch.object(orchestrator, "score_trial_json", return_value=self._score(88)):
                summary = orchestrator.orchestrate_session(source, template, root / "out", "S_MIXED")
            self.assertEqual(summary["segmented_candidate_count"], 2)
            self.assertEqual(summary["segmentation_valid_repetition_count"], 1)
            self.assertEqual(summary["rejected_repetition_count"], 1)
            self.assertEqual(summary["average_score"], 88.0)

    def test_rejected_metadata_preserves_candidates_and_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            reps = [self._rep(1, 0, 4, 0, 80, valid=False, invalid_reason="merged")]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame())), \
                 patch.object(orchestrator, "build_trial_from_raw_log", side_effect=AssertionError("should not build trial")), \
                 patch.object(orchestrator, "score_trial_json", side_effect=AssertionError("should not score")):
                orchestrator.orchestrate_session(source, template, root / "out", "S_REJECT")
            metadata_path = next((root / "out" / "S_REJECT" / "rejected_repetitions").glob("*.metadata.json"))
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["invalid_reason"], "merged")
        self.assertTrue(metadata["merged_candidate"])
        self.assertTrue(metadata["incomplete_candidate"])
        self.assertEqual(metadata["start_boundary"]["boundary_index"], 0)

    def test_compensation_summary_uses_detector_output_not_score_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            low_score = self._score(40, True, comp=False)
            p = root / "score.json"
            p.write_text(json.dumps(low_score), encoding="utf-8")
            summary = orchestrator.summarize_scores("S", root / "s.csv", [{"rep_index": 1}], [], [], [p], "source_sha", 10, 86, root / "baseline.json", "baseline_sha", 0, 100, 1, 1)
        self.assertEqual(summary["compensation_summary"]["detected_repetition_count"], 0)

    def test_missing_baseline_fails_without_formal_template_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                orchestrator.validate_baseline_template(Path(tmp) / "missing.json")

    def test_invalid_baseline_json_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(ValueError):
                orchestrator.validate_baseline_template(path)

    def test_baseline_missing_task_12_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.json"
            path.write_text(json.dumps({"11": {}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                orchestrator.validate_baseline_template(path)

    def test_baseline_missing_required_field_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._template(Path(tmp))
            payload = json.loads(path.read_text(encoding="utf-8"))
            del payload["12"]["ROM_mean"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                orchestrator.validate_baseline_template(path)

    def test_session_summary_is_recomputable_from_per_rep_score_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = []
            for index, value in enumerate([80, 90], start=1):
                path = root / f"rep_{index:02d}_score.json"
                path.write_text(json.dumps(self._score(value)), encoding="utf-8")
                files.append(path)
            summary = orchestrator.summarize_scores(
                "S",
                root / "s.csv",
                [{"rep_index": 1}, {"rep_index": 2}],
                [],
                [],
                files,
                "source_sha",
                10,
                86,
                root / "baseline.json",
                "baseline_sha",
                0,
                100,
                2,
                2,
            )
        self.assertEqual(summary["average_score"], 85.0)
        self.assertEqual(summary["subscore_averages"]["ROM_score"], 20.0)

    def test_source_and_baseline_sha_are_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            expected_source_sha = orchestrator.sha256_file(source)
            expected_template_sha = orchestrator.sha256_file(template)
            reps = [self._rep(1, 0, 4, 0, 80)]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame())), \
                 patch.object(orchestrator, "build_trial_from_raw_log", return_value={"TaskNumber": 12}), \
                 patch.object(orchestrator, "score_trial_json", return_value=self._score(80)):
                summary = orchestrator.orchestrate_session(source, template, root / "out", "S_SHA")
            summary_path = root / "out" / "S_SHA" / "session_summary.json"
            manifest_path = root / "out" / "S_SHA" / "session_manifest.json"
            self.assertTrue(summary_path.exists())
            self.assertTrue(manifest_path.exists())
            persisted_summary = self._read_json_inside_temp(summary_path)
            persisted_manifest = self._read_json_inside_temp(manifest_path)
            self.assertEqual(summary["source_sha256"], expected_source_sha)
            self.assertEqual(summary["baseline_template_sha256"], expected_template_sha)
            self.assertEqual(persisted_summary["source_sha256"], expected_source_sha)
            self.assertEqual(persisted_summary["baseline_template_sha256"], expected_template_sha)
            self.assertEqual(persisted_manifest["source_sha256"], expected_source_sha)
            self.assertEqual(persisted_manifest["baseline_template_sha256"], expected_template_sha)

    def test_session_times_come_from_csv_first_and_last_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            df = self._frame(6)
            reps = [self._rep(1, 1, 4, 20, 80)]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, df)), \
                 patch.object(orchestrator, "build_trial_from_raw_log", return_value={"TaskNumber": 12}), \
                 patch.object(orchestrator, "score_trial_json", return_value=self._score(80)):
                summary = orchestrator.orchestrate_session(source, template, root / "out", "S_TIME")
        self.assertEqual(summary["session_started_at_ms"], 0)
        self.assertEqual(summary["session_ended_at_ms"], 100)
        self.assertEqual(summary["activity_started_at_ms"], 20)
        self.assertEqual(summary["activity_ended_at_ms"], 80)

    def test_existing_session_output_fails_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            (root / "out" / "S_EXISTS").mkdir(parents=True)
            with self.assertRaises(FileExistsError):
                orchestrator.orchestrate_session(source, template, root / "out", "S_EXISTS")

    def test_overwrite_replaces_only_selected_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            selected = root / "out" / "S_OVER"
            other = root / "out" / "OTHER"
            selected.mkdir(parents=True)
            other.mkdir(parents=True)
            (selected / "old_marker.txt").write_text("old", encoding="utf-8")
            (other / "keep.txt").write_text("keep", encoding="utf-8")
            reps = [self._rep(1, 0, 4, 0, 80)]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame())), \
                 patch.object(orchestrator, "build_trial_from_raw_log", return_value={"TaskNumber": 12}), \
                 patch.object(orchestrator, "score_trial_json", return_value=self._score(80)):
                summary = orchestrator.orchestrate_session(source, template, root / "out", "S_OVER", overwrite=True)
            summary_path = selected / "session_summary.json"
            manifest_path = selected / "session_manifest.json"
            self.assertTrue(summary_path.exists())
            self.assertTrue(manifest_path.exists())
            self.assertFalse((selected / "old_marker.txt").exists())
            self.assertTrue((other / "keep.txt").exists())
            self.assertEqual((other / "keep.txt").read_text(encoding="utf-8"), "keep")
            self.assertEqual(list((root / "out").glob("S_OVER.tmp_*")), [])
            self.assertEqual(list((root / "out").glob("S_OVER.rollback_*")), [])
            persisted = self._read_json_inside_temp(summary_path)
            self.assertEqual(persisted["session_id"], summary["session_id"])
            self.assertEqual(persisted["score_valid_repetition_count"], summary["score_valid_repetition_count"])
            self.assertEqual(persisted["average_score"], summary["average_score"])

    def test_failed_run_cleans_temporary_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            reps = [self._rep(1, 0, 4, 0, 80)]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame())), \
                 patch.object(orchestrator, "build_trial_from_raw_log", return_value={"TaskNumber": 12}), \
                 patch.object(orchestrator, "score_trial_json", return_value={"bad": True}), \
                 self.assertRaises(ValueError):
                orchestrator.orchestrate_session(source, template, root / "out", "S_FAIL")
            tmp_dirs = list((root / "out").glob("S_FAIL.tmp_*"))
        self.assertEqual(tmp_dirs, [])

    def test_rep_score_metadata_has_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            reps = [self._rep(1, 0, 4, 0, 80)]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame())), \
                 patch.object(orchestrator, "build_trial_from_raw_log", return_value={"TaskNumber": 12}), \
                 patch.object(orchestrator, "score_trial_json", return_value=self._score(80)):
                orchestrator.orchestrate_session(source, template, root / "out", "S_META")
            metadata = json.loads(next((root / "out" / "S_META" / "per_rep_scores").glob("*_score_metadata.json")).read_text(encoding="utf-8"))
        self.assertEqual(metadata["source_origin"], orchestrator.SOURCE_ORIGIN)
        self.assertEqual(metadata["baseline_version"], orchestrator.BASELINE_VERSION)
        self.assertEqual(metadata["rep_csv_file"], "rep_01.csv")
        self.assertNotIn("fatigue_context", metadata)

    def test_formal_http_interfaces_are_not_modified_by_orchestrator_contract(self):
        self.assertEqual(orchestrator.FROZEN_SCORE_FIELDS, ["score_version", "valid", "final_score", "subscores", "compensation", "fatigue", "brs", "feedback"])

    def test_segmentation_method_is_recorded_from_v2_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            reps = [self._rep(1, 0, 4, 0, 80)]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame())), \
                 patch.object(orchestrator, "build_trial_from_raw_log", return_value={"TaskNumber": 12}), \
                 patch.object(orchestrator, "score_trial_json", return_value=self._score(80)):
                orchestrator.orchestrate_session(source, template, root / "out", "S_SEG")
            manifest = json.loads((root / "out" / "S_SEG" / "session_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["segmentation_version"], orchestrator.SEGMENTATION_VERSION)
        self.assertEqual(manifest["segmentation_method"], "drink_hybrid_segmentation_v2_1")

    def test_score_trend_uses_scored_rep_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = []
            for value in [88, 77]:
                path = root / f"{value}.json"
                path.write_text(json.dumps(self._score(value)), encoding="utf-8")
                files.append(path)
            summary = orchestrator.summarize_scores("S", root / "s.csv", [{"rep_index": 1}, {"rep_index": 2}], [], [], files, "source_sha", 10, 86, root / "baseline.json", "baseline_sha", 0, 100, 2, 2)
        self.assertEqual([item["final_score"] for item in summary["score_trend"]], [88, 77])

    def test_summary_json_serializable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "score.json"
            p.write_text(json.dumps(self._score(90)), encoding="utf-8")
            summary = orchestrator.summarize_scores("S", root / "s.csv", [{"rep_index": 1}], [], [], [p], "source_sha", 10, 86, root / "baseline.json", "baseline_sha", 0, 100, 1, 1)
        json.dumps(summary)

    def test_repetition_csv_is_closed_interval_slice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            df = self._frame(10)
            reps = [self._rep(1, 2, 4, 40, 80)]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, df)), \
                 patch.object(orchestrator, "build_trial_from_raw_log", return_value={"TaskNumber": 12}), \
                 patch.object(orchestrator, "score_trial_json", return_value=self._score(80)):
                orchestrator.orchestrate_session(source, template, root / "out", "S_SLICE")
            exported = pd.read_csv(root / "out" / "S_SLICE" / "repetitions" / "rep_01.csv")
        self.assertEqual(exported["log_seq"].tolist(), [2, 3, 4])

    def test_build_trial_uses_explicit_baseline_template_in_score_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            reps = [self._rep(1, 0, 4, 0, 80)]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame())), \
                 patch.object(orchestrator, "build_trial_from_raw_log", return_value={"TaskNumber": 12}), \
                 patch.object(orchestrator, "score_trial_json", return_value=self._score(80)) as score_mock:
                orchestrator.orchestrate_session(source, template, root / "out", "S_TEMPLATE")
        self.assertEqual(score_mock.call_args.kwargs["template_path"], template)

    def test_expected_repetition_count_is_not_an_argument(self):
        parser = orchestrator.build_parser()
        option_strings = {option for action in parser._actions for option in action.option_strings}
        self.assertNotIn("--expected-repetitions", option_strings)

    def test_rejected_repetitions_are_in_summary_not_score_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            reps = [self._rep(1, 0, 4, 0, 80, valid=False, invalid_reason="short")]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame())), \
                 patch.object(orchestrator, "build_trial_from_raw_log", side_effect=AssertionError("should not build trial")), \
                 patch.object(orchestrator, "score_trial_json", side_effect=AssertionError("should not score")):
                summary = orchestrator.orchestrate_session(source, template, root / "out", "S_REJ_SUM")
        self.assertEqual(summary["rejected_repetitions"][0]["invalid_reason"], "short")
        self.assertEqual(summary["score_trend"], [])

    def test_current_imu_mapping_constants_match_installation(self):
        self.assertEqual(
            raw_log_to_trial.IMU_MAP,
            {
                "upperarm": "imu1",
                "trunk": "imu2",
                "forearm": "imu3",
                "wrist": "imu4",
            },
        )

    def test_emg_mapping_constants_are_unchanged(self):
        self.assertEqual(raw_log_to_trial.EMG_MUSCLE_MAP[1], "Extensor_digitorum")
        self.assertEqual(raw_log_to_trial.EMG_MUSCLE_MAP[2], "Biceps_brachii")
        self.assertEqual(raw_log_to_trial.EMG_MUSCLE_MAP[3], "Triceps_brachii")
        self.assertEqual(raw_log_to_trial.EMG_MUSCLE_MAP[4], "Extensor_carpi_ulnaris")

    def test_build_trial_features_read_correct_imu_prefixes(self):
        df = self._frame(3)
        df["imu1_roll"] = [0, 10, 10]
        df["imu1_pitch"] = [0, 0, 0]
        df["imu1_yaw"] = [0, 0, 0]
        df["imu2_roll"] = [0, 20, 20]
        df["imu2_pitch"] = [0, 0, 0]
        df["imu2_yaw"] = [0, 0, 0]
        df["imu3_roll"] = [0, 30, 30]
        df["imu3_pitch"] = [0, 0, 0]
        df["imu3_yaw"] = [0, 0, 0]
        df["imu4_roll"] = [0, 40, 40]
        df["imu4_pitch"] = [0, 0, 0]
        df["imu4_yaw"] = [0, 0, 0]
        angular_prefixes = []

        def fake_angular_speed(frame, prefix):
            angular_prefixes.append(prefix)
            return np.asarray([{"imu1": 1.0, "imu2": 2.0, "imu3": 3.0, "imu4": 4.0}[prefix]] * len(frame))

        def fake_linear_acc(frame, prefix):
            return np.asarray([0.0] * len(frame)), "mock"

        with patch.object(raw_log_to_trial, "read_fused_csv", return_value=df), \
             patch.object(raw_log_to_trial, "calc_imu_angular_speed", side_effect=fake_angular_speed), \
             patch.object(raw_log_to_trial, "calc_imu_linear_acc_magnitude", side_effect=fake_linear_acc), \
             patch.object(raw_log_to_trial, "calc_imu_raw_acc_magnitude", side_effect=fake_linear_acc):
            trial = raw_log_to_trial.build_trial_from_raw_log("dummy.csv")

        self.assertEqual(angular_prefixes[:4], ["imu4", "imu3", "imu1", "imu2"])
        self.assertEqual(trial["WristMeanSpeed_mm_s"], 4.0)
        self.assertEqual(trial["ForearmMeanAngularSpeed_deg_s"], 3.0)
        self.assertEqual(trial["UpperArmMeanAngularSpeed_deg_s"], 1.0)
        self.assertEqual(trial["TrunkROM_deg"], 20.0)

    def test_v1_2_constants_record_new_baseline_and_sensor_mapping(self):
        self.assertEqual(orchestrator.BASELINE_VERSION, "drink_water_v2.1.2")
        self.assertEqual(orchestrator.SESSION_VERSION, "drink_water_session_v1_2")
        self.assertEqual(orchestrator.SENSOR_MAPPING_VERSION, "rehab_sensor_mapping_20260806")

    def test_v2_1_2_baseline_tool_writes_new_directory(self):
        self.assertEqual(baseline_v212.BASELINE_VERSION, "drink_water_v2.1.2")
        self.assertEqual(baseline_v212.SENSOR_MAPPING_VERSION, "rehab_sensor_mapping_20260806")
        self.assertIn("baseline_v2_1_2_sensor_mapping_fixed", str(baseline_v212.OUTPUT_DIR))
        self.assertNotIn("baseline_v2_1_1", str(baseline_v212.OUTPUT_DIR))

    def test_baseline_missing_sensor_mapping_metadata_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._template(Path(tmp))
            payload = json.loads(path.read_text(encoding="utf-8"))
            del payload["12"]["sensor_mapping_version"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                orchestrator.validate_baseline_template(path)

    def test_old_v2_1_1_baseline_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._template(Path(tmp))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["12"]["baseline_version"] = "drink_water_v2.1.1"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                orchestrator.validate_baseline_template(path)

    def test_official_invalid_score_result_is_accepted_without_filling_fields(self):
        status, payload = orchestrator.validate_score_core_result(
            {"score_version": "v1.0", "valid": False, "invalid_reason": "bad_input"}
        )
        self.assertEqual(status, "invalid_score")
        self.assertEqual(set(payload.keys()), {"score_version", "valid", "invalid_reason"})

    def test_score_invalid_repetition_goes_to_scoring_rejections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            reps = [self._rep(1, 0, 4, 0, 80)]
            invalid = {"score_version": "v1.0", "valid": False, "invalid_reason": "scoring_failed"}
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame())), \
                 patch.object(orchestrator, "build_trial_from_raw_log", return_value={"TaskNumber": 12}), \
                 patch.object(orchestrator, "score_trial_json", return_value=invalid):
                summary = orchestrator.orchestrate_session(source, template, root / "out", "S_SCORE_INVALID")
            session_dir = root / "out" / "S_SCORE_INVALID"
            invalid_score_file = session_dir / "scoring_rejections" / "rep_01_score_invalid.json"
            invalid_metadata_file = session_dir / "scoring_rejections" / "rep_01_score_invalid_metadata.json"
            rep_csv = session_dir / "repetitions" / "rep_01.csv"
            self.assertEqual(summary["scored_repetition_count"], 1)
            self.assertEqual(summary["score_valid_repetition_count"], 0)
            self.assertEqual(summary["score_invalid_repetition_count"], 1)
            self.assertEqual(len(summary["scoring_rejections"]), 1)
            self.assertEqual(summary["average_score"], None)
            self.assertEqual(summary["score_trend"], [])
            self.assertTrue(invalid_score_file.exists())
            self.assertTrue(invalid_metadata_file.exists())
            self.assertTrue(rep_csv.exists())
            invalid_payload = self._read_json_inside_temp(invalid_score_file)
            self.assertEqual(
                invalid_payload,
                {"score_version": "v1.0", "valid": False, "invalid_reason": "scoring_failed"},
            )
            for field in ("final_score", "subscores", "compensation", "fatigue", "brs", "feedback"):
                self.assertNotIn(field, invalid_payload)
            self.assertEqual(len(list((session_dir / "per_rep_scores").glob("*_score.json"))), 0)
            self.assertEqual(len(list((session_dir / "scoring_rejections").glob("*_score_invalid.json"))), 1)

    def test_one_score_invalid_does_not_drop_later_successful_reps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            reps = [self._rep(1, 0, 4, 0, 80), self._rep(2, 5, 9, 100, 180), self._rep(3, 10, 14, 200, 280)]
            invalid = {"score_version": "v1.0", "valid": False, "invalid_reason": "temporary_bad_rep"}
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame())), \
                 patch.object(orchestrator, "build_trial_from_raw_log", return_value={"TaskNumber": 12}), \
                 patch.object(orchestrator, "score_trial_json", side_effect=[self._score(81), invalid, self._score(83)]):
                summary = orchestrator.orchestrate_session(source, template, root / "out", "S_SCORE_MIX")
        self.assertEqual(summary["scored_repetition_count"], 3)
        self.assertEqual(summary["score_valid_repetition_count"], 2)
        self.assertEqual(summary["score_invalid_repetition_count"], 1)
        self.assertEqual([item["final_score"] for item in summary["score_trend"]], [81, 83])

    def test_session_summary_has_no_fatigue_key_in_repetitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            reps = [self._rep(1, 0, 4, 0, 80)]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame())), \
                 patch.object(orchestrator, "build_trial_from_raw_log", return_value={"TaskNumber": 12}), \
                 patch.object(orchestrator, "score_trial_json", return_value=self._score(80)):
                summary = orchestrator.orchestrate_session(source, template, root / "out", "S_NO_FATIGUE")
        self.assertNotIn("fatigue", summary["repetitions"][0])
        self.assertEqual(summary["repetitions"][0]["compensation_detected"], False)

    def test_session_manifest_has_no_fatigue_policy_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            reps = [self._rep(1, 0, 4, 0, 80)]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame())), \
                 patch.object(orchestrator, "build_trial_from_raw_log", return_value={"TaskNumber": 12}), \
                 patch.object(orchestrator, "score_trial_json", return_value=self._score(80)):
                orchestrator.orchestrate_session(source, template, root / "out", "S_MANIFEST")
            manifest = json.loads((root / "out" / "S_MANIFEST" / "session_manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("fatigue_policy", manifest)
        self.assertNotIn("pc_session_fatigue_state_machine", manifest)
        self.assertNotIn("online_global_fatigue_state_modified", manifest)
        self.assertEqual(manifest["sensor_mapping_version"], orchestrator.SENSOR_MAPPING_VERSION)

    def test_report_has_no_fatigue_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            reps = [self._rep(1, 0, 4, 0, 80)]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, self._frame())), \
                 patch.object(orchestrator, "build_trial_from_raw_log", return_value={"TaskNumber": 12}), \
                 patch.object(orchestrator, "score_trial_json", return_value=self._score(80)):
                orchestrator.orchestrate_session(source, template, root / "out", "S_REPORT")
            text = (root / "out" / "S_REPORT" / "session_score_report.md").read_text(encoding="utf-8")
        self.assertNotIn("fatigue |", text)
        self.assertNotIn("fatigue_policy", text)

    def test_compensation_types_only_come_from_detected_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "a.json"
            b = root / "b.json"
            false_comp = self._score(90, comp=False)
            false_comp["compensation"] = {"detected": False, "types": ["should_not_count"]}
            true_comp = self._score(80, comp=True)
            true_comp["compensation"] = {"detected": True, "types": ["trunk_compensation"]}
            a.write_text(json.dumps(false_comp), encoding="utf-8")
            b.write_text(json.dumps(true_comp), encoding="utf-8")
            summary = orchestrator.summarize_scores("S", root / "s.csv", [{"rep_index": 1}, {"rep_index": 2}], [], [], [a, b], "source_sha", 10, 86, root / "baseline.json", "baseline_sha", 0, 100, 2, 2)
        self.assertEqual(summary["compensation_summary"]["types"], ["trunk_compensation"])

    def test_safe_session_id_accepts_plain_names_and_chinese(self):
        self.assertEqual(orchestrator.validate_session_id("USER_VALIDATE_NORMAL3"), "USER_VALIDATE_NORMAL3")
        self.assertEqual(orchestrator.validate_session_id("测试会话_01"), "测试会话_01")

    def test_session_id_rejects_path_traversal_and_absolute_paths(self):
        for value in ["", ".", "..", "..\\x", "../x", "C:\\temp\\x", "\\absolute", "bad/name", "bad:name"]:
            with self.assertRaises(ValueError):
                orchestrator.validate_session_id(value)

    def test_orchestrate_rejects_illegal_session_id_before_directory_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            with self.assertRaises(ValueError):
                orchestrator.orchestrate_session(source, template, root / "out", "..\\escape")
            self.assertFalse((root / "escape").exists())

    def test_missing_imu4_gx_fails_contract(self):
        frame = self._frame().drop(columns=["imu4_gx"])
        with self.assertRaises(ValueError):
            raw_log_to_trial.validate_fused_scoring_input(frame)

    def test_missing_imu2_pose_field_fails_contract(self):
        frame = self._frame().drop(columns=["imu2_roll"])
        with self.assertRaises(ValueError):
            raw_log_to_trial.validate_fused_scoring_input(frame)

    def test_missing_emg3_rms_fails_contract(self):
        frame = self._frame().drop(columns=["emg_rms_3"])
        with self.assertRaises(ValueError):
            raw_log_to_trial.validate_fused_scoring_input(frame)

    def test_partial_linear_acc_group_fails_contract(self):
        frame = self._frame().drop(columns=["imu4_laz"])
        with self.assertRaises(ValueError):
            raw_log_to_trial.validate_fused_scoring_input(frame)

    def test_key_column_all_nan_fails_contract(self):
        frame = self._frame()
        frame["imu1_gx"] = [np.nan] * len(frame)
        with self.assertRaises(ValueError):
            raw_log_to_trial.validate_fused_scoring_input(frame)

    def test_non_numeric_required_column_all_nan_fails_contract(self):
        frame = self._frame()
        frame["emg_rms_3"] = ["bad"] * len(frame)
        with self.assertRaises(ValueError):
            raw_log_to_trial.validate_fused_scoring_input(frame)

    def test_missing_time_ms_and_log_seq_fail_contract(self):
        with self.assertRaises(ValueError):
            raw_log_to_trial.validate_fused_scoring_input(self._frame().drop(columns=["time_ms"]))
        with self.assertRaises(ValueError):
            raw_log_to_trial.validate_fused_scoring_input(self._frame().drop(columns=["log_seq"]))

    def test_missing_required_column_does_not_return_zero_series(self):
        with self.assertRaises(ValueError):
            raw_log_to_trial.get_col(self._frame().drop(columns=["imu4_gx"]), "imu4_gx", required=True)

    def test_baseline_bad_normal_rep_contract_fails_before_output_completion(self):
        record = {
            "rep_csv": Path("rep_01.csv"),
            "source": {"filename": "source.csv"},
            "rep": {"rep_index": 1},
        }
        bad = self._frame().drop(columns=["imu4_gx"])
        with patch.object(baseline_v212, "read_fused_csv", return_value=bad), \
             self.assertRaises(ValueError):
            baseline_v212.build_trial(record)

    def test_session_source_contract_error_does_not_call_score_or_leave_session_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = root / "bad.csv"
            self._frame().drop(columns=["imu4_gx"]).to_csv(source, index=False, encoding="utf-8")
            with patch.object(orchestrator, "score_trial_json", side_effect=AssertionError("should not score")):
                with self.assertRaises(ValueError):
                    orchestrator.orchestrate_session(source, template, root / "out", "BAD_SOURCE")
            self.assertFalse((root / "out" / "BAD_SOURCE").exists())

    def test_rep_contract_error_is_hard_failure_not_scoring_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = self._template(root)
            source = self._source(root)
            bad_rep_df = self._frame().drop(columns=["imu4_gx"])
            reps = [self._rep(1, 0, 4, 0, 80)]
            with patch.object(orchestrator.pipeline, "segment_file", return_value=(reps, {"method": "drink_hybrid_segmentation_v2_1"}, bad_rep_df)), \
                 patch.object(orchestrator, "score_trial_json", side_effect=AssertionError("should not score")), \
                 self.assertRaises(ValueError):
                orchestrator.orchestrate_session(source, template, root / "out", "BAD_REP")
            self.assertFalse((root / "out" / "BAD_REP").exists())

    def test_detector_zero_hit_does_not_allow_production_switch(self):
        manifest = {
            "total_source_count": 40,
            "used_count": 29,
            "excluded_reps": [{}] * 11,
            "input_contract_validated": True,
        }
        validation = {
            "sessions": [{"score": {"valid": True}}],
            "aggregates": {
                "normal": {"final_score": {"mean": 90}},
                "compensation": {"final_score": {"mean": 70}, "compensation_detected_count": 0},
            },
        }
        readiness = baseline_v212.readiness_from_validation(manifest, validation, self._template_payload())
        self.assertEqual(readiness["score_separation_readiness"], "OBSERVED")
        self.assertEqual(readiness["compensation_detector_readiness"], "NOT_VALIDATED")
        self.assertEqual(readiness["production_switch_readiness"], "MANUAL_DECISION_REQUIRED")

    def _template_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._template(Path(tmp))
            return json.loads(path.read_text(encoding="utf-8"))

    def test_formal_and_v2_1_1_baseline_paths_are_not_v2_1_2_outputs(self):
        self.assertNotIn("template", str(baseline_v212.OUTPUT_DIR))
        self.assertNotIn("baseline_v2_1_1", str(baseline_v212.OUTPUT_DIR))


if __name__ == "__main__":
    unittest.main()
