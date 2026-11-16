import json
import unittest
from pathlib import Path

import numpy as np

from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.object_geometry import compare_canvases, summarize_geometry
from deltar_p4_pipeline.point_cloud import build_point_cloud
from deltar_p4_pipeline.synthetic_geometry import evaluate_synthetic, rectangle_depth, synthetic_calibration

ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads((ROOT / "geometry_profile.json").read_text())


class LegacyObjectGeometryTests(unittest.TestCase):
    def test_known_rectangle_height_and_bottom(self):
        for bottom, height in ((0.0, 1.0), (0.3, 0.7)):
            with self.subTest(bottom=bottom, height=height):
                cal = synthetic_calibration()
                depth, mask, truth = rectangle_depth(
                    cal, distance_m=2.5, bottom_m=bottom, height_m=height
                )
                result = evaluate_synthetic(depth, mask, cal, PROFILE, truth)
                self.assertLess(result["errors"]["object_height_m"], 0.02)
                self.assertLess(result["errors"]["bottom_height_m"], 0.02)

    def test_four_distance_definitions_present(self):
        cal = synthetic_calibration()
        depth, mask, truth = rectangle_depth(
            cal, distance_m=2.5, bottom_m=0, height_m=0.8
        )
        geometry = evaluate_synthetic(depth, mask, cal, PROFILE, truth)["geometry"]
        for name in (
            "distance_axial_m",
            "distance_radial_m",
            "distance_centroid_m",
            "nearest_stable_surface_m",
        ):
            self.assertGreater(geometry[name], 0)

    def test_quantile_estimators_present(self):
        cal = synthetic_calibration()
        depth, mask, truth = rectangle_depth(
            cal, distance_m=2.5, bottom_m=0, height_m=0.8
        )
        geometry = evaluate_synthetic(depth, mask, cal, PROFILE, truth)["geometry"]
        self.assertEqual(
            set(geometry["vertical_estimators"]),
            {
                "min_max",
                "p02_p98",
                "p05_p95",
                "p10_p90",
                "mad_then_p05_p95",
            },
        )

    def test_canvas_agreement_and_disagreement(self):
        base = {
            "distance_m": 2.0,
            "object_height_m": 1.0,
            "bottom_height_m": 0.0,
            "point_count": 100,
        }
        self.assertEqual(compare_canvases(base, dict(base), PROFILE)["agreement_status"], "agree")
        changed = dict(base, distance_m=2.5)
        self.assertEqual(compare_canvases(base, changed, PROFILE)["agreement_status"], "disagree")

    def test_below_floor_rejected(self):
        pc = {
            "points_camera": np.ones((100, 3)),
            "points_ground": np.column_stack(
                [np.zeros(100), np.ones(100), np.linspace(-0.2, 0.2, 100)]
            ),
            "filter_metrics": {
                "valid_depth_ratio": 1,
                "point_retention_ratio": 1,
                "depth_mad_m": 0,
                "depth_iqr_m": 0,
                "multimodal": False,
                "touches_image_border": False,
            },
            "warnings": [],
        }
        with self.assertRaises(PipelineError):
            summarize_geometry(pc, synthetic_calibration(), PROFILE)


if __name__ == "__main__":
    unittest.main()
