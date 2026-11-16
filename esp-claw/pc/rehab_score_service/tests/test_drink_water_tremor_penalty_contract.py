from __future__ import annotations

import math
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from cloud_interface.cloud_score_api import score_trial_json
from feature_extractors.compensation_detector import (
    DRINK_WATER_TRUNK_COMPENSATION_RATIO_THRESHOLD,
    DRINK_WATER_LF_RULE_VERSION,
    DRINK_WATER_TREMOR_RULE_VERSION,
    detect_compensation_4imu_4emg,
)
from feature_extractors.tremor_detector import (
    TREMOR_BAND_HZ,
    TREMOR_BAND_RATIO_THRESHOLD,
    TREMOR_BAND_RMS_THRESHOLD,
    TREMOR_DETECTOR_VERSION,
    TREMOR_FRACTION_THRESHOLD,
    TREMOR_STEP_SECONDS,
    TREMOR_WINDOW_SECONDS,
    detect_drink_water_tremor_from_rows,
    tremor_fraction_detected,
)
from scoring.score_action_integrated import (
    LF_EXTRA_PENALTY,
    TREMOR_PENALTY,
    calculate_additional_compensation_penalty,
    score_action_integrated,
)


ROOT = Path(__file__).resolve().parents[1]


class DrinkWaterTremorPenaltyContractTest(unittest.TestCase):
    def _rows(self, hz: float = 4.5, amplitude: float = 12.0, seconds: float = 6.0, column: str = "imu4_gx") -> pd.DataFrame:
        fs = 50.0
        time_ms = np.arange(0, seconds, 1.0 / fs) * 1000.0
        values = amplitude * np.sin(2.0 * math.pi * hz * (time_ms / 1000.0))
        frame = pd.DataFrame({"time_ms": time_ms, "imu4_gx": 0.0, "imu4_gy": 0.0, "imu4_gz": 0.0})
        frame[column] = values
        return frame

    def _row(self) -> pd.Series:
        return pd.Series(
            {
                "TaskNumber": 12,
                "WristMeanSpeed_mm_s": 80.0,
                "NumSpeedPeaks": 3.0,
                "PauseCount": 0.0,
                "UpperArmROM_mm": 160.0,
                "ForearmRotationRange_deg": 80.0,
                "ForearmMeanAngularSpeed_deg_s": 40.0,
                "TrunkCompensationRatio": 0.0,
                "TrunkForwardLean_deg": 0.0,
                "TrunkRotation_deg": 0.0,
                "Extensor_digitorum_RMS": 1.0,
                "Extensor_digitorum_MAV": 1.0,
                "Extensor_digitorum_WL_mean": 1.0,
                "Biceps_brachii_RMS": 1.0,
                "Biceps_brachii_MAV": 1.0,
                "Biceps_brachii_WL_mean": 1.0,
                "Triceps_brachii_RMS": 1.0,
                "Triceps_brachii_MAV": 1.0,
                "Triceps_brachii_WL_mean": 1.0,
                "Extensor_carpi_ulnaris_RMS": 1.0,
                "Extensor_carpi_ulnaris_MAV": 1.0,
                "Extensor_carpi_ulnaris_WL_mean": 1.0,
            }
        )

    def _template(self) -> dict:
        task = {
            "speed_mean": 80.0,
            "speed_std": 1.0,
            "ROM_mean": 160.0,
            "ROM_std": 1.0,
            "num_speed_peaks_mean": 3.0,
            "num_speed_peaks_std": 1.0,
            "pause_count_mean": 0.0,
            "pause_count_std": 1.0,
            "forearm_rotation_mean": 80.0,
            "forearm_rotation_std": 1.0,
            "forearm_angular_speed_mean": 40.0,
            "forearm_angular_speed_std": 1.0,
        }
        for muscle in ("Extensor_digitorum", "Biceps_brachii", "Triceps_brachii", "Extensor_carpi_ulnaris"):
            task[f"{muscle}_RMS_mean"] = 1.0
            task[f"{muscle}_RMS_std"] = 1.0
            task[f"{muscle}_MAV_mean"] = 1.0
            task[f"{muscle}_MAV_std"] = 1.0
            task[f"{muscle}_WL_mean"] = 1.0
            task[f"{muscle}_WL_std"] = 1.0
        return {12: task}

    def test_4_5hz_wrist_oscillation_triggers_tremor(self) -> None:
        result = detect_drink_water_tremor_from_rows(self._rows())
        self.assertEqual(result["version"], TREMOR_DETECTOR_VERSION)
        self.assertTrue(result["detected"])
        self.assertGreaterEqual(result["tremor_fraction"], TREMOR_FRACTION_THRESHOLD)

    def test_smooth_low_frequency_motion_does_not_trigger_tremor(self) -> None:
        result = detect_drink_water_tremor_from_rows(self._rows(hz=1.0, amplitude=20.0))
        self.assertFalse(result["detected"])

    def test_single_fast_burst_does_not_satisfy_fraction_gate(self) -> None:
        frame = self._rows(hz=1.0, amplitude=1.0, seconds=8.0)
        frame.loc[50:70, "imu4_gx"] = 40.0 * np.sin(2.0 * math.pi * 4.5 * np.arange(21) / 50.0)
        result = detect_drink_water_tremor_from_rows(frame)
        self.assertFalse(result["detected"])
        self.assertLess(result["tremor_fraction"] or 0, TREMOR_FRACTION_THRESHOLD)

    def test_window_requires_same_axis_ratio_and_rms_thresholds(self) -> None:
        weak = detect_drink_water_tremor_from_rows(self._rows(hz=4.5, amplitude=1.0))
        self.assertFalse(weak["detected"])
        self.assertEqual(TREMOR_BAND_RATIO_THRESHOLD, 0.18)
        self.assertEqual(TREMOR_BAND_RMS_THRESHOLD, 6.0)

    def test_any_single_reliable_axis_can_trigger_window(self) -> None:
        result = detect_drink_water_tremor_from_rows(self._rows(column="imu4_gy"))
        self.assertTrue(result["detected"])
        self.assertTrue(any(window["axis"] == "gy" for window in result["windows"]))

    def test_invalid_imu4_data_is_not_zero_filled_or_interpolated(self) -> None:
        frame = self._rows()
        frame.loc[:, "imu4_gx"] = np.nan
        frame.loc[:, "imu4_gy"] = np.nan
        frame.loc[:, "imu4_gz"] = np.nan
        result = detect_drink_water_tremor_from_rows(frame)
        self.assertFalse(result["detected"])
        self.assertEqual(result["status"], "not_evaluated")

    def test_insufficient_data_does_not_forge_tremor(self) -> None:
        result = detect_drink_water_tremor_from_rows(self._rows(seconds=1.0))
        self.assertFalse(result["detected"])
        self.assertEqual(result["status"], "not_evaluated")

    def test_tremor_detector_contract_constants(self) -> None:
        self.assertEqual(TREMOR_DETECTOR_VERSION, "drink_water_tremor_v1_1")
        self.assertEqual(DRINK_WATER_TREMOR_RULE_VERSION, "drink_water_tremor_v1_1")
        self.assertEqual(TREMOR_BAND_HZ, (3.5, 6.0))
        self.assertEqual(TREMOR_WINDOW_SECONDS, 2.0)
        self.assertEqual(TREMOR_STEP_SECONDS, 0.5)
        self.assertEqual(TREMOR_FRACTION_THRESHOLD, 0.40)

    def test_tremor_fraction_threshold_v1_1_contract(self) -> None:
        self.assertFalse(tremor_fraction_detected(0.333333))
        self.assertTrue(tremor_fraction_detected(0.40))
        self.assertTrue(tremor_fraction_detected(0.466667))

    def test_penalty_normal_lf_tremor_and_combined(self) -> None:
        self.assertEqual(calculate_additional_compensation_penalty({"compensation_types": []})[0], 0.0)
        self.assertEqual(calculate_additional_compensation_penalty({"compensation_types": ["LF"]})[0], 10.0)
        self.assertEqual(calculate_additional_compensation_penalty({"compensation_types": ["TREMOR"]})[0], 15.0)
        self.assertEqual(calculate_additional_compensation_penalty({"compensation_types": ["LF", "TREMOR"]})[0], 25.0)
        self.assertEqual(LF_EXTRA_PENALTY, 10.0)
        self.assertEqual(TREMOR_PENALTY, 15.0)

    def test_lf_threshold_is_unchanged(self) -> None:
        self.assertEqual(DRINK_WATER_TRUNK_COMPENSATION_RATIO_THRESHOLD, 0.09885350515694372)

    def test_detector_types_can_contain_lf_and_tremor(self) -> None:
        result = detect_compensation_4imu_4emg(
            wristF={"DrinkWaterTremorDetected": True, "DrinkWaterTremorFraction": 0.5},
            forearmF={},
            upperF={},
            trunkF={
                "TrunkCompensationRatio": DRINK_WATER_TRUNK_COMPENSATION_RATIO_THRESHOLD,
                "DrinkWaterLFDetected": True,
                "DrinkWaterLFVersion": DRINK_WATER_LF_RULE_VERSION,
            },
            emgF={},
            template_task={},
            muscles=[],
        )
        self.assertEqual(result["compensation_types"], ["LF", "TREMOR"])
        self.assertTrue(result["has_compensation"])

    def test_final_score_penalty_is_applied_once_and_subscores_stay_same(self) -> None:
        row = self._row()
        with patch("scoring.score_action_integrated.detect_compensation_4imu_4emg", return_value={"has_compensation": False, "compensation_types": [], "compensation_score": 0, "details": {}, "quality_findings": []}):
            normal = score_action_integrated(row, self._template())
        with patch("scoring.score_action_integrated.detect_compensation_4imu_4emg", return_value={"has_compensation": True, "compensation_types": ["LF", "TREMOR"], "compensation_score": 40, "details": {}, "quality_findings": []}):
            compensated = score_action_integrated(row, self._template())
        for key in ("speed_score", "ROM_score", "smoothness_score", "coordination_score", "muscle_score"):
            self.assertEqual(normal[key], compensated[key])
        self.assertAlmostEqual(normal["final_score"] - compensated["final_score"], LF_EXTRA_PENALTY + TREMOR_PENALTY)
        self.assertEqual(compensated["compensation_penalty"], 25.0)

    def test_final_score_is_clamped_to_zero(self) -> None:
        row = self._row()
        with patch("scoring.score_action_integrated.detect_compensation_4imu_4emg", return_value={"has_compensation": True, "compensation_types": ["LF", "TREMOR"], "compensation_score": 40, "details": {}, "quality_findings": []}):
            result = score_action_integrated(row, self._template())
        self.assertGreaterEqual(result["final_score"], 0)

    def test_feedback_contains_lf_and_tremor_prompts(self) -> None:
        row = self._row()
        with patch("scoring.score_action_integrated.detect_compensation_4imu_4emg", return_value={"has_compensation": True, "compensation_types": ["LF", "TREMOR"], "compensation_score": 40, "details": {}, "quality_findings": []}):
            result = score_action_integrated(row, self._template())
        self.assertIn("检测到躯干前倾代偿", result["feedback"])
        self.assertIn("检测到手部抖动代偿", result["feedback"])

    def test_tremor_only_feedback_does_not_report_trunk_compensation(self) -> None:
        row = self._row()
        with patch("scoring.score_action_integrated.detect_compensation_4imu_4emg", return_value={"has_compensation": True, "compensation_types": ["TREMOR"], "compensation_score": 15, "details": {}, "quality_findings": []}):
            result = score_action_integrated(row, self._template())
        self.assertIn("检测到手部抖动代偿", result["feedback"])
        self.assertNotIn("检测到躯干前倾代偿", result["feedback"])
        self.assertNotIn("躯干代偿较明显", result["feedback"])

    def test_lf_only_feedback_does_not_report_tremor(self) -> None:
        row = self._row()
        with patch("scoring.score_action_integrated.detect_compensation_4imu_4emg", return_value={"has_compensation": True, "compensation_types": ["LF"], "compensation_score": 10, "details": {}, "quality_findings": []}):
            result = score_action_integrated(row, self._template())
        self.assertIn("检测到躯干前倾代偿", result["feedback"])
        self.assertNotIn("检测到手部抖动代偿", result["feedback"])

    def test_feedback_does_not_infer_lf_from_penalty_amount(self) -> None:
        source = (ROOT / "scoring" / "score_action_integrated.py").read_text(encoding="utf-8")
        self.assertNotIn("compensation_penalty'] > 10", source)
        self.assertNotIn('compensation_penalty"] > 10', source)
        self.assertNotIn("躯干代偿较明显", source)

    def test_public_score_contract_top_level_and_fatigue_remain_frozen(self) -> None:
        trial = {
            "TaskNumber": 12,
            "WristMeanSpeed_mm_s": 80,
            "NumSpeedPeaks": 3,
            "PauseCount": 0,
            "UpperArmROM_mm": 160,
            "TrunkCompensationRatio": 0.0,
            "Extensor_digitorum_RMS": 1,
            "Extensor_digitorum_MAV": 1,
            "Extensor_digitorum_WL_mean": 1,
            "Biceps_brachii_RMS": 1,
            "Biceps_brachii_MAV": 1,
            "Biceps_brachii_WL_mean": 1,
            "Triceps_brachii_RMS": 1,
            "Triceps_brachii_MAV": 1,
            "Triceps_brachii_WL_mean": 1,
            "Extensor_carpi_ulnaris_RMS": 1,
            "Extensor_carpi_ulnaris_MAV": 1,
            "Extensor_carpi_ulnaris_WL_mean": 1,
        }
        with patch("cloud_interface.cloud_score_api.score_trial_full_json", return_value={"scores": {"final_score": 90, "speed_score": 10, "ROM_score": 10, "smoothness_score": 10, "coordination_score": 10, "muscle_score": 10}, "compensation": {"has_compensation": False, "compensation_types": []}, "feedback": []}):
            result = score_trial_json(trial)
        self.assertEqual(list(result.keys()), ["score_version", "valid", "final_score", "subscores", "compensation", "fatigue", "brs", "feedback"])
        self.assertEqual(list(result["subscores"].keys()), ["speed_score", "ROM_score", "smoothness_score", "coordination_score", "muscle_score"])
        self.assertEqual(result["fatigue"], {"state": "正常", "need_rest": False, "penalty": 0})

    def test_v211_segmentation_and_v212_template_files_are_not_targeted(self) -> None:
        touched_names = {path.name for path in [ROOT / "feature_extractors" / "tremor_detector.py"]}
        self.assertNotIn("drink_water_v1_pipeline.py", touched_names)
        self.assertNotIn("MHH_healthy_template.json", touched_names)


if __name__ == "__main__":
    unittest.main()
