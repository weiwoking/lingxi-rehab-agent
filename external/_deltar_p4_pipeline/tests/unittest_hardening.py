from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from deltar_p4_pipeline.canvas import apply_canvas
from deltar_p4_pipeline.cli import DEFAULT_CHECKPOINT, DEFAULT_WORKTREE, run_sample
from deltar_p4_pipeline.contracts import validate_profile, validate_tof
from deltar_p4_pipeline.deltar_bridge import DeltarBridge, EXPECTED_WORKTREE_COMMIT
from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.outputs import DEPTH_OUTPUT_NAMES, prepare_output_dir, write_depth_outputs, write_json
from deltar_p4_pipeline.tof_adapter import adapt_inputs
from tests.helpers import valid_profile, valid_tof

ROOT = Path(__file__).resolve().parents[1]


class FailIfCalledBridge:
    def __init__(self): self.calls = 0
    def infer_twice(self, inputs, seed):
        self.calls += 1
        raise AssertionError("forward called")


class HardeningCompatibilityTests(unittest.TestCase):
    def test_all_invalid_rejected_without_forward(self):
        sample = ROOT / "samples" / "synthetic" / "all_invalid"
        bridge = FailIfCalledBridge()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(PipelineError) as caught:
                run_sample(sample, Path(folder), bridge=bridge, device="cpu")
            self.assertEqual(caught.exception.code, "E_NO_VALID_ZONES")
            self.assertEqual(bridge.calls, 0)
            self.assertEqual(list(Path(folder).rglob("depth_m.npy")), [])

    def test_effective_sigma_contract(self):
        raw = valid_tof(); raw["range_sigma_mm"][2] = 0
        canvas = apply_canvas(np.zeros((360, 640, 3), dtype=np.uint8), "letterbox_640x480")
        with self.assertRaises(PipelineError) as caught:
            adapt_inputs(canvas.rgb, canvas, validate_tof(raw), validate_profile(valid_profile()), DEFAULT_WORKTREE)
        self.assertEqual(caught.exception.code, "E_EFFECTIVE_SIGMA_NONPOSITIVE")
        mask = [True] * 64; mask[2] = False
        inputs = adapt_inputs(canvas.rgb, canvas, validate_tof(raw), validate_profile(valid_profile(mask={"strategy": "explicit_bool64", "values": mask})), DEFAULT_WORKTREE)
        self.assertFalse(inputs.mask[0, 2].item())

    def test_center_crop_actual_integer_matrix(self):
        result = apply_canvas(np.zeros((360, 640, 3), dtype=np.uint8), "scale_to_fill_center_crop_640x480")
        self.assertEqual(result.source_to_canvas[0, 0], 853 / 640)
        self.assertEqual(result.source_to_canvas[1, 1], 480 / 360)
        self.assertTrue(np.allclose(result.source_to_canvas @ result.canvas_to_source, np.eye(3)))

    def test_worktree_commit_correct_and_wrong(self):
        self.assertEqual(DeltarBridge(DEFAULT_WORKTREE, DEFAULT_CHECKPOINT, "cpu").worktree_commit, EXPECTED_WORKTREE_COMMIT)
        completed = subprocess.CompletedProcess(["git"], 0, stdout="0" * 40 + "\n", stderr="")
        with patch("deltar_p4_pipeline.deltar_bridge.subprocess.run", return_value=completed):
            with self.assertRaises(PipelineError) as caught:
                DeltarBridge(DEFAULT_WORKTREE, DEFAULT_CHECKPOINT, "cpu")
        self.assertEqual(caught.exception.code, "E_DELTAR_WORKTREE_COMMIT")

    def test_wrong_module_source(self):
        old = sys.modules.get("src.models.deltar")
        fake = types.ModuleType("src.models.deltar"); fake.__file__ = str(ROOT / "foreign" / "deltar.py")
        sys.modules["src.models.deltar"] = fake
        try:
            with self.assertRaises(PipelineError) as caught:
                DeltarBridge(DEFAULT_WORKTREE, DEFAULT_CHECKPOINT, "cpu")
            self.assertEqual(caught.exception.code, "E_DELTAR_MODULE_SOURCE")
        finally:
            if old is None: sys.modules.pop("src.models.deltar", None)
            else: sys.modules["src.models.deltar"] = old

    def test_bad_source_metadata_json(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); sample = root / "bad_metadata"
            shutil.copytree(ROOT / "samples" / "synthetic" / "flat_wall_all_valid", sample)
            (sample / "source_metadata.json").write_text("{", encoding="utf-8")
            with self.assertRaises(PipelineError) as caught:
                run_sample(sample, root / "outputs", bridge=FailIfCalledBridge(), device="cpu")
            self.assertEqual(caught.exception.code, "E_JSON_PARSE")

    def test_output_write_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            canvas = apply_canvas(np.zeros((360, 640, 3), dtype=np.uint8), "letterbox_640x480")
            with patch("deltar_p4_pipeline.outputs.np.save", side_effect=OSError("disk full")):
                with self.assertRaises(PipelineError) as caught:
                    write_depth_outputs(root, np.ones((480, 640), dtype=np.float32), canvas)
            self.assertEqual(caught.exception.code, "E_OUTPUT_WRITE")
            self.assertFalse(any((root / name).exists() for name in DEPTH_OUTPUT_NAMES))
            with self.assertRaises(PipelineError) as json_caught:
                write_json(root, {"x": 1})
            self.assertEqual(json_caught.exception.code, "E_OUTPUT_WRITE")
            blocked = root / "blocked"; blocked.write_text("file", encoding="utf-8")
            with self.assertRaises(PipelineError) as mkdir_caught:
                prepare_output_dir(blocked / "child")
            self.assertEqual(mkdir_caught.exception.code, "E_OUTPUT_WRITE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
