from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import deltar_p4_pipeline.frame_pipeline as frame_pipeline_module
from deltar_p4_pipeline.frame_pipeline import (
    CachedYolo,
    FramePipeline,
    FramePipelineError,
    PreparedSource,
)
from deltar_p4_pipeline.scan_aggregation import determine_scan_status
from deltar_p4_pipeline.scan_contracts import ScanContractError, atomic_write_json
from deltar_p4_pipeline.stage6_schema import validate_frame_result_v1_1


SHA256 = "0" * 64


class _Bundle:
    calibration = {"calibration_status": "measured"}
    algorithm = {
        "detect_segment_iou_threshold": 0.5,
        "border_tolerance_px": 1.0,
    }

    @staticmethod
    def public_provenance() -> dict:
        return {}


def _pipeline(tmp_path: Path, *, detections: list[dict], segments: list[dict]) -> tuple[FramePipeline, SimpleNamespace]:
    image_path = tmp_path / "image.rgb565"
    image_path.write_bytes(b"\0" * 32)
    frame = SimpleNamespace(
        frame={
            "frame_id": "frame_000000",
            "sequence": 0,
            "camera_timestamp_us": 100,
            "tof_timestamp_us": 100,
            "pair_delta_us": 0,
        },
        tof={"distance_mm": [], "target_status": [], "nb_target_detected": []},
        image_path=image_path,
    )
    pipeline = object.__new__(FramePipeline)
    pipeline.bundle = _Bundle()
    pipeline.output_root = tmp_path
    pipeline.stage7d_processor = None
    pipeline.all_object_geometry_processor = None
    pipeline.progress_reporter = None
    pipeline.stage6_required_region_only = False
    pipeline._thread_state = SimpleNamespace(suppress_progress=True)
    pipeline._models = {
        task: {"sha256": SHA256} for task in ("depth", "detect", "segment")
    }
    pipeline._geometry_profile = {}
    pipeline._prepared_sources = {
        "frame_000000": PreparedSource(
            rgb=np.zeros((1, 1, 3), dtype=np.uint8),
            path=image_path,
            decode_rgb565_ms=0.0,
        )
    }
    pipeline._cached_yolo = {
        "frame_000000": CachedYolo(
            detect_document={"instances": detections},
            segment_document={"instances": segments},
            masks={},
        )
    }
    pipeline._run_depth = lambda _rgb, _tof: ({}, {}, {})
    return pipeline, frame


@pytest.fixture(autouse=True)
def _isolate_formal_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(frame_pipeline_module, "normalize_frame_diagnostics", lambda _result: [])
    monkeypatch.setattr(frame_pipeline_module, "validate_frame_result_v1_1", lambda _result: None)


def test_detection_zero_is_an_ordinary_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pipeline, frame = _pipeline(tmp_path, detections=[], segments=[])
    monkeypatch.setattr(
        frame_pipeline_module,
        "build_logical_groups",
        lambda *_args, **_kwargs: ([], [], []),
    )
    result = pipeline.process(frame)

    assert result["status"] == "success"
    assert result["detections"]["detect_count"] == 0
    assert result["objects"] == []
    assert result["rejections"] == []


def test_zero_successful_geometry_is_an_ordinary_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    detection = {"object_id": "detect_0"}
    pipeline, frame = _pipeline(tmp_path, detections=[detection], segments=[])
    group = {
        "logical_object_id": "object_0",
        "segmentation_primary": {"available": False},
        "match": {"status": "unmatched"},
    }
    monkeypatch.setattr(
        frame_pipeline_module,
        "build_logical_groups",
        lambda *_args, **_kwargs: ([group], [group], []),
    )
    rejected_object = {
        "frame_object_id": "frame_000000:object_0",
        "rejection_reason": "insufficient_depth",
        "warnings": [],
    }
    monkeypatch.setattr(
        frame_pipeline_module,
        "formal_result_to_observation",
        lambda *_args, **_kwargs: rejected_object,
    )

    result = pipeline.process(frame)

    assert result["status"] == "success"
    assert result["detections"]["detect_count"] == 1
    assert result["objects"] == [rejected_object]
    assert result["rejections"] == ["frame_000000:object_0"]


def test_zero_selected_and_all_empty_scan_remain_success() -> None:
    frames = [{"status": "success"} for _ in range(11)]

    assert determine_scan_status(frames, [], "unverified") == "success"


def test_mixed_normal_and_empty_frames_are_all_success() -> None:
    frames = [{"status": "success", "objects": [{"id": index}]} for index in range(10)]
    frames.append({"status": "success", "objects": []})

    assert determine_scan_status(frames, [{"selected": False}], "measured") == "success"


def test_true_rejected_frame_still_makes_scan_partial() -> None:
    frames = [{"status": "success"} for _ in range(10)] + [{"status": "rejected"}]

    assert determine_scan_status(frames, [], "measured") == "partial_success"


def test_model_exception_remains_a_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pipeline, frame = _pipeline(tmp_path, detections=[], segments=[])
    frame.tof = {"distance_mm": [1000.0], "target_status": [5], "nb_target_detected": [1]}

    def fail_model(_rgb: np.ndarray, _tof: dict) -> tuple:
        raise RuntimeError("model exploded")

    pipeline._run_depth = fail_model

    with pytest.raises(FramePipelineError, match="DELTAR failed") as caught:
        pipeline.process(frame)

    assert caught.value.phase == "model_inference"
    assert caught.value.code == "E_FRAME_PROCESSING"


def test_geometry_exception_remains_a_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pipeline, frame = _pipeline(tmp_path, detections=[], segments=[])
    frame.tof = {"distance_mm": [1000.0], "target_status": [5], "nb_target_detected": [1]}
    pipeline._run_depth = lambda *_args: (
        {
            "direct_640x360": np.ones((360, 640)),
            "letterbox_640x480": np.ones((360, 640)),
        },
        {},
        {},
    )
    monkeypatch.setattr(
        frame_pipeline_module,
        "build_logical_groups",
        lambda *_args, **_kwargs: ([], [], []),
    )

    with pytest.raises(FramePipelineError, match="formal all-object Geometry v2 processor is required") as caught:
        pipeline.process(frame)

    assert caught.value.phase == "frame_processing"
    assert caught.value.code == "E_FRAME_PROCESSING"


def test_formal_result_schema_failure_remains_a_failure() -> None:
    with pytest.raises(ScanContractError) as caught:
        validate_frame_result_v1_1({"status": "success"})

    assert caught.value.code == "E_FRAME_RESULT_SCHEMA"


def test_formal_artifact_write_failure_is_not_swallowed(tmp_path: Path) -> None:
    blocked_parent = tmp_path / "not_a_directory"
    blocked_parent.write_text("block", encoding="utf-8")

    with pytest.raises(OSError):
        atomic_write_json(blocked_parent / "frame.json", {"status": "success"})
