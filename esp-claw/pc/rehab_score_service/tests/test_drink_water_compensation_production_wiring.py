from __future__ import annotations

import math
import unittest

from cloud_interface.cloud_score_api import json_to_score_row
from feature_extractors import compensation_detector as detector


class DrinkWaterCompensationProductionWiringTests(unittest.TestCase):
    def _trial(self, **overrides):
        trial = {
            "TaskNumber": 12,
            "WristMeanSpeed_mm_s": 1.0,
            "NumSpeedPeaks": 1,
            "PauseCount": 0,
            "ForearmRotationRange_deg": 10.0,
            "ForearmMeanAngularSpeed_deg_s": 2.0,
            "UpperArmROM_mm": 40.0,
            "UpperArmMeanAngularSpeed_deg_s": 2.0,
            "TrunkCompensationRatio": 0.05,
            "TrunkROM_deg": 8.0,
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
        trial.update(overrides)
        return trial

    def _detect(self, trunk_ratio=None, trunk_lf=0.0, trunk_tr=0.0, final_score=None):
        trunk = {
            "TrunkForwardLean_deg": trunk_lf,
            "TrunkRotation_deg": trunk_tr,
        }
        if trunk_ratio is not None:
            trunk["TrunkCompensationRatio"] = trunk_ratio
        if final_score is not None:
            trunk["final_score"] = final_score
        return detector.detect_compensation_4imu_4emg(
            wristF={"num_speed_peaks": 1, "pause_count": 0},
            forearmF={"rotation_range": 10.0},
            upperF={"ROM": 40.0},
            trunkF=trunk,
            emgF={},
            template_task={
                "ROM_mean": 40.0,
                "ROM_std": 5.0,
                "forearm_rotation_mean": 10.0,
                "forearm_rotation_std": 3.0,
            },
            muscles=[],
        )

    def test_01_score_row_passes_real_trunk_compensation_ratio(self):
        row = json_to_score_row(self._trial(TrunkCompensationRatio="0.123"))
        self.assertAlmostEqual(float(row["TrunkCompensationRatio"]), 0.123)

    def test_02_score_row_does_not_write_ratio_as_forward_lean(self):
        row = json_to_score_row(self._trial(TrunkCompensationRatio=0.123))
        self.assertEqual(float(row["TrunkForwardLean_deg"]), 0.0)

    def test_03_score_row_does_not_fill_missing_ratio_with_zero(self):
        trial = self._trial()
        trial.pop("TrunkCompensationRatio")
        row = json_to_score_row(trial)
        self.assertNotIn("TrunkCompensationRatio", row.index)

    def test_04_score_row_rejects_nan_ratio(self):
        with self.assertRaises(ValueError):
            json_to_score_row(self._trial(TrunkCompensationRatio=math.nan))

    def test_05_score_row_rejects_inf_ratio(self):
        with self.assertRaises(ValueError):
            json_to_score_row(self._trial(TrunkCompensationRatio=math.inf))

    def test_06_ratio_below_threshold_does_not_hit_lf(self):
        result = self._detect(trunk_ratio=detector.DRINK_WATER_TRUNK_COMPENSATION_RATIO_THRESHOLD - 1e-9)
        self.assertFalse(result["has_compensation"])
        self.assertNotIn("LF", result["compensation_types"])

    def test_07_ratio_equal_threshold_hits_lf(self):
        result = self._detect(trunk_ratio=detector.DRINK_WATER_TRUNK_COMPENSATION_RATIO_THRESHOLD)
        self.assertTrue(result["has_compensation"])
        self.assertIn("LF", result["compensation_types"])

    def test_08_ratio_above_threshold_hits_lf(self):
        result = self._detect(trunk_ratio=detector.DRINK_WATER_TRUNK_COMPENSATION_RATIO_THRESHOLD + 1e-9)
        self.assertTrue(result["has_compensation"])
        self.assertEqual(result["compensation_types"], ["LF"])

    def test_09_new_and_old_lf_rules_do_not_duplicate_lf(self):
        result = self._detect(
            trunk_ratio=detector.DRINK_WATER_TRUNK_COMPENSATION_RATIO_THRESHOLD,
            trunk_lf=16.0,
        )
        self.assertEqual(result["compensation_types"].count("LF"), 1)

    def test_10_tr_old_rule_is_unchanged(self):
        result = self._detect(trunk_ratio=0.0, trunk_tr=16.0)
        self.assertIn("TR", result["compensation_types"])
        self.assertNotIn("LF", result["compensation_types"])

    def test_11_quality_findings_are_unchanged_by_ratio_hit(self):
        low = self._detect(trunk_ratio=0.0)
        high = self._detect(trunk_ratio=detector.DRINK_WATER_TRUNK_COMPENSATION_RATIO_THRESHOLD)
        self.assertEqual(low["quality_findings"], high["quality_findings"])

    def test_12_detector_does_not_use_final_score(self):
        low_score = self._detect(trunk_ratio=0.0, final_score=0.0)
        high_score = self._detect(trunk_ratio=0.0, final_score=100.0)
        self.assertEqual(low_score["has_compensation"], high_score["has_compensation"])

    def test_13_threshold_constant_is_exact(self):
        self.assertEqual(detector.DRINK_WATER_TRUNK_COMPENSATION_RATIO_THRESHOLD, 0.09885350515694372)

    def test_14_rule_version_is_exact(self):
        self.assertEqual(detector.DRINK_WATER_TRUNK_COMPENSATION_RATIO_RULE_VERSION, "drink_water_ratio_lf_v1")

    def test_15_detector_details_include_ratio_rule_trace(self):
        result = self._detect(trunk_ratio=0.123)
        trace = result["details"]["trunk_compensation_ratio_rule"]
        self.assertEqual(trace["trunk_compensation_ratio"], 0.123)
        self.assertEqual(trace["trunk_compensation_ratio_threshold"], detector.DRINK_WATER_TRUNK_COMPENSATION_RATIO_THRESHOLD)
        self.assertTrue(trace["trunk_compensation_ratio_rule_passed"])
        self.assertEqual(trace["trunk_compensation_ratio_rule_version"], "drink_water_ratio_lf_v1")


if __name__ == "__main__":
    unittest.main()
