from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from deltar_p4_pipeline.cli import run_sample
from deltar_p4_pipeline.cli import DEFAULT_CHECKPOINT, DEFAULT_WORKTREE
from deltar_p4_pipeline.deltar_bridge import DeltarBridge

ROOT = Path(__file__).resolve().parents[1]


class OfficialCudaIntegrationTests(unittest.TestCase):
    def test_direct_and_letterbox_without_gt_and_exact_repeat(self):
        bridge = DeltarBridge(DEFAULT_WORKTREE, DEFAULT_CHECKPOINT, "cuda:0")
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            for sample_name, shape in (("official_000_letterbox", [480, 640]), ("official_000_direct_360", [360, 640])):
                sample = ROOT / "samples" / "official_reverse" / sample_name
                self.assertFalse((sample / "depth.npy").exists())
                result = run_sample(sample, output, bridge=bridge)
                self.assertEqual(result["status"], "success")
                self.assertEqual(result["deltar_input"]["rgb_shape"], [1, 3, *shape])
                self.assertEqual(result["deltar_input"]["hist_shape"], [1, 64, 16])
                self.assertEqual(result["depth"]["shape"], shape)
                self.assertEqual(result["depth"]["dtype"], "float32")
                self.assertEqual(result["depth"]["finite_ratio"], 1.0)
                self.assertEqual(result["depth"]["positive_ratio"], 1.0)
                output_dir = output / sample.name / result["profile_id"]
                depth = np.load(output_dir / "depth_m.npy", allow_pickle=False)
                self.assertEqual(depth.dtype, np.float32)
                self.assertTrue((output_dir / "depth_mm.png").is_file())
                self.assertTrue((output_dir / "validation_report.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
