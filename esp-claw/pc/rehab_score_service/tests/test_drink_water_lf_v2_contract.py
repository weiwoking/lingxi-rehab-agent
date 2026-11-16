from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from cloud_interface.cloud_score_api import score_trial_json
from feature_extractors.compensation_detector import (
    DRINK_WATER_LF_RULE_VERSION,
    DRINK_WATER_TRUNK_COMPENSATION_RATIO_THRESHOLD,
    detect_compensation_4imu_4emg,
)
from feature_extractors.lf_detector import (
    DRINK_WATER_LF_VERSION,
    LF_MIN_DURATION_S,
    LF_ROLL_THRESHOLD_DEG,
    detect_drink_water_lf_from_rows,
)


ROOT = Path(__file__).resolve().parents[1]


class DrinkWaterLFV2ContractTest(unittest.TestCase):
    def _rows(self, values: list[float], step_ms: float = 100.0) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "time_ms": [index * step_ms for index in range(len(values))],
                "imu2_roll": values,
                "imu2_valid": [1] * len(values),
            }
        )

    def _rows_with_times(self, times_ms: list[float], values: list[float]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "time_ms": times_ms,
                "imu2_roll": values,
                "imu2_valid": [1] * len(values),
            }
        )

    def _compensation(self, trunk: dict, wrist: dict | None = None) -> dict:
        return detect_compensation_4imu_4emg(
            wristF=wrist or {},
            forearmF={},
            upperF={},
            trunkF=trunk,
            emgF={},
            template_task={},
            muscles=[],
        )

    def test_positive_relative_roll_duration_triggers_lf(self) -> None:
        rows = self._rows([0.0] * 8 + [12.0] * 16)
        result = detect_drink_water_lf_from_rows(rows)
        self.assertEqual(result["version"], DRINK_WATER_LF_VERSION)
        self.assertTrue(result["detected"])
        self.assertGreaterEqual(result["max_duration_over_threshold_s"], LF_MIN_DURATION_S)

    def test_negative_relative_roll_duration_triggers_lf(self) -> None:
        rows = self._rows([0.0] * 8 + [-12.0] * 16)
        result = detect_drink_water_lf_from_rows(rows)
        self.assertTrue(result["detected"])
        self.assertGreaterEqual(result["peak_abs_roll_deg"], LF_ROLL_THRESHOLD_DEG)

    def test_long_nine_degree_roll_does_not_trigger_lf(self) -> None:
        rows = self._rows([0.0] * 8 + [9.0] * 40)
        result = detect_drink_water_lf_from_rows(rows)
        self.assertFalse(result["detected"])

    def test_short_fifteen_degree_spike_does_not_trigger_lf(self) -> None:
        rows = self._rows([0.0] * 8 + [15.0] * 3 + [0.0] * 20)
        result = detect_drink_water_lf_from_rows(rows)
        self.assertFalse(result["detected"])
        self.assertLess(result["max_duration_over_threshold_s"], LF_MIN_DURATION_S)

    def test_tremor_like_fast_threshold_crossing_does_not_trigger_lf(self) -> None:
        values = [0.0] * 8
        values.extend([12.0, -12.0, 12.0, -12.0, 12.0, -12.0] * 4)
        result = detect_drink_water_lf_from_rows(self._rows(values))
        self.assertFalse(result["detected"])

    def test_positive_and_negative_short_segments_are_not_merged(self) -> None:
        rows = self._rows([0.0] * 8 + [12.0] * 7 + [-12.0] * 7)
        result = detect_drink_water_lf_from_rows(rows)
        self.assertFalse(result["detected"])
        self.assertLess(result["positive_duration_over_threshold_s"], LF_MIN_DURATION_S)
        self.assertLess(result["negative_duration_over_threshold_s"], LF_MIN_DURATION_S)

    def test_fast_positive_negative_fifteen_degree_crossing_does_not_trigger_lf(self) -> None:
        values = [0.0] * 8
        values.extend([15.0, -15.0] * 16)
        result = detect_drink_water_lf_from_rows(self._rows(values))
        self.assertFalse(result["detected"])

    def test_normal_low_amplitude_roll_does_not_trigger_lf(self) -> None:
        rows = self._rows([0.0] * 8 + [3.0, 4.0, 5.0, 4.0, 3.0] * 5)
        result = detect_drink_water_lf_from_rows(rows)
        self.assertFalse(result["detected"])

    def test_missing_samples_are_not_interpolated_into_one_second_segment(self) -> None:
        rows = self._rows([0.0] * 8 + [12.0] * 6 + [np.nan] * 6 + [12.0] * 6)
        result = detect_drink_water_lf_from_rows(rows)
        self.assertFalse(result["detected"])
        self.assertFalse(result["uses_interpolation"])
        self.assertFalse(result["uses_zero_fill"])
        self.assertFalse(result["uses_repeat_last"])

    def test_invalid_samples_interrupt_duration_gate(self) -> None:
        rows = self._rows([0.0] * 8 + [12.0] * 5 + [None] + [12.0] * 5)
        result = detect_drink_water_lf_from_rows(rows)
        self.assertFalse(result["detected"])

    def test_missing_imu2_valid_is_insufficient_evidence(self) -> None:
        rows = self._rows([0.0] * 8 + [12.0] * 16).drop(columns=["imu2_valid"])
        result = detect_drink_water_lf_from_rows(rows)
        self.assertFalse(result["detected"])
        self.assertTrue(result["insufficient_evidence"])
        self.assertEqual(result["status"], "missing_imu2_valid")

    def test_imu2_invalid_samples_interrupt_duration_gate(self) -> None:
        rows = self._rows([0.0] * 8 + [12.0] * 5 + [12.0] + [12.0] * 5)
        rows.loc[13, "imu2_valid"] = 0
        result = detect_drink_water_lf_from_rows(rows)
        self.assertFalse(result["detected"])

    def test_large_time_gap_interrupts_same_direction_duration_gate(self) -> None:
        times = [index * 100.0 for index in range(15)]
        times.extend([2400.0 + index * 100.0 for index in range(7)])
        values = [0.0] * 8 + [12.0] * 7 + [12.0] * 7
        result = detect_drink_water_lf_from_rows(self._rows_with_times(times, values))
        self.assertFalse(result["detected"])
        self.assertEqual(result["median_dt_ms"], 100.0)
        self.assertEqual(result["max_allowed_gap_ms"], 300.0)

    def test_normal_continuous_positive_roll_still_triggers_lf(self) -> None:
        rows = self._rows([0.0] * 8 + [12.0] * 16)
        result = detect_drink_water_lf_from_rows(rows)
        self.assertTrue(result["detected"])
        self.assertGreaterEqual(result["positive_duration_over_threshold_s"], LF_MIN_DURATION_S)

    def test_neutral_uses_rep_initial_valid_roll_window(self) -> None:
        rows = self._rows([5.0] * 8 + [17.0] * 16)
        result = detect_drink_water_lf_from_rows(rows)
        self.assertTrue(result["detected"])
        self.assertAlmostEqual(result["neutral_roll_deg"], 5.0)

    def test_ratio_high_without_lf_v2_roll_gate_does_not_add_lf(self) -> None:
        result = self._compensation(
            {
                "TrunkCompensationRatio": DRINK_WATER_TRUNK_COMPENSATION_RATIO_THRESHOLD * 2.0,
                "DrinkWaterLFDetected": False,
                "DrinkWaterLFVersion": DRINK_WATER_LF_VERSION,
            }
        )
        self.assertNotIn("LF", result["compensation_types"])
        self.assertTrue(result["details"]["trunk_compensation_ratio_rule"]["trunk_compensation_ratio_rule_passed"])
        self.assertTrue(result["details"]["trunk_compensation_ratio_rule"]["diagnostic_only"])

    def test_tremor_result_does_not_participate_in_lf_detection(self) -> None:
        result = self._compensation(
            {"DrinkWaterLFDetected": False, "DrinkWaterLFVersion": DRINK_WATER_LF_VERSION},
            wrist={"DrinkWaterTremorDetected": True, "DrinkWaterTremorFraction": 0.5},
        )
        self.assertEqual(result["compensation_types"], ["TREMOR"])

    def test_score_api_top_level_contract_remains_frozen(self) -> None:
        result = score_trial_json({})
        self.assertEqual(list(result.keys()), ["score_version", "valid", "invalid_reason"])

    def test_lf_v2_rule_version_and_constants(self) -> None:
        self.assertEqual(DRINK_WATER_LF_RULE_VERSION, "drink_water_lf_v2")
        self.assertEqual(LF_ROLL_THRESHOLD_DEG, 10.0)
        self.assertEqual(LF_MIN_DURATION_S, 1.0)

    def test_real_data_validation_script_has_lf_v2_expected_gate(self) -> None:
        source = (ROOT / "tools" / "drink_water_tremor_real_data_validation_v1.ps1").read_text(encoding="utf-8")
        self.assertIn("Get-Content -LiteralPath $scoreFiles[$i].FullName -Raw -Encoding UTF8 | ConvertFrom-Json", source)
        self.assertIn("EXPECTED_LF_TP=5", source)
        self.assertIn("EXPECTED_LF_FN=0", source)
        self.assertIn("EXPECTED_LF_FP=0", source)
        self.assertIn("EXPECTED_LF_TN=24", source)
        self.assertIn("EXPECTED_TREMOR_TP=7", source)
        self.assertIn("EXPECTED_TREMOR_TN=22", source)
        self.assertIn("DRINK_WATER_LF_V2_TREMOR_V1_1_GATE=PASS", source)


if __name__ == "__main__":
    unittest.main()
