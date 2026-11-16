from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT=Path(__file__).parents[1]


class Stage3ArtifactIntegrationTests(unittest.TestCase):
    def test_matrix(self):
        matrix=json.loads((ROOT/"outputs_stage3"/"stage3_matrix.json").read_text(encoding="utf-8"))
        self.assertEqual(matrix["status"],"pass"); self.assertGreaterEqual(matrix["official_sample_count"],10)
    def test_matching(self):
        matrix=json.loads((ROOT/"outputs_stage3"/"stage3_matrix.json").read_text(encoding="utf-8"))
        self.assertGreater(matrix["matched_object_count"],0)
    def test_gt_separation(self):
        result=json.loads((ROOT/"outputs_stage3"/"official_144"/"object_depth_results.json").read_text(encoding="utf-8"))
        self.assertFalse(result["gt_used_for_inference"])
    def test_source_shapes(self):
        report=json.loads((ROOT/"outputs_stage3"/"official_144"/"validation_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["direct_source_shape"],[360,640]); self.assertEqual(report["letterbox_source_shape"],[360,640])


if __name__=="__main__": unittest.main()
