import json
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(r"C:\ec\_calibration_workspaces\stage3b_competition_geometry_20260818_01")
V2 = ROOT / "candidate_geometry_v2"
sys.path.insert(0, str(V2))

from generic_extent import estimate_generic_extent
from generic_support import group_same_surfaces
from shape_router import route_shape


class GeometryV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((V2 / "config.v2.json").read_text(encoding="utf-8"))

    def test_config_forbids_truth_and_special_cases(self):
        self.assertFalse(self.config["ground_truth_used_for_fit"])
        self.assertFalse(self.config["ground_truth_used_for_parameter_selection"])
        self.assertFalse(self.config["class_specific_fixed_compensation"])
        self.assertFalse(self.config["scene_target_name_special_cases"])

    def test_geometry_routes_slender_without_container_class(self):
        mask = np.zeros((360, 640), np.uint8)
        mask[100:260, 200:240] = 1
        result = route_shape("book", mask.astype(bool), [200, 100, 240, 260], 0.8, 30, self.config["router"])
        self.assertEqual(result["selected_family"], "upright_slender")
        self.assertEqual(result["evidence"]["upright_semantic_prior"], 0.0)

    def test_geometry_routes_round_without_ball_class(self):
        mask = np.zeros((360, 640), np.uint8)
        cv2.circle(mask, (300, 180), 50, 1, -1)
        result = route_shape("mouse", mask.astype(bool), [250, 130, 350, 230], 0.8, 25, self.config["router"])
        self.assertEqual(result["selected_family"], "roughly_spherical")
        self.assertEqual(result["evidence"]["spherical_semantic_prior"], 0.0)

    def test_generic_extent_always_finite(self):
        valid = np.zeros((20, 20), bool)
        valid[5:15, 5:15] = True
        z = np.full((20, 20), np.nan)
        z[valid] = np.linspace(100, 200, valid.sum())
        result = estimate_generic_extent(valid, z, 90, self.config["generic_extent"])
        self.assertTrue(np.isfinite(result["object_height_mm"]))
        self.assertGreater(result["object_height_mm"], 0)

    def test_consensus_fails_closed_on_negative_extent(self):
        objects = []
        for top in (100.0, 220.0):
            objects.append({"bbox_xyxy": [0, 0, 20, 20], "generic_support": {"support_mm": 150.0, "confidence": 0.8}, "final": {"support_mm": 150.0, "support_source": "x", "top_mm": top, "object_height_mm": top - 150.0}, "same_surface_group_id": None, "consensus_used": False})
        group_same_surfaces(objects, self.config["same_surface"], "synthetic")
        self.assertFalse(objects[0]["consensus_used"])
        self.assertTrue(objects[1]["consensus_used"])

    def test_completed_heldout_has_generic_and_no_catastrophe(self):
        path = V2 / "results" / "UNRELATED_HELDOUT_REPLAY.json"
        if not path.exists():
            self.skipTest("held-out replay not run")
        result = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(result["catastrophic_regression_count"], 0)
        self.assertEqual(result["finite_count"], result["candidate_object_count"])
        self.assertGreater(result["family_counts"].get("generic", 0), 0)


if __name__ == "__main__":
    unittest.main()
