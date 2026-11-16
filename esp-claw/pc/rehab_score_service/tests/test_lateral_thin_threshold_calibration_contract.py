from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from tools import lateral_transfer_thin_threshold_calibration as calibration


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "lateral_transfer_thin_threshold_calibration.py"


class LateralThinThresholdCalibrationContractTest(unittest.TestCase):
    def source(self) -> str:
        return TOOL.read_text(encoding="utf-8")

    def test_uses_current_lateral_wrapper(self) -> None:
        source = self.source()
        self.assertIn("from tools.lateral_transfer_segmentation_v1 import segment_lateral_transfer_file", source)
        self.assertIn("segment_lateral_transfer_file(csv_path)", source)

    def test_uses_drink_lf_and_tremor_authorities(self) -> None:
        source = self.source()
        self.assertIn("from feature_extractors import lf_detector, tremor_detector", source)
        self.assertIn("lf_detector.detect_drink_water_lf_from_rows", source)
        self.assertIn("tremor_detector.detect_drink_water_tremor_from_rows", source)

    def test_does_not_call_lateral_candidate_detectors(self) -> None:
        source = self.source()
        self.assertNotIn("detect_lateral_transfer_lf", source)
        self.assertNotIn("detect_lateral_transfer_tremor", source)
        self.assertNotIn("lateral_transfer_detectors", source)

    def test_tremor_structure_is_frozen_to_authority_constants(self) -> None:
        source = self.source()
        self.assertIn("tremor_detector.TREMOR_WINDOW_SECONDS", source)
        self.assertIn("tremor_detector.TREMOR_STEP_SECONDS", source)
        self.assertIn("tremor_detector._axis_band_features", source)
        self.assertIn('"emg_tremor_implemented": False', source)
        self.assertNotIn("emg2", source)
        self.assertNotIn("emg3", source)

    def test_tremor_search_range_is_bounded(self) -> None:
        source = self.source()
        self.assertIn("TREMOR_RATIO_CANDIDATES = (0.18, 0.16, 0.14)", source)
        self.assertIn("TREMOR_RMS_CANDIDATES = (6.0, 5.5, 5.0)", source)
        self.assertIn("TREMOR_FRACTION_CANDIDATES = (0.40, 0.35, 0.30, 0.25)", source)
        self.assertNotIn("TREMOR_RATIO_CANDIDATES = (0.18, 0.16, 0.14, 0.12", source)
        self.assertNotIn("TREMOR_RMS_CANDIDATES = (6.0, 5.5, 5.0, 4.5", source)

    def test_lf_search_range_is_bounded(self) -> None:
        source = self.source()
        self.assertIn("LF_ANGLE_CANDIDATES_DEG = (10.0, 11.0, 12.0, 13.0, 14.0, 15.0)", source)
        self.assertIn("LF_PERSISTENCE_CANDIDATES_S = (1.0, 1.1, 1.2, 1.3, 1.4, 1.5)", source)
        self.assertIn("lf_detector._smooth_valid_roll", source)
        self.assertIn("lf_detector._max_continuous_duration_s", source)

    def test_expected_labels_are_only_for_evaluation(self) -> None:
        source = self.source()
        self.assertIn("expected_label", source)
        self.assertIn("_confusion", source)
        segment_call = [line.strip() for line in source.splitlines() if "segment_lateral_transfer_file(csv_path)" in line][0]
        self.assertEqual(segment_call, "reps, diagnostic, df = segment_lateral_transfer_file(csv_path)")

    def test_mismatched_sessions_are_skipped_not_excluded(self) -> None:
        source = self.source()
        self.assertIn('"reason": "SEGMENTATION_SKIPPED"', source)
        self.assertNotIn("DATA_QUALITY_EXCLUSION", source)
        self.assertNotIn("TASK036", source)
        self.assertNotIn("TASK039", source)

    def test_no_fabricated_data_operations(self) -> None:
        source = self.source()
        for token in ("zero_fill", "fillna", "interpolate", "ffill", "bfill", "repeat_last", "repeat-last"):
            self.assertNotIn(token, source)

    def test_outputs_required_files_and_does_not_write_profile(self) -> None:
        source = self.source()
        for token in (
            "calibration_summary.json",
            "tremor_error_cases.csv",
            "lf_error_cases.csv",
            "tremor_threshold_scan.csv",
            "lf_threshold_scan.csv",
            "tremor_per_rep_margin.csv",
            "lf_per_rep_margin.csv",
        ):
            self.assertIn(token, source)
        self.assertIn('"profile_written_to_production": False', source)
        self.assertNotIn("lateral_transfer_profile.py').write", source)
        self.assertNotIn('lateral_transfer_profile.py").write', source)

    def test_tremor_window_keeps_all_valid_axes(self) -> None:
        frame = pd.DataFrame(
            {
                "time_ms": [float(index * 100) for index in range(25)],
                "imu4_gx": [1.0] * 25,
                "imu4_gy": [2.0] * 25,
                "imu4_gz": [3.0] * 25,
            }
        )

        def fake_axis_features(values, fs):
            mean_value = float(sum(values) / len(values))
            return {1.0: (0.30, 1.0), 2.0: (0.20, 7.0), 3.0: (0.10, 9.0)}[mean_value]

        with mock.patch.object(calibration.tremor_detector, "_axis_band_features", side_effect=fake_axis_features):
            features = calibration._tremor_window_features(frame)

        self.assertGreater(len(features["windows"]), 0)
        self.assertEqual({axis["axis"] for axis in features["windows"][0]["axes"]}, {"gx", "gy", "gz"})

    def test_tremor_candidate_uses_any_axis_same_window_ratio_and_rms(self) -> None:
        features = {
            "windows": [
                {
                    "axes": [
                        {"axis": "gx", "band_ratio": 0.30, "band_rms": 1.0},
                        {"axis": "gy", "band_ratio": 0.20, "band_rms": 7.0},
                    ]
                }
            ]
        }
        detected, positive_count, valid_count, fraction = calibration._tremor_detect(features, 0.18, 6.0, 0.40)
        self.assertTrue(detected)
        self.assertEqual(positive_count, 1)
        self.assertEqual(valid_count, 1)
        self.assertEqual(fraction, 1.0)

    def test_tremor_baseline_equivalence_is_enforced_before_recommendation(self) -> None:
        source = self.source()
        self.assertIn("TREMOR_AUTHORITY_BASELINE_NOT_EQUIVALENT", source)
        self.assertIn("tremor_authority_baseline_equivalent", source)
        self.assertIn("_find_tremor_baseline_scan", source)

    def test_stage_a_success_prevents_stage_b_or_c_recommendation(self) -> None:
        rows = [
            *[
                {"session_id": f"t{index}", "rep_index": 1, "expected_label": "tremor", "frame": object()}
                for index in range(5)
            ],
            {"session_id": "n", "rep_index": 1, "expected_label": "normal", "frame": object()},
        ]
        features = {
            "authority": {"detected": False},
            "windows": [{"axes": [{"axis": "gy", "band_ratio": 0.18, "band_rms": 6.0}]}],
        }
        with mock.patch.object(calibration, "_tremor_window_features", return_value=features):
            scan_rows, _margins, selected = calibration._scan_tremor(rows)

        self.assertEqual(selected["search_stage"], "FRACTION_ONLY")
        self.assertTrue(selected["recommended"])
        self.assertTrue(all(row["search_stage"] == "FRACTION_ONLY" for row in scan_rows))


if __name__ == "__main__":
    unittest.main()
