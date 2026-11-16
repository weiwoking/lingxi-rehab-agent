import math
import unittest
from pathlib import Path

from deltar_p4_pipeline.core_class_contract import classify_class
from deltar_p4_pipeline.partial_object import border_flags, visibility_contract
from deltar_p4_pipeline.scan_strategy import distance_band, fov_coverage, scan_recommendation
from deltar_p4_pipeline.stage5_2b_validation import optimization_decision, stage6_contract


ROOT = Path(__file__).resolve().parents[1]
BASE = dict(mask_touches_border=False, bbox_touches_border=False, top_visible=True, bottom_visible=True,
            obvious_occlusion=False, depth_continuous=True, large_truncation=False, point_support_top=True,
            point_support_bottom=True, distance_valid=True, visible_mask_valid=True, ground_transform_valid=True,
            calibration_measured=True)


class Stage52BCompatibilityTests(unittest.TestCase):
    def test_coverage(self):
        self.assertAlmostEqual(fov_coverage(1.0)["coverage_width_m"], 2 * math.tan(math.radians(22.5)))

    def test_band(self):
        self.assertEqual(distance_band(1.25), "1.25-1.5_m")

    def test_partial_distance(self):
        value = visibility_contract(**{**BASE, "top_visible": False})
        self.assertTrue(value["distance_valid"])
        self.assertFalse(value["physical_height_valid"])

    def test_bottom_null(self):
        value = visibility_contract(**{**BASE, "bottom_visible": False})
        self.assertIsNone(value["bottom_height_m"])

    def test_border(self):
        self.assertTrue(border_flags([10, 0, 20, 30], 640, 480)["touches_top"])

    def test_classes(self):
        self.assertEqual(classify_class("book"), "core_now")
        self.assertEqual(classify_class("block"), "future_custom_class")

    def test_recommendation(self):
        self.assertEqual(scan_recommendation()["default_scan_distance_m"], 1.2)

    def test_stage6(self):
        self.assertFalse(optimization_decision(ROOT)["algorithm_optimization_needed"])
        self.assertTrue(stage6_contract()["authorized"])


if __name__ == "__main__":
    unittest.main()
