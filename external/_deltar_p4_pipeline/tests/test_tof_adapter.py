from pathlib import Path

import numpy as np
import torch
import pytest

from deltar_p4_pipeline.canvas import apply_canvas
from deltar_p4_pipeline.contracts import validate_profile, validate_tof
from deltar_p4_pipeline.tof_adapter import OfficialUtilsPerJobCache, adapt_inputs
from deltar_p4_pipeline.errors import PipelineError
from tests.helpers import valid_profile, valid_tof

WORKTREE = Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128")


def test_mm_to_m_mask_hypotheses_and_patch_info() -> None:
    rgb = np.zeros((360, 640, 3), dtype=np.uint8)
    canvas = apply_canvas(rgb, "letterbox_640x480")
    inputs = adapt_inputs(canvas.rgb, canvas, validate_tof(valid_tof()), validate_profile(valid_profile()), WORKTREE)
    assert inputs.raw_hist.shape == (64, 2)
    assert torch.all(inputs.raw_hist[:, 0] == 2.0)
    assert torch.all(inputs.raw_hist[:, 1] == 0.04)
    assert inputs.hypotheses.shape == (1, 64, 16)
    assert inputs.mask.shape == (1, 64) and inputs.mask.dtype == torch.bool
    assert inputs.fr.shape == (1, 64, 4)
    assert inputs.patch_info["zone_num"].tolist() == [8]
    assert inputs.patch_info[4]["pad_size"].shape == (1, 2)


def test_candidate_mask_uses_only_documented_minimum_rule() -> None:
    tof = valid_tof()
    tof["nb_target_detected"][0] = 0
    tof["target_status"][1] = 255
    tof["target_status"][2] = 9
    profile = valid_profile(mask={"strategy": "status_count_candidate_v1"})
    canvas = apply_canvas(np.zeros((360, 640, 3), dtype=np.uint8), "letterbox_640x480")
    inputs = adapt_inputs(canvas.rgb, canvas, validate_tof(tof), validate_profile(profile), WORKTREE)
    assert inputs.mask[0, :3].tolist() == [False, False, True]
    assert any("not frozen" in warning for warning in inputs.warnings)


def test_zone_flip_reorders_raw_facts_explicitly() -> None:
    tof = valid_tof()
    tof["distance_mm"] = list(range(64))
    profile = valid_profile(zone_mapping={"strategy": "flip_horizontal"})
    canvas = apply_canvas(np.zeros((360, 640, 3), dtype=np.uint8), "letterbox_640x480")
    inputs = adapt_inputs(canvas.rgb, canvas, validate_tof(tof), validate_profile(profile), WORKTREE)
    assert inputs.raw_hist[:8, 0].tolist() == [v / 1000 for v in [7, 6, 5, 4, 3, 2, 1, 0]]


def test_effective_valid_sigma_zero_is_rejected() -> None:
    tof = valid_tof()
    tof["range_sigma_mm"][3] = 0
    canvas = apply_canvas(np.zeros((360, 640, 3), dtype=np.uint8), "letterbox_640x480")
    with pytest.raises(PipelineError) as caught:
        adapt_inputs(canvas.rgb, canvas, validate_tof(tof), validate_profile(valid_profile()), WORKTREE)
    assert caught.value.code == "E_EFFECTIVE_SIGMA_NONPOSITIVE"
    assert caught.value.details["effective_zone_indices"] == [3]


def test_masked_sigma_zero_is_accepted() -> None:
    tof = valid_tof()
    tof["range_sigma_mm"][3] = 0
    mask = [True] * 64
    mask[3] = False
    profile = valid_profile(mask={"strategy": "explicit_bool64", "values": mask})
    canvas = apply_canvas(np.zeros((360, 640, 3), dtype=np.uint8), "letterbox_640x480")
    inputs = adapt_inputs(canvas.rgb, canvas, validate_tof(tof), validate_profile(profile), WORKTREE)
    assert inputs.mask[0, 3].item() is False
    assert torch.all(inputs.hypotheses[0, 3] == 0)


def test_all_invalid_precedes_zero_sigma() -> None:
    tof = valid_tof()
    tof["range_sigma_mm"] = [0] * 64
    profile = valid_profile(mask={"strategy": "all_invalid"})
    canvas = apply_canvas(np.zeros((360, 640, 3), dtype=np.uint8), "letterbox_640x480")
    with pytest.raises(PipelineError) as caught:
        adapt_inputs(canvas.rgb, canvas, validate_tof(tof), validate_profile(profile), WORKTREE)
    assert caught.value.code == "E_NO_VALID_ZONES"


def test_official_utils_per_job_cache_hits_and_identity_change_rebuilds(monkeypatch, tmp_path) -> None:
    from deltar_p4_pipeline import tof_adapter

    loaded = []

    def fake_loader(worktree):
        module = object()
        loaded.append((Path(worktree).resolve(), module))
        return module

    monkeypatch.setattr(tof_adapter, "_official_utils", fake_loader)
    cache = OfficialUtilsPerJobCache()
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first = cache.get(first_root)
    assert cache.get(first_root) is first
    second = cache.get(second_root)
    assert second is not first
    assert [item[0] for item in loaded] == [first_root.resolve(), second_root.resolve()]
    assert cache.hits == 1
    assert cache.misses == 2


def test_cached_and_uncached_official_utils_inputs_are_exact() -> None:
    rgb = np.zeros((360, 640, 3), dtype=np.uint8)
    canvas = apply_canvas(rgb, "letterbox_640x480")
    tof = validate_tof(valid_tof())
    profile = validate_profile(valid_profile())
    uncached = adapt_inputs(canvas.rgb, canvas, tof, profile, WORKTREE)
    cache = OfficialUtilsPerJobCache()
    cached = adapt_inputs(
        canvas.rgb,
        canvas,
        tof,
        profile,
        WORKTREE,
        official_utils_cache=cache,
    )
    assert torch.equal(cached.rgb, uncached.rgb)
    assert torch.equal(cached.raw_hist, uncached.raw_hist)
    assert torch.equal(cached.hypotheses, uncached.hypotheses)
    assert torch.equal(cached.fr, uncached.fr)
    assert torch.equal(cached.mask, uncached.mask)
    assert cached.warnings == uncached.warnings
    assert cached.validation == uncached.validation
    assert cached.patch_info.keys() == uncached.patch_info.keys()
    for key in cached.patch_info:
        if isinstance(cached.patch_info[key], dict):
            assert cached.patch_info[key].keys() == uncached.patch_info[key].keys()
            assert all(
                torch.equal(cached.patch_info[key][name], uncached.patch_info[key][name])
                for name in cached.patch_info[key]
            )
        else:
            assert torch.equal(cached.patch_info[key], uncached.patch_info[key])
