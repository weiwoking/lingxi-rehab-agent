from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import pytest

from deltar_p4_pipeline import frame_pipeline
from deltar_p4_pipeline.frame_pipeline import FramePipeline, FramePipelineError, _write_source_pngs


def _images() -> list[np.ndarray]:
    rng = np.random.default_rng(20260810)
    return [
        np.zeros((17, 29, 3), dtype=np.uint8),
        np.full((31, 19, 3), 255, dtype=np.uint8),
        rng.integers(0, 256, size=(360, 640, 3), dtype=np.uint8),
        rng.integers(0, 256, size=(73, 91, 3), dtype=np.uint8),
    ]


def test_parallel_source_pngs_are_byte_exact(tmp_path: Path) -> None:
    images = _images()
    sequential = [(tmp_path / "sequential" / f"{index}.png", rgb) for index, rgb in enumerate(images)]
    parallel = [(tmp_path / "parallel" / f"{index}.png", rgb) for index, rgb in enumerate(images)]
    (tmp_path / "sequential").mkdir()
    (tmp_path / "parallel").mkdir()

    _write_source_pngs(sequential, workers=1)
    _write_source_pngs(parallel, workers=4)

    assert [path.read_bytes() for path, _ in parallel] == [
        path.read_bytes() for path, _ in sequential
    ]


def test_parallel_source_png_workers_are_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lock = threading.Lock()
    active = 0
    maximum = 0

    def observed_save(path: Path, rgb: np.ndarray) -> None:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.03)
        with lock:
            active -= 1

    monkeypatch.setattr(frame_pipeline, "_save_source_png", observed_save)
    items = [(tmp_path / f"{index}.png", np.zeros((1, 1, 3), dtype=np.uint8)) for index in range(11)]

    _write_source_pngs(items, workers=4)

    assert 2 <= maximum <= 4
    assert active == 0
    assert not any(thread.name.startswith("phase6-source-png") for thread in threading.enumerate())


def test_parallel_source_png_failure_propagates_after_shutdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entered = threading.Event()

    def failing_save(path: Path, rgb: np.ndarray) -> None:
        entered.set()
        if path.name == "0.png":
            raise OSError("injected source PNG failure")
        time.sleep(0.01)

    monkeypatch.setattr(frame_pipeline, "_save_source_png", failing_save)
    items = [(tmp_path / f"{index}.png", np.zeros((1, 1, 3), dtype=np.uint8)) for index in range(11)]

    with pytest.raises(OSError, match="injected source PNG failure"):
        _write_source_pngs(items, workers=4)

    assert entered.is_set()
    assert not any(thread.name.startswith("phase6-source-png") for thread in threading.enumerate())


def test_prepare_scan_propagates_png_failure_before_yolo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Frame:
        frame = {"frame_id": "frame_000000", "sequence": 0}
        image_path = tmp_path / "source.rgb565"

    pipeline = object.__new__(FramePipeline)
    pipeline.device = "cuda:0"
    pipeline.output_root = tmp_path / "output"
    pipeline.source_png_workers = 4
    pipeline._pipeline_event = lambda *args, **kwargs: None
    yolo_called = False

    monkeypatch.setattr(
        frame_pipeline,
        "read_rgb565_le",
        lambda path: np.zeros((360, 640, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        frame_pipeline,
        "_write_source_pngs",
        lambda items, workers: (_ for _ in ()).throw(OSError("injected write")),
    )

    def unexpected_yolo(*args, **kwargs):
        nonlocal yolo_called
        yolo_called = True
        raise AssertionError("YOLO must not run")

    monkeypatch.setattr(frame_pipeline, "run_yolo_pair", unexpected_yolo)

    with pytest.raises(FramePipelineError, match="source RGB preparation failed"):
        pipeline.prepare_scan_yolo("scan_test", [Frame()])

    assert not yolo_called


@pytest.mark.parametrize("workers", [0, 5])
def test_source_png_worker_bound_is_rejected(workers: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 4"):
        _write_source_pngs([], workers=workers)
