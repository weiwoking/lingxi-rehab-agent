import json
from pathlib import Path

import numpy as np
import pytest

from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.object_geometry import compare_canvases
from deltar_p4_pipeline.stage4_validation import evaluate_candidate
from deltar_p4_pipeline.synthetic_geometry import synthetic_calibration

ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads((ROOT / "geometry_profile.json").read_text())


def test_one_canvas_rejected_is_not_silent_agreement():
    assert compare_canvases(None, {"distance_m": 1}, PROFILE)["agreement_status"] == "rejected_candidate"


def test_missing_mask_key_rejected(tmp_path):
    np.save(tmp_path / "depth.npy", np.ones((360, 640)))
    np.savez_compressed(tmp_path / "mask.npz", other=np.ones((360, 640), bool))
    candidate = {"source_type": "segmentation", "mask_npz_path": "mask.npz", "mask_key": "missing", "bbox_xyxy": [0, 0, 10, 10], "canvas_candidates": {}}
    group = {"source_depth_refs": {"direct_640x360": "depth.npy", "letterbox_640x480": "depth.npy"}}
    result = evaluate_candidate(candidate, group, synthetic_calibration(), PROFILE, tmp_path)
    assert not result["success"]
    assert all(item["error"]["error_code"] == "E_STAGE4_MASK_KEY" for item in result["canvas_results"].values())


def test_optional_in_memory_arrays_are_exact_and_skip_file_loads(tmp_path, monkeypatch):
    depth = np.linspace(0.7, 1.4, 360 * 640, dtype=np.float32).reshape(360, 640)
    mask = np.zeros((360, 640), dtype=bool)
    mask[100:260, 180:360] = True
    np.save(tmp_path / "depth.npy", depth, allow_pickle=False)
    np.savez_compressed(tmp_path / "mask.npz", selected=mask)
    candidate = {
        "source_type": "segmentation",
        "mask_npz_path": "mask.npz",
        "mask_key": "selected",
        "bbox_xyxy": [180, 100, 360, 260],
        "canvas_candidates": {},
    }
    group = {
        "source_depth_refs": {
            "direct_640x360": "depth.npy",
            "letterbox_640x480": "depth.npy",
        }
    }
    baseline = evaluate_candidate(candidate, group, synthetic_calibration(), PROFILE, tmp_path)
    monkeypatch.setattr(np, "load", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("file load used")))
    candidate_result = evaluate_candidate(
        candidate,
        group,
        synthetic_calibration(),
        PROFILE,
        tmp_path,
        depth_arrays={"direct_640x360": depth, "letterbox_640x480": depth},
        segment_masks={"selected": mask},
    )
    assert candidate_result == baseline


def test_optional_in_memory_missing_key_preserves_file_fallback(tmp_path, monkeypatch):
    depth = np.ones((360, 640), dtype=np.float32)
    np.save(tmp_path / "depth.npy", depth, allow_pickle=False)
    candidate = {
        "source_type": "detection",
        "bbox_xyxy": [10, 10, 100, 100],
        "canvas_candidates": {},
    }
    group = {"source_depth_refs": {"direct_640x360": "depth.npy"}}
    calls = []
    original = np.load

    def tracked(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(np, "load", tracked)
    evaluate_candidate(
        candidate,
        group,
        synthetic_calibration(),
        PROFILE,
        tmp_path,
        depth_arrays={},
    )
    assert calls == [tmp_path / "depth.npy"]


def test_unmeasured_calibration_never_claims_absolute_validity():
    from scripts.run_stage4_official_matrix import run
    result = run()
    assert result["absolute_geometry_valid"] is False
    assert all(not row["absolute_geometry_valid"] for row in result["objects"])
