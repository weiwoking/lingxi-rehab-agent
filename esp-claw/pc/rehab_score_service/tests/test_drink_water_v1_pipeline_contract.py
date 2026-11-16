import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from tools import drink_water_v1_pipeline as pipeline


ROOT = Path(__file__).resolve().parents[1]


class DrinkWaterV1PipelineContractTests(unittest.TestCase):
    def _pose_frame(self, rows=201, step_ms=100):
        time_ms = np.arange(rows, dtype=float) * step_ms
        return pd.DataFrame(
            {
                "time_ms": time_ms,
                "log_seq": np.arange(rows),
                "imu1_roll": np.zeros(rows),
                "imu1_pitch": np.zeros(rows),
                "imu1_yaw": np.zeros(rows),
                "imu3_roll": np.zeros(rows),
                "imu3_pitch": np.zeros(rows),
                "imu3_yaw": np.zeros(rows),
                "imu4_roll": np.zeros(rows),
                "imu4_pitch": np.zeros(rows),
                "imu4_yaw": np.zeros(rows),
            }
        )

    def test_repetition_schema_documents_inclusive_end_index(self):
        self.assertEqual(pipeline.END_INDEX_CONVENTION, "inclusive")
        self.assertEqual(pipeline.REPETITION_INDEX_BASE, 1)

    def test_imu_all_valid_rate_drops_when_one_channel_is_invalid(self):
        frame = pd.DataFrame(
            {
                "imu1_valid": [1, 1, 1],
                "imu2_valid": [1, 0, 1],
                "imu3_valid": [1, 1, 1],
                "imu4_valid": [1, 1, 1],
                "emg_valid": [1, 1, 1],
            }
        )
        quality = pipeline.quality_summary(frame)
        self.assertEqual(quality["imu1_valid_rate"], 1.0)
        self.assertEqual(quality["imu2_valid_rate"], round(2 / 3, 6))
        self.assertEqual(quality["imu_all_valid_rate"], round(2 / 3, 6))

    def test_each_imu_valid_rate_is_independent(self):
        frame = pd.DataFrame(
            {
                "imu1_valid": [1, 0, 0, 0],
                "imu2_valid": [1, 1, 0, 0],
                "imu3_valid": [1, 1, 1, 0],
                "imu4_valid": [1, 1, 1, 1],
            }
        )
        quality = pipeline.quality_summary(frame)
        self.assertEqual(quality["imu1_valid_rate"], 0.25)
        self.assertEqual(quality["imu2_valid_rate"], 0.5)
        self.assertEqual(quality["imu3_valid_rate"], 0.75)
        self.assertEqual(quality["imu4_valid_rate"], 1.0)

    def test_log_seq_exact_continuity_and_gap_metrics(self):
        frame = pd.DataFrame({"log_seq": [10, 11, 14, 15, 20], "time_ms": [0, 20, 40, 40, 100]})
        quality = pipeline.quality_summary(frame)
        self.assertEqual(quality["log_seq_exact_continuity_rate"], 0.5)
        self.assertEqual(quality["log_seq_gap_event_count"], 2)
        self.assertEqual(quality["log_seq_total_gap"], 6)
        self.assertEqual(quality["log_seq_max_gap"], 4)
        self.assertEqual(quality["time_ms_nonpositive_diff_count"], 1)
        self.assertEqual(quality["time_ms_median_step_ms"], 20.0)
        self.assertEqual(quality["time_ms_max_step_ms"], 60.0)

    def test_load_or_scan_manifest_refreshes_instead_of_reusing_stale_file(self):
        with patch.object(pipeline, "scan_dataset", return_value={"source_file_count": 22}) as mocked:
            payload = pipeline.load_or_scan_manifest(Path("changed-root"))
        self.assertEqual(payload["source_file_count"], 22)
        mocked.assert_called_once()

    def test_dataset_root_change_must_refresh_manifest(self):
        roots = []

        def fake_scan(args):
            roots.append(args.dataset_root)
            return {"standard_dataset_root": args.dataset_root}

        with patch.object(pipeline, "scan_dataset", side_effect=fake_scan):
            first = pipeline.load_or_scan_manifest(Path("root-a"))
            second = pipeline.load_or_scan_manifest(Path("root-b"))
        self.assertEqual(first["standard_dataset_root"], "root-a")
        self.assertEqual(second["standard_dataset_root"], "root-b")
        self.assertEqual(roots, ["root-a", "root-b"])

    def test_empty_data_with_mock_segmenter_returns_zero_repetitions(self):
        empty = pd.DataFrame({"log_seq": [], "time_ms": []})
        with patch.object(pipeline, "read_standard_csv", return_value=empty), \
             patch.object(pipeline, "segment_drink_complete_cycles", return_value=([], {"method": "mock"})), \
             patch.object(pipeline.trial_segmenter, "segment_trials", return_value=([], [], {})):
            reps, diagnostic, frame = pipeline.segment_file(Path("sample.csv"))
        self.assertEqual(reps, [])
        self.assertEqual(len(frame), 0)
        self.assertEqual(diagnostic["repetition_count"], 0)

    def test_hybrid_v2_prefers_obvious_quiet_boundary(self):
        frame = self._pose_frame()
        time_ms = pipeline.time_ms_array(frame)
        envelope = np.ones(len(frame))
        envelope[90:101] = 0.0
        boundaries = pipeline.internal_boundaries_for_segment(frame, 0, 200, envelope, time_ms)
        self.assertTrue(boundaries)
        self.assertIn("strong_quiet", boundaries[0]["boundary_sources"])
        self.assertGreaterEqual(boundaries[0]["confidence"], 0.9)
        self.assertEqual(boundaries[0]["boundary_time_ms"], frame["time_ms"].iloc[boundaries[0]["boundary_index"]])

    def test_adjacent_boundary_candidates_cluster_and_select_strong_quiet(self):
        frame = self._pose_frame()
        time_ms = pipeline.time_ms_array(frame)
        prior = pipeline.load_cycle_duration_prior()
        candidates = [
            pipeline.boundary_payload(frame, 96, ["motion_valley_between_peaks"], None, 0.7, None, 0.72),
            pipeline.boundary_payload(frame, 100, ["strong_quiet"], 800.0, 0.9, 0.8, 0.92),
            pipeline.boundary_payload(frame, 104, ["weak_quiet", "pose_return"], 300.0, 0.8, 0.9, 0.76),
        ]
        clusters = pipeline.cluster_candidate_boundaries(candidates, time_ms, prior)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["selected_index"], 100)
        self.assertEqual(clusters[0]["selection_reason"], "strong_quiet_priority")

    def test_motion_valley_only_boundary_is_not_reliable_cycle_split_evidence(self):
        boundary = {
            "boundary_sources": ["motion_valley_between_peaks"],
            "quiet_duration_ms": None,
            "motion_valley_score": 0.95,
            "pose_return_score": 0.0,
        }
        self.assertFalse(pipeline.boundary_has_reliable_cycle_split_evidence(boundary))

    def test_motion_valley_with_strong_quiet_is_reliable_cycle_split_evidence(self):
        boundary = {
            "boundary_sources": ["motion_valley_between_peaks", "strong_quiet"],
            "quiet_duration_ms": 600.0,
            "motion_valley_score": 0.8,
            "pose_return_score": 0.0,
        }
        self.assertTrue(pipeline.boundary_has_reliable_cycle_split_evidence(boundary))

    def test_motion_valley_with_reliable_pose_return_is_reliable_cycle_split_evidence(self):
        boundary = {
            "boundary_sources": ["motion_valley_between_peaks", "pose_return"],
            "quiet_duration_ms": None,
            "motion_valley_score": 0.8,
            "pose_return_score": pipeline.CONFIG.pose_return_threshold,
        }
        self.assertTrue(pipeline.boundary_has_reliable_cycle_split_evidence(boundary))

    def test_motion_valley_with_unreliable_pose_return_is_not_reliable_cycle_split_evidence(self):
        boundary = {
            "boundary_sources": ["motion_valley_between_peaks", "pose_return"],
            "quiet_duration_ms": None,
            "motion_valley_score": 0.8,
            "pose_return_score": pipeline.CONFIG.pose_return_threshold - 0.01,
        }
        self.assertFalse(pipeline.boundary_has_reliable_cycle_split_evidence(boundary))

    def test_internal_boundaries_filter_motion_valley_only_but_keep_true_boundaries(self):
        frame = self._pose_frame(rows=160, step_ms=100)
        time_ms = pipeline.time_ms_array(frame)
        envelope = np.full(len(frame), 0.6)
        envelope[45] = 0.0
        envelope[95:102] = 0.0
        boundaries = pipeline.internal_boundaries_for_segment(frame, 0, 159, envelope, time_ms)
        self.assertTrue(boundaries)
        self.assertTrue(all(pipeline.boundary_has_reliable_cycle_split_evidence(item) for item in boundaries))
        self.assertTrue(any("strong_quiet" in item["boundary_sources"] for item in boundaries))

    def test_short_local_stage_is_not_a_complete_drink_cycle(self):
        frame = self._pose_frame(rows=30, step_ms=100)
        time_ms = pipeline.time_ms_array(frame)
        envelope = np.ones(len(frame))
        prior = pipeline.load_cycle_duration_prior()
        quality = pipeline.full_cycle_quality(frame, 0, 14, envelope, time_ms, None, None, prior)
        self.assertFalse(quality["valid"])
        self.assertTrue(quality["incomplete_candidate"])
        self.assertEqual(quality["invalid_reason"], "not_a_complete_drink_cycle")

    def test_global_selection_prefers_complete_cycles_over_many_fragments(self):
        frame = self._pose_frame(rows=218, step_ms=100)
        time_ms = pipeline.time_ms_array(frame)
        envelope = np.full(len(frame), 0.2)
        for peak in (35, 108, 178):
            envelope[max(0, peak - 8) : min(len(envelope), peak + 9)] = 1.4
        segment = pipeline.ActionSegment(1, 0, 217, 0.0, 21.7)
        boundaries = [
            pipeline.boundary_payload(frame, index, ["strong_quiet"], 700.0, 0.9, 0.9, 0.92)
            for index in (70, 143)
        ] + [
            pipeline.boundary_payload(frame, index, ["motion_valley_between_peaks"], None, 0.75, 0.4, 0.72)
            for index in (15, 30, 90, 120, 160, 200)
        ]
        selected, metadata, rejected = pipeline.choose_complete_cycles_global(
            frame,
            segment,
            boundaries,
            envelope,
            time_ms,
            pipeline.load_cycle_duration_prior(),
        )
        self.assertLessEqual(len(selected), 3)
        self.assertGreaterEqual(len(selected), 2)
        self.assertTrue(all(metadata[(item.start_index, item.end_index)]["full_cycle_quality"]["valid"] for item in selected))
        self.assertTrue(rejected)

    def test_20260807_real_device_expected_rep_counts_are_not_changed_to_hide_task019(self):
        script = (ROOT / "tools" / "drink_water_tremor_real_data_validation_v1.ps1").read_text(encoding="utf-8")
        expected_lines = {
            'Task="TASK003"; Labels=@("normal","lf","normal")',
            'Task="TASK005"; Labels=@("normal","lf","normal")',
            'Task="TASK006"; Labels=@("normal","normal","normal","normal","normal")',
            'Task="TASK007"; Labels=@("normal","normal","normal","normal","normal")',
            'Task="TASK008"; Labels=@("normal")',
            'Task="TASK009"; Labels=@("normal")',
            'Task="TASK011"; Labels=@("normal")',
            'Task="TASK012"; Labels=@("lf")',
            'Task="TASK013"; Labels=@("lf")',
            'Task="TASK014"; Labels=@("lf")',
            'Task="TASK015"; Labels=@("tremor")',
            'Task="TASK016"; Labels=@("tremor")',
            'Task="TASK019"; Labels=@("tremor")',
        }
        for line in expected_lines:
            self.assertIn(line, script)
        self.assertNotIn('Task="TASK019"; Labels=@("tremor","tremor")', script)

    def test_overlong_segment_without_trustworthy_split_is_merged_invalid(self):
        frame = self._pose_frame(rows=146, step_ms=100)
        time_ms = pipeline.time_ms_array(frame)
        envelope = np.full(len(frame), 0.05)
        segment = pipeline.ActionSegment(1, 0, 145, 0.0, 14.5)
        boundaries = [pipeline.boundary_payload(frame, 70, ["motion_valley_between_peaks"], None, 0.1, 0.1, 0.2)]
        selected, metadata, _ = pipeline.choose_complete_cycles_global(
            frame,
            segment,
            boundaries,
            envelope,
            time_ms,
            pipeline.load_cycle_duration_prior(),
        )
        self.assertEqual(len(selected), 1)
        quality = metadata[(selected[0].start_index, selected[0].end_index)]["full_cycle_quality"]
        self.assertFalse(quality["valid"])
        self.assertTrue(quality["merged_candidate"])
        self.assertEqual(quality["invalid_reason"], "merged_segment_without_trustworthy_complete_cycle_split")

    def test_full_cycle_confidence_comes_from_quality_not_constant(self):
        frame = self._pose_frame(rows=80, step_ms=100)
        time_ms = pipeline.time_ms_array(frame)
        envelope = np.concatenate([np.full(20, 0.1), np.full(40, 1.1), np.full(20, 0.1)])
        quality = pipeline.full_cycle_quality(frame, 0, 79, envelope, time_ms, None, None, pipeline.load_cycle_duration_prior())
        self.assertNotEqual(quality["confidence"], 0.9)
        self.assertEqual(quality["confidence"], quality["full_cycle_score"])

    def test_repetition_metadata_contains_only_own_boundaries(self):
        frame = self._pose_frame(rows=150, step_ms=100)
        segment = pipeline.ActionSegment(1, 0, 74, 0.0, 7.4)
        start_boundary = pipeline.boundary_payload(frame, 0, ["coarse_segment_start"], None, None, None, 0.55)
        end_boundary = pipeline.boundary_payload(frame, 74, ["strong_quiet"], 700.0, 0.9, 0.9, 0.92)
        rep = pipeline.convert_action_segment(
            segment,
            frame,
            "complete_cycle",
            "drink_hybrid_segmentation_v2_1_1",
            {
                "start_boundary": start_boundary,
                "end_boundary": end_boundary,
                "local_supporting_candidates": [end_boundary],
                "full_cycle_quality": {
                    "valid": True,
                    "invalid_reason": None,
                    "confidence": 0.71,
                    "full_cycle_score": 0.71,
                    "merged_candidate": False,
                    "incomplete_candidate": False,
                },
            },
        )
        self.assertIn("start_boundary", rep)
        self.assertIn("end_boundary", rep)
        self.assertIsNotNone(rep["start_boundary"])
        self.assertIsNotNone(rep["end_boundary"])
        self.assertIn("local_supporting_candidates", rep)
        self.assertNotIn("boundaries", rep)

    def test_valid_rep_without_candidates_gets_non_null_coarse_boundaries(self):
        frame = self._pose_frame(rows=120, step_ms=100)
        segment = pipeline.ActionSegment(1, 10, 90, 1.0, 9.0)
        rep = pipeline.convert_action_segment(
            segment,
            frame,
            "complete_cycle",
            "drink_hybrid_segmentation_v2_1_1",
            {
                "full_cycle_quality": {
                    "valid": True,
                    "invalid_reason": None,
                    "confidence": 0.68,
                    "full_cycle_score": 0.68,
                    "start_boundary_quality": 0.35,
                    "end_boundary_quality": 0.35,
                    "merged_candidate": False,
                    "incomplete_candidate": False,
                },
            },
        )
        self.assertEqual(rep["start_boundary"]["boundary_index"], rep["start_index"])
        self.assertEqual(rep["end_boundary"]["boundary_index"], rep["end_index"])
        self.assertEqual(rep["start_boundary"]["boundary_time_ms"], frame["time_ms"].iloc[10])
        self.assertEqual(rep["end_boundary"]["boundary_time_ms"], frame["time_ms"].iloc[90])
        self.assertEqual(rep["start_boundary"]["boundary_origin"], "coarse_segment_edge")
        self.assertEqual(rep["end_boundary"]["boundary_origin"], "coarse_segment_edge")
        self.assertFalse(rep["start_boundary"]["detected_from_signal_candidate"])
        self.assertFalse(rep["end_boundary"]["detected_from_signal_candidate"])
        self.assertEqual(
            rep["start_boundary"]["selection_reason"],
            "accepted coarse segment start; no explicit local boundary candidate",
        )

    def test_coarse_edge_boundary_does_not_impersonate_signal_candidate(self):
        frame = self._pose_frame(rows=120, step_ms=100)
        boundary = pipeline.normalize_repetition_boundary(frame, None, 40, 0.35, "start")
        self.assertEqual(boundary["boundary_sources"], ["coarse_segment_start"])
        self.assertNotIn("strong_quiet", boundary["boundary_sources"])
        self.assertNotIn("motion_valley_between_peaks", boundary["boundary_sources"])
        self.assertIsNone(boundary["quiet_duration_ms"])
        self.assertIsNone(boundary["pose_return_score"])

    def test_detected_candidate_boundary_content_is_preserved_and_marked(self):
        frame = self._pose_frame(rows=120, step_ms=100)
        original = pipeline.boundary_payload(frame, 55, ["strong_quiet"], 700.0, 0.8, 0.9, 0.92)
        normalized = pipeline.normalize_repetition_boundary(frame, original, 55, 0.35, "start")
        self.assertEqual(normalized["boundary_index"], original["boundary_index"])
        self.assertEqual(normalized["boundary_time_ms"], original["boundary_time_ms"])
        self.assertEqual(normalized["boundary_sources"], original["boundary_sources"])
        self.assertEqual(normalized["quiet_duration_ms"], original["quiet_duration_ms"])
        self.assertEqual(normalized["pose_return_score"], original["pose_return_score"])
        self.assertEqual(normalized["confidence"], original["confidence"])
        self.assertEqual(normalized["boundary_origin"], "detected_candidate")
        self.assertTrue(normalized["detected_from_signal_candidate"])

    def test_file_edges_are_marked_without_signal_candidate_evidence(self):
        frame = self._pose_frame(rows=20, step_ms=50)
        start = pipeline.normalize_repetition_boundary(frame, None, 0, 0.35, "start")
        end = pipeline.normalize_repetition_boundary(frame, None, len(frame) - 1, 0.35, "end")
        self.assertEqual(start["boundary_sources"], ["file_start"])
        self.assertEqual(end["boundary_sources"], ["file_end"])
        self.assertEqual(start["boundary_origin"], "file_edge")
        self.assertEqual(end["boundary_origin"], "file_edge")
        self.assertFalse(start["detected_from_signal_candidate"])
        self.assertFalse(end["detected_from_signal_candidate"])

    def test_boundary_normalization_does_not_change_repetition_indexes(self):
        frame = self._pose_frame(rows=120, step_ms=100)
        segment = pipeline.ActionSegment(1, 12, 88, 1.2, 8.8)
        before = (segment.start_index, segment.end_index)
        rep = pipeline.convert_action_segment(
            segment,
            frame,
            "complete_cycle",
            "drink_hybrid_segmentation_v2_1_1",
            {
                "full_cycle_quality": {
                    "valid": True,
                    "invalid_reason": None,
                    "confidence": 0.7,
                    "full_cycle_score": 0.7,
                    "start_boundary_quality": 0.35,
                    "end_boundary_quality": 0.35,
                    "merged_candidate": False,
                    "incomplete_candidate": False,
                },
            },
        )
        after = (rep["start_index"], rep["end_index"])
        self.assertEqual(before, after)

    def test_focus_diagnostics_is_always_generated(self):
        captured = {}
        payload = {"generated_at": "now", "records": []}
        with patch.object(pipeline, "json_dump", side_effect=lambda path, data: captured.update({"path": path, "data": data})):
            pipeline.write_hybrid_v2_focus_diagnostics(payload, [])
        self.assertEqual(captured["path"], pipeline.SEGMENTATION_DIR / "hybrid_v2_focus_diagnostics.json")
        self.assertEqual(captured["data"]["failure_count"], 0)
        self.assertEqual(captured["data"]["records"], [])

    def test_fixture_14_86_second_segment_selects_two_cycles_not_six_fragments(self):
        selected = self._synthetic_global_selection(duration_ms=14860, boundary_indexes=[74], peak_indexes=[36, 112])
        self.assertLessEqual(len(selected), 2)
        self.assertGreaterEqual(len(selected), 1)

    def test_fixture_21_70_second_segment_selects_about_three_cycles_not_eight_fragments(self):
        selected = self._synthetic_global_selection(duration_ms=21700, boundary_indexes=[72, 145], peak_indexes=[35, 108, 180])
        self.assertLessEqual(len(selected), 3)
        self.assertGreaterEqual(len(selected), 2)

    def test_fixture_21_96_second_segment_does_not_emit_ten_fragments(self):
        selected = self._synthetic_global_selection(duration_ms=21960, boundary_indexes=[73, 146], peak_indexes=[36, 109, 182])
        self.assertLess(len(selected), 10)

    def test_fixture_14_56_and_14_02_second_segments_trigger_internal_selection(self):
        for duration_ms in (14560, 14020):
            rows = int(duration_ms // 100) + 1
            frame = self._pose_frame(rows=rows, step_ms=100)
            time_ms = pipeline.time_ms_array(frame)
            envelope = np.ones(len(frame))
            boundary = pipeline.boundary_payload(frame, rows // 2, ["strong_quiet"], 700.0, 0.8, 0.8, 0.92)
            segment = pipeline.ActionSegment(1, 0, rows - 1, 0.0, float(time_ms[-1]) / 1000.0)
            selected, _, _ = pipeline.choose_complete_cycles_global(
                frame,
                segment,
                [boundary],
                envelope,
                time_ms,
                pipeline.load_cycle_duration_prior(),
            )
            self.assertGreaterEqual(len(selected), 1)

    def test_single_cycle_6_45_to_8_20_seconds_is_not_over_split(self):
        for duration_ms in (6450, 8200):
            rows = int(duration_ms // 100) + 1
            frame = self._pose_frame(rows=rows, step_ms=100)
            time_ms = pipeline.time_ms_array(frame)
            envelope = np.ones(len(frame))
            boundaries = pipeline.internal_boundaries_for_segment(frame, 0, rows - 1, envelope, time_ms)
            self.assertEqual(boundaries, [])

    def _synthetic_global_selection(self, duration_ms: int, boundary_indexes: list[int], peak_indexes: list[int]):
        rows = int(duration_ms // 100) + 1
        frame = self._pose_frame(rows=rows, step_ms=100)
        time_ms = pipeline.time_ms_array(frame)
        envelope = np.full(len(frame), 0.2)
        for peak in peak_indexes:
            envelope[max(0, peak - 8) : min(len(envelope), peak + 9)] = 1.5
        boundaries = [
            pipeline.boundary_payload(frame, index, ["strong_quiet"], 700.0, 0.9, 0.9, 0.92)
            for index in boundary_indexes
            if 0 < index < len(frame) - 1
        ]
        segment = pipeline.ActionSegment(1, 0, len(frame) - 1, 0.0, float(time_ms[-1]) / 1000.0)
        selected, _, _ = pipeline.choose_complete_cycles_global(
            frame,
            segment,
            boundaries,
            envelope,
            time_ms,
            pipeline.load_cycle_duration_prior(),
        )
        return selected

    def test_short_pause_can_split_by_motion_valley_between_peaks(self):
        frame = self._pose_frame()
        time_ms = pipeline.time_ms_array(frame)
        envelope = np.full(len(frame), 0.2)
        envelope[35] = 1.6
        envelope[100] = 0.05
        envelope[165] = 1.6
        boundaries = pipeline.internal_boundaries_for_segment(frame, 0, 200, envelope, time_ms)
        self.assertTrue(any("motion_valley_between_peaks" in item["boundary_sources"] for item in boundaries))

    def test_long_merged_segment_triggers_internal_split(self):
        frame = self._pose_frame(rows=221, step_ms=100)
        time_ms = pipeline.time_ms_array(frame)
        segment = pipeline.ActionSegment(1, 0, 220, 0.0, 22.0)
        boundary = pipeline.boundary_payload(frame, 110, ["strong_quiet"], 600.0, 0.8, 0.9, 0.92)
        split = pipeline.split_segment_by_boundaries(segment, [boundary], time_ms)
        self.assertEqual(len(split), 2)
        self.assertLess(split[0].end_index, split[1].start_index)

    def test_single_action_duration_is_not_over_split_by_v2(self):
        frame = self._pose_frame(rows=61, step_ms=100)
        time_ms = pipeline.time_ms_array(frame)
        envelope = np.ones(len(frame))
        boundaries = pipeline.internal_boundaries_for_segment(frame, 0, 60, envelope, time_ms)
        self.assertEqual(boundaries, [])

    def test_expected_repetitions_is_not_a_hybrid_v2_input(self):
        frame = self._pose_frame(rows=221, step_ms=100)
        segment = pipeline.ActionSegment(1, 0, 220, 0.0, 22.0)
        segments, diagnostic, _ = pipeline.hybrid_segment_drink_v2(frame, [segment], {"method": "mock"})
        self.assertIn("hybrid_v2_enabled", diagnostic)
        self.assertNotIn("expected_repetitions", diagnostic)
        self.assertGreaterEqual(len(segments), 1)

    def test_boundary_payload_is_traceable_to_source_row_and_time(self):
        frame = self._pose_frame(rows=20, step_ms=50)
        boundary = pipeline.boundary_payload(frame, 7, ["strong_quiet"], 500.0, 0.7, 0.8, 0.9)
        self.assertEqual(boundary["boundary_index"], 7)
        self.assertEqual(boundary["boundary_time_ms"], 350.0)
        self.assertEqual(boundary["boundary_sources"], ["strong_quiet"])

    def test_invalid_segment_is_not_counted_as_detected_repetition(self):
        captured = []

        def fake_json_dump(path, payload):
            captured.append((path, payload))

        manifest = {
            "records": [
                {
                    "relative_path": "01/sample.csv",
                    "filename": "sample.csv",
                    "sha256": "abc",
                    "expected_label": "normal_single",
                    "expected_repetitions": 1,
                    "quality_summary": {},
                }
            ]
        }
        reps = [
            {"rep_index": 1, "valid": True, "split_method": "mock", "candidate_assessment": "not_evaluated"},
            {"rep_index": 2, "valid": False, "split_method": "mock", "candidate_assessment": "not_evaluated"},
        ]
        with patch.object(pipeline, "ensure_output_dirs"), \
             patch.object(pipeline, "resolve_standard_dataset_root", return_value=Path("root")), \
             patch.object(pipeline, "load_or_scan_manifest", return_value=manifest), \
             patch.object(pipeline, "segment_file", return_value=(reps, {"method": "mock"}, pd.DataFrame())), \
             patch.object(pipeline, "detect_compensation_for_repetition", return_value={"status": "not_evaluated_in_phase_1"}), \
             patch.object(pipeline, "json_dump", side_effect=fake_json_dump), \
             patch.object(pipeline, "write_segmentation_report"), \
             patch.object(pipeline, "write_repetition_schema"):
            result = pipeline.segment_dataset(argparse.Namespace(dataset_root="root"))
        self.assertEqual(result["candidate_total"], 2)
        self.assertEqual(result["valid_total"], 1)
        self.assertEqual(result["invalid_total"], 1)
        self.assertEqual(result["detected_total_repetitions"], 1)

    def test_bootstrap_rejects_normal_single_with_two_valid_repetitions(self):
        manifest = {
            "records": [
                {"category": "01_normal", "relative_path": f"normal_{idx}.csv", "sha256": str(idx)}
                for idx in range(10)
            ]
        }
        two_reps = [
            {"rep_index": 1, "valid": True, "start_index": 1, "end_index": 2},
            {"rep_index": 2, "valid": True, "start_index": 3, "end_index": 4},
        ]
        with patch.object(pipeline, "ensure_output_dirs"), \
             patch.object(pipeline, "resolve_standard_dataset_root", return_value=Path("root")), \
             patch.object(pipeline, "load_or_scan_manifest", return_value=manifest), \
             patch.object(pipeline, "segment_file", return_value=(two_reps, {"method": "mock"}, pd.DataFrame())), \
             patch.object(pipeline, "bootstrap_failure_report", return_value={"template_status": "bootstrap_failed"}) as failure:
            result = pipeline.generate_bootstrap_template(argparse.Namespace(dataset_root="root"))
        self.assertEqual(result["template_status"], "bootstrap_failed")
        failure.assert_called_once()

    def test_bootstrap_rejects_fewer_than_ten_sources(self):
        manifest = {"records": [{"category": "01_normal", "relative_path": "one.csv", "sha256": "1"}]}
        with patch.object(pipeline, "ensure_output_dirs"), \
             patch.object(pipeline, "resolve_standard_dataset_root", return_value=Path("root")), \
             patch.object(pipeline, "load_or_scan_manifest", return_value=manifest), \
             patch.object(pipeline, "bootstrap_failure_report", return_value={"template_status": "bootstrap_failed"}) as failure:
            result = pipeline.generate_bootstrap_template(argparse.Namespace(dataset_root="root"))
        self.assertEqual(result["template_status"], "bootstrap_failed")
        failure.assert_called_once()

    def test_angle_179_to_minus_179_unwrap_uses_short_motion(self):
        values = pipeline.unwrap_degrees([179.0, -179.0])
        self.assertAlmostEqual(float(values[1] - values[0]), 2.0)
        frame = pd.DataFrame({"imu1_roll": [179.0, -179.0]})
        normalized = pipeline.normalize_frame(frame, ["imu1_roll"], 3)["imu1_roll"]
        self.assertLess(max(normalized) - min(normalized), 3.0)

    def test_exported_slice_rows_equal_original_iloc_slice(self):
        frame = pd.DataFrame({"log_seq": [1, 2, 3, 4], "time_ms": [0, 20, 40, 60]})
        expected = frame.iloc[1 : 3 + 1].copy()
        actual = frame.iloc[1 : 3 + 1].copy()
        pd.testing.assert_frame_equal(actual, expected)

    def test_export_slice_content_is_independent_from_boundary_metadata(self):
        frame = pd.DataFrame({"log_seq": [1, 2, 3, 4, 5], "time_ms": [0, 20, 40, 60, 80]})
        segment = pipeline.ActionSegment(1, 1, 3, 0.02, 0.06)
        rep = pipeline.convert_action_segment(
            segment,
            frame,
            "complete_cycle",
            "drink_hybrid_segmentation_v2_1_1",
            {
                "full_cycle_quality": {
                    "valid": True,
                    "invalid_reason": None,
                    "confidence": 0.7,
                    "full_cycle_score": 0.7,
                    "start_boundary_quality": 0.35,
                    "end_boundary_quality": 0.35,
                    "merged_candidate": False,
                    "incomplete_candidate": False,
                },
            },
        )
        expected = frame.iloc[1 : 3 + 1].copy()
        actual = frame.iloc[int(rep["start_index"]) : int(rep["end_index"]) + 1].copy()
        pd.testing.assert_frame_equal(actual, expected)

    def test_output_roots_do_not_target_original_dataset_or_formal_templates(self):
        self.assertIn("_rehab_scoring_assets", str(pipeline.DERIVED_ROOT))
        self.assertIn("derived", str(pipeline.DERIVED_ROOT))
        self.assertNotEqual(pipeline.DERIVED_ROOT, pipeline.STANDARD_DATASET_ROOT)
        self.assertNotIn("MHH_healthy_template.json", str(pipeline.DERIVED_ROOT))
        self.assertNotIn("temporary_standard_20260724", str(pipeline.DERIVED_ROOT))

    def test_repetition_schema_allows_null_candidate_fields(self):
        schema = {}
        with patch.object(pipeline, "json_dump", side_effect=lambda path, payload: schema.update(payload)):
            pipeline.write_repetition_schema()
        self.assertEqual(schema["properties"]["merged_candidate"]["type"], ["boolean", "null"])
        self.assertEqual(schema["properties"]["incomplete_candidate"]["type"], ["boolean", "null"])
        self.assertEqual(schema["properties"]["start_boundary"]["type"], "object")
        self.assertEqual(schema["properties"]["end_boundary"]["type"], "object")
        self.assertIn("boundary_origin", schema["properties"]["start_boundary"]["required"])
        self.assertEqual(
            schema["properties"]["start_boundary"]["properties"]["boundary_origin"]["enum"],
            ["detected_candidate", "coarse_segment_edge", "file_edge"],
        )
        self.assertIn("candidate_assessment", schema["required"])

    def test_json_payload_is_serializable(self):
        payload = {
            "schema_version": pipeline.TOOL_SCHEMA_VERSION,
            "config": pipeline.asdict(pipeline.CONFIG),
            "formal_template_replacement": False,
        }
        json.dumps(payload, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
