from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import pytest

from deltar_p4_pipeline import stage7d_outputs
from deltar_p4_pipeline.stage7d_outputs import Stage7DProcessor, _save_stage7d_visuals


def _inputs() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(20260810)
    world = rng.normal(0.0, 1000.0, size=(360, 640, 3)).astype(np.float64)
    valid = rng.random((360, 640)) > 0.08
    rgb = rng.integers(0, 256, size=(360, 640, 3), dtype=np.uint8)
    return world, valid, rgb


def _write(root: Path, workers: int) -> None:
    world, valid, rgb = _inputs()
    _save_stage7d_visuals(
        root / "world.png", world, valid, root / "overlay.png", rgb,
        "axial_z", [], [], workers,
    )


def test_parallel_stage7d_visuals_are_byte_exact(tmp_path: Path) -> None:
    sequential = tmp_path / "sequential"
    parallel = tmp_path / "parallel"
    sequential.mkdir()
    parallel.mkdir()
    _write(sequential, 1)
    _write(parallel, 2)
    assert (parallel / "world.png").read_bytes() == (sequential / "world.png").read_bytes()
    assert (parallel / "overlay.png").read_bytes() == (sequential / "overlay.png").read_bytes()


def test_parallel_stage7d_visual_workers_are_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock = threading.Lock()
    active = 0
    maximum = 0

    def observed(*args, **kwargs) -> None:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.03)
        with lock:
            active -= 1

    monkeypatch.setattr(stage7d_outputs, "_save_world_height_visual", observed)
    monkeypatch.setattr(stage7d_outputs, "_save_overlay", observed)
    world, valid, rgb = _inputs()
    _save_stage7d_visuals(
        tmp_path / "world.png", world, valid, tmp_path / "overlay.png", rgb,
        "axial_z", [], [], 2,
    )
    assert maximum == 2
    assert active == 0
    assert not any(t.name.startswith("phase6-stage7d-png") for t in threading.enumerate())


def test_parallel_stage7d_visual_failure_propagates_after_shutdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def failing(*args, **kwargs) -> None:
        raise OSError("injected Stage7D visual failure")

    def delayed(*args, **kwargs) -> None:
        time.sleep(0.02)

    monkeypatch.setattr(stage7d_outputs, "_save_world_height_visual", failing)
    monkeypatch.setattr(stage7d_outputs, "_save_overlay", delayed)
    world, valid, rgb = _inputs()
    with pytest.raises(OSError, match="injected Stage7D visual failure"):
        _save_stage7d_visuals(
            tmp_path / "world.png", world, valid, tmp_path / "overlay.png", rgb,
            "axial_z", [], [], 2,
        )
    assert not any(t.name.startswith("phase6-stage7d-png") for t in threading.enumerate())


@pytest.mark.parametrize("workers", [0, 3])
def test_stage7d_visual_worker_bound_is_rejected(workers: int, tmp_path: Path) -> None:
    world, valid, rgb = _inputs()
    with pytest.raises(ValueError, match="between 1 and 2"):
        _save_stage7d_visuals(
            tmp_path / "world.png", world, valid, tmp_path / "overlay.png", rgb,
            "axial_z", [], [], workers,
        )


def test_stage7d_processor_worker_bound_is_rejected(tmp_path: Path) -> None:
    processor = object.__new__(Stage7DProcessor)
    processor.output_root = tmp_path
    processor.ransac_batch_bound = 1
    processor.visual_png_workers = 3
    with pytest.raises(ValueError, match="between 1 and 2"):
        processor.__post_init__()
