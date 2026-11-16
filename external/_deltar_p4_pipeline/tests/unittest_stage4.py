import json
import unittest
from pathlib import Path

import numpy as np

from deltar_p4_pipeline.calibration import invert_transform, transform_points, validate_calibration
from deltar_p4_pipeline.camera_geometry import backproject_pixels
from deltar_p4_pipeline.geometry_quality import compute_absolute_geometry_valid
from deltar_p4_pipeline.object_geometry import compare_canvases
from deltar_p4_pipeline.stage4_validation import select_canvas_geometry
from deltar_p4_pipeline.synthetic_geometry import evaluate_synthetic, rectangle_depth, semantics_roundtrip_error, synthetic_calibration

ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads((ROOT / "geometry_profile.json").read_text())


class Stage4UnitTests(unittest.TestCase):
    def test_calibration(self):
        self.assertEqual(validate_calibration(synthetic_calibration())["profile_id"], "synthetic_test_calibration")

    def test_transform_inverse(self):
        cal = synthetic_calibration(roll_deg=2, pitch_deg=3, yaw_deg=4); t = cal["transforms"]["T_ground_from_camera"]
        p = np.array([[1.0, 2.0, 3.0]])
        self.assertTrue(np.allclose(transform_points(transform_points(p, t), invert_transform(t)), p))

    def test_axial(self):
        camera = synthetic_calibration()["camera_model"]
        self.assertTrue(np.allclose(backproject_pixels(np.array([[319.5, 179.5]]), np.array([2.0]), camera, "axial_z"), [[0, 0, 2]]))

    def test_radial(self):
        camera = synthetic_calibration()["camera_model"]
        p = backproject_pixels(np.array([[20.0, 30.0]]), np.array([2.0]), camera, "radial_range")
        self.assertAlmostEqual(float(np.linalg.norm(p)), 2.0)

    def test_semantics_bias(self):
        self.assertGreater(semantics_roundtrip_error("axial_z")["wrong_semantics_median_point_error_m"], 0.02)

    def test_floor_rectangle(self):
        cal = synthetic_calibration(); depth, mask, truth = rectangle_depth(cal, distance_m=2.5, bottom_m=0, height_m=1)
        result = evaluate_synthetic(depth, mask, cal, PROFILE, truth)
        self.assertLess(result["errors"]["object_height_m"], 0.02)

    def test_elevated_rectangle(self):
        cal = synthetic_calibration(); depth, mask, truth = rectangle_depth(cal, distance_m=2.5, bottom_m=.3, height_m=.7)
        self.assertLess(evaluate_synthetic(depth, mask, cal, PROFILE, truth)["errors"]["bottom_height_m"], .02)

    def test_canvas_agreement(self):
        item = {"distance_m": 2, "object_height_m": 1, "bottom_height_m": 0, "point_count": 100}
        self.assertEqual(compare_canvases(item, item, PROFILE)["agreement_status"], "agree")

    def test_real_input_counts(self):
        manifest = json.loads((ROOT / "outputs_stage4" / "input_groups_manifest.json").read_text())
        self.assertEqual(manifest["counts"]["groups"], 61)

    def test_synthetic_count(self):
        matrix = json.loads((ROOT / "outputs_stage4" / "synthetic_matrix.json").read_text())
        self.assertEqual(matrix["summary"]["passed"], 10)

    def test_official_not_absolute(self):
        matrix = json.loads((ROOT / "outputs_stage4" / "official_matrix.json").read_text())
        self.assertFalse(matrix["absolute_geometry_valid"])

    def test_sensitivity_count(self):
        report = json.loads((ROOT / "outputs_stage4" / "sensitivity_report.json").read_text())
        self.assertEqual(report["case_count"], 40)

    def test_canvas_disagreement_selects_nothing(self):
        item = {"distance_m": 2, "object_height_m": 1, "bottom_height_m": 0, "point_count": 100, "warnings": []}
        results = {
            "direct_640x360": {"status": "success", "geometry": item},
            "letterbox_640x480": {"status": "success", "geometry": dict(item, distance_m=3)},
        }
        selected = select_canvas_geometry(results, {"agreement_status": "disagree"})
        self.assertEqual(selected["canvas_selection_status"], "disagreed")
        self.assertIsNone(selected["selected_geometry"])

    def test_absolute_geometry_requires_all_conditions(self):
        common = dict(
            calibration_status="measured",
            depth_semantics_status="PROVEN",
            selected_geometry_status="success",
            canvas_selection_status="agreed",
            automatic_geometry_valid=True,
            critical_geometry_rejections=[],
        )
        self.assertTrue(compute_absolute_geometry_valid(**common))
        common["depth_semantics_status"] = "PLAUSIBLE_BUT_UNPROVEN"
        self.assertFalse(compute_absolute_geometry_valid(**common))

    def test_stage4_1_summary(self):
        summary = json.loads((ROOT / "outputs_stage4_1" / "hardening_summary.json").read_text())
        self.assertTrue(summary["unit_equivalence_tests"]["passed"])
        self.assertEqual(summary["absolute_geometry_valid_true_count"], 0)


if __name__ == "__main__":
    unittest.main()
