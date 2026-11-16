import json
from pathlib import Path

import numpy as np
import pytest

from deltar_p4_pipeline.cli import DEFAULT_CHECKPOINT, DEFAULT_WORKTREE, run_sample
from deltar_p4_pipeline.deltar_bridge import DeltarBridge

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_direct_and_letterbox_cuda_inference_without_gt(tmp_path: Path) -> None:
    bridge = DeltarBridge(DEFAULT_WORKTREE, DEFAULT_CHECKPOINT, "cuda:0")
    cases = (("official_000_letterbox", [480, 640]), ("official_000_direct_360", [360, 640]))
    for sample_name, expected_shape in cases:
        sample = ROOT / "samples" / "official_reverse" / sample_name
        assert not (sample / "depth.npy").exists() and not (sample / "depth.png").exists()
        result = run_sample(sample, tmp_path, bridge=bridge)
        assert result["status"] == "success"
        assert result["deltar_input"]["rgb_shape"] == [1, 3, *expected_shape]
        assert result["deltar_input"]["hist_shape"] == [1, 64, 16]
        assert result["depth"]["shape"] == expected_shape
        assert result["depth"]["dtype"] == "float32"
        assert result["depth"]["finite_ratio"] == 1.0
        assert result["depth"]["positive_ratio"] == 1.0
        assert result["deltar_provenance"]["worktree_commit"] == "f3c517562bcb2dfb9e4bbf74965f2ee76a2dd096"
        output_dir = tmp_path / sample.name / result["profile_id"]
        depth = np.load(output_dir / "depth_m.npy", allow_pickle=False)
        validation = json.loads((output_dir / "validation_report.json").read_text(encoding="utf-8"))
        assert depth.dtype == np.float32
        assert validation["repeatability"]["float_exact_equal"] is True
