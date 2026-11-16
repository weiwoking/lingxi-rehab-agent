from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from deltar_p4_pipeline.horizontal_planes import extract_horizontal_planes


ROOT = Path(__file__).resolve().parents[1]


def _exact(left: Any, right: Any) -> None:
    assert type(left) is type(right)
    if isinstance(left, np.ndarray):
        assert left.dtype == right.dtype
        assert left.shape == right.shape
        assert np.array_equal(left, right, equal_nan=True)
    elif isinstance(left, dict):
        assert list(left) == list(right)
        for key in left:
            _exact(left[key], right[key])
    elif isinstance(left, list):
        assert len(left) == len(right)
        for a, b in zip(left, right):
            _exact(a, b)
    else:
        assert left == right


def _fixture() -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    height, width = 90, 120
    yy, xx = np.mgrid[0:height, 0:width]
    world = np.empty((height, width, 3), dtype=np.float64)
    world[..., 0] = (xx - width / 2) * 11.0
    world[..., 1] = yy * 13.0
    world[..., 2] = np.where(yy >= 48, 0.0, 680.0)
    rng = np.random.default_rng(20260810)
    world[..., 2] += rng.normal(0.0, 0.4, size=(height, width))
    valid = rng.random((height, width)) > 0.02
    world[~valid] = np.nan
    profile = json.loads(
        (ROOT / "profiles" / "stage7d_horizontal_plane_profile.json").read_text(
            encoding="utf-8"
        )
    )
    profile.update(
        {
            "fit_stride_px": 2,
            "ransac_iterations": 96,
            "min_ground_pixels": 500,
            "min_support_pixels": 400,
            "min_visible_span_px": 20,
        }
    )
    return world, valid, profile


def test_batched_extraction_is_complete_result_exact() -> None:
    world, valid, profile = _fixture()
    baseline = extract_horizontal_planes(world, valid.copy(), profile)
    candidate = extract_horizontal_planes(
        world, valid.copy(), profile, ransac_batch_bound=32
    )
    _exact(baseline, candidate)


def test_default_path_does_not_enter_batch_matrix(monkeypatch) -> None:
    world, valid, profile = _fixture()

    def forbidden(*args, **kwargs):
        raise AssertionError("default extraction entered the batched path")

    monkeypatch.setattr(np, "column_stack", forbidden)
    extract_horizontal_planes(world, valid, profile)


def test_batch_width_never_exceeds_32(monkeypatch) -> None:
    world, valid, profile = _fixture()
    original = np.column_stack
    observed = []

    def checked(arrays):
        result = original(arrays)
        observed.append(result.shape[1])
        return result

    monkeypatch.setattr(np, "column_stack", checked)
    extract_horizontal_planes(world, valid, profile, ransac_batch_bound=32)
    assert observed
    assert max(observed) <= 32


@pytest.mark.parametrize("bound", [0, 33])
def test_batch_bound_rejects_out_of_range(bound: int) -> None:
    world, valid, profile = _fixture()
    with pytest.raises(ValueError, match="between 1 and 32"):
        extract_horizontal_planes(
            world, valid, profile, ransac_batch_bound=bound
        )
