from __future__ import annotations

import json
from pathlib import Path

import pytest

from deltar_p4_pipeline.pipeline_progress import (
    MAX_EVENTS,
    PipelineProgressReporter,
    SCHEMA_VERSION,
    replay_progress,
    validate_progress_event,
)


def test_reporter_schema_monotonic_1_based_flush_and_replay(tmp_path: Path) -> None:
    path = tmp_path / "pipeline_progress.jsonl"
    reporter = PipelineProgressReporter(path, "scan_a")
    first = reporter.emit("pipeline", "terminal", "started", message="开始")
    second = reporter.emit(
        "frame_processing", "frame", "started", frame_id="frame_000003",
        frame_index=4, frame_total=11, current=4, total=11, message="第4帧",
    )
    reporter.close()
    assert first and second
    assert [first["event_id"], second["event_id"]] == [1, 2]
    assert second["schema_version"] == SCHEMA_VERSION
    assert second["frame_index"] == 4
    assert path.read_bytes().endswith(b"\n")
    assert [event["event_id"] for event in replay_progress(path, strict=True)] == [1, 2]


def test_reporter_terminal_fsync_event_cap_and_size_cap(monkeypatch, tmp_path: Path) -> None:
    fsyncs: list[int] = []
    monkeypatch.setattr("deltar_p4_pipeline.pipeline_progress.os.fsync", fsyncs.append)
    reporter = PipelineProgressReporter(tmp_path / "events.jsonl", "scan_a")
    for _ in range(MAX_EVENTS + 5):
        reporter.emit("frame_processing", "frame", "completed", checkpoint=True)
    reporter.close()
    assert reporter.event_id == MAX_EVENTS
    assert fsyncs
    event = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    event["message"] = "x" * 5000
    with pytest.raises(ValueError, match="4096"):
        validate_progress_event(event)


def test_reporter_writer_failure_becomes_permanent_noop(monkeypatch, tmp_path: Path) -> None:
    def broken_open(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", broken_open)
    with pytest.warns(RuntimeWarning, match="progress disabled"):
        reporter = PipelineProgressReporter(tmp_path / "events.jsonl", "scan_a")
    assert reporter.failed is True
    assert reporter.emit("pipeline", "terminal", "failed") is None


def test_replay_skips_malformed_and_rejects_non_monotonic_in_strict_mode(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    reporter = PipelineProgressReporter(path, "scan_a")
    reporter.emit("pipeline", "terminal", "started")
    reporter.close()
    path.write_text(path.read_text(encoding="utf-8") + "not-json\n", encoding="utf-8")
    assert len(replay_progress(path)) == 1
    with pytest.raises((ValueError, json.JSONDecodeError)):
        replay_progress(path, strict=True)
