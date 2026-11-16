import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(r"C:\ec\_calibration_workspaces\stage3b_competition_geometry_20260818_01")
sys.path.insert(0, str(ROOT / "candidate_geometry"))
import stage3b_geometry_candidate as candidate


class CandidateGeometryTests(unittest.TestCase):
    def test_config_explicitly_forbids_truth_fit(self):
        config = json.loads(candidate.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertIs(config["ground_truth_used_for_fit"], False)
        self.assertNotIn("truth", config["silhouette"])

    def test_detector_ellipse_is_bounded(self):
        mask = candidate._ellipse_from_bbox([10, 20, 30, 60], (100, 100))
        yy, xx = np.nonzero(mask)
        self.assertGreater(mask.sum(), 0)
        self.assertGreaterEqual(xx.min(), 10)
        self.assertLessEqual(xx.max(), 30)
        self.assertGreaterEqual(yy.min(), 20)
        self.assertLessEqual(yy.max(), 60)

    def test_safe_replace_rejects_outside_candidate_results(self):
        with self.assertRaises(RuntimeError):
            candidate._safe_replace_directory(ROOT / "results")

    def test_error_identity_in_completed_replay(self):
        summary = candidate.RESULT_ROOT / "SUMMARY.candidate.json"
        if not summary.exists():
            self.skipTest("replay not run")
        document = json.loads(summary.read_text(encoding="utf-8"))
        for sample in document["samples"].values():
            for item in sample["targets"].values():
                for name in ("baseline", "candidate"):
                    value = item[name]
                    if value.get("available", True):
                        self.assertAlmostEqual(value["top_mm"], value["support_mm"] + value["object_height_mm"], places=9)


if __name__ == "__main__":
    unittest.main()
