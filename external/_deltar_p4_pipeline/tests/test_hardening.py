from __future__ import annotations

import json
import shutil
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from deltar_p4_pipeline.canvas import apply_canvas
from deltar_p4_pipeline.cli import DEFAULT_CHECKPOINT, DEFAULT_WORKTREE, run_sample
from deltar_p4_pipeline.deltar_bridge import DeltarBridge, EXPECTED_WORKTREE_COMMIT, import_official_module
from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.outputs import DEPTH_OUTPUT_NAMES, prepare_output_dir, write_depth_outputs, write_json

ROOT = Path(__file__).resolve().parents[1]


class FailIfCalledBridge:
    def __init__(self) -> None:
        self.calls = 0

    def infer_twice(self, inputs, seed):
        self.calls += 1
        raise AssertionError("model forward must not be called")


def test_all_invalid_rejected_without_model_or_stale_depth(tmp_path: Path) -> None:
    sample = ROOT / "samples" / "synthetic" / "all_invalid"
    profile_id = json.loads((sample / "profile.json").read_text(encoding="utf-8"))["profile_id"]
    output_dir = tmp_path / sample.name / profile_id
    output_dir.mkdir(parents=True)
    for name in DEPTH_OUTPUT_NAMES:
        (output_dir / name).write_bytes(b"stale")
    bridge = FailIfCalledBridge()
    with pytest.raises(PipelineError) as caught:
        run_sample(sample, tmp_path, bridge=bridge, device="cpu")
    assert caught.value.code == "E_NO_VALID_ZONES"
    assert bridge.calls == 0
    assert not any((output_dir / name).exists() for name in DEPTH_OUTPUT_NAMES)
    result = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
    validation = json.loads((output_dir / "validation_report.json").read_text(encoding="utf-8"))
    assert result["status"] == "rejected" and result["model_forward_called"] is False
    assert validation["depth_outputs_present"] == []


def test_valid_sigma_zero_rejected_without_model(tmp_path: Path) -> None:
    source = ROOT / "samples" / "synthetic" / "flat_wall_all_valid"
    sample = tmp_path / "valid_sigma_zero"
    shutil.copytree(source, sample)
    tof = json.loads((sample / "tof.json").read_text(encoding="utf-8"))
    tof["range_sigma_mm"][5] = 0
    (sample / "tof.json").write_text(json.dumps(tof), encoding="utf-8")
    bridge = FailIfCalledBridge()
    with pytest.raises(PipelineError) as caught:
        run_sample(sample, tmp_path / "outputs", bridge=bridge, device="cpu")
    assert caught.value.code == "E_EFFECTIVE_SIGMA_NONPOSITIVE"
    assert caught.value.details["effective_zone_indices"] == [5]
    assert bridge.calls == 0


def test_bad_source_metadata_json_is_stable_error(tmp_path: Path) -> None:
    source = ROOT / "samples" / "synthetic" / "flat_wall_all_valid"
    sample = tmp_path / "bad_metadata"
    shutil.copytree(source, sample)
    (sample / "source_metadata.json").write_text("{", encoding="utf-8")
    with pytest.raises(PipelineError) as caught:
        run_sample(sample, tmp_path / "outputs", bridge=FailIfCalledBridge(), device="cpu")
    assert caught.value.code == "E_JSON_PARSE"
    assert caught.value.field.endswith("source_metadata.json")


def test_output_failures_map_to_stable_code_and_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    depth = np.ones((480, 640), dtype=np.float32)
    canvas = apply_canvas(np.zeros((360, 640, 3), dtype=np.uint8), "letterbox_640x480")
    monkeypatch.setattr("deltar_p4_pipeline.outputs.np.save", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(PipelineError) as caught:
        write_depth_outputs(tmp_path, depth, canvas)
    assert caught.value.code == "E_OUTPUT_WRITE"
    assert not any((tmp_path / name).exists() for name in DEPTH_OUTPUT_NAMES)
    with pytest.raises(PipelineError) as json_caught:
        write_json(tmp_path, {"x": 1})
    assert json_caught.value.code == "E_OUTPUT_WRITE"
    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("file, not directory", encoding="utf-8")
    with pytest.raises(PipelineError) as mkdir_caught:
        prepare_output_dir(blocked_parent / "child")
    assert mkdir_caught.value.code == "E_OUTPUT_WRITE"


def test_worktree_commit_and_correct_module_cache() -> None:
    bridge = DeltarBridge(DEFAULT_WORKTREE, DEFAULT_CHECKPOINT, device="cpu")
    assert bridge.worktree_commit == EXPECTED_WORKTREE_COMMIT
    module = import_official_module("src.utils.dataloader", DEFAULT_WORKTREE)
    assert Path(module.__file__).resolve().is_relative_to(DEFAULT_WORKTREE.resolve())
    second = DeltarBridge(DEFAULT_WORKTREE, DEFAULT_CHECKPOINT, device="cpu")
    assert second.worktree_commit == EXPECTED_WORKTREE_COMMIT


def test_wrong_worktree_commit_is_rejected() -> None:
    completed = subprocess.CompletedProcess(["git"], 0, stdout="0" * 40 + "\n", stderr="")
    with patch("deltar_p4_pipeline.deltar_bridge.subprocess.run", return_value=completed):
        with pytest.raises(PipelineError) as caught:
            DeltarBridge(DEFAULT_WORKTREE, DEFAULT_CHECKPOINT, device="cpu")
    assert caught.value.code == "E_DELTAR_WORKTREE_COMMIT"


def test_preloaded_wrong_module_source_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = types.ModuleType("src.models.deltar")
    fake.__file__ = str(tmp_path / "foreign" / "deltar.py")
    monkeypatch.setitem(sys.modules, "src.models.deltar", fake)
    with pytest.raises(PipelineError) as caught:
        DeltarBridge(DEFAULT_WORKTREE, DEFAULT_CHECKPOINT, device="cpu")
    assert caught.value.code == "E_DELTAR_MODULE_SOURCE"
