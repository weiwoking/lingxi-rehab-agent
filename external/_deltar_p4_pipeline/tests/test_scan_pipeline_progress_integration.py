from __future__ import annotations

from pathlib import Path

from deltar_p4_pipeline.scan_progress import ProgressStore


def child_event(event_id: int, status: str, *, frame_index: int, elapsed_ms: float) -> dict:
    completed = frame_index if status in {"completed", "failed"} else frame_index - 1
    return {
        "pipeline_event_id": event_id,
        "pipeline_schema_version": "visionpc.pipeline.progress.v1",
        "pipeline_timestamp": f"2026-08-03T10:20:{event_id:02d}.000000Z",
        "pipeline_status": status,
        "phase": "frame_processing",
        "subphase": "frame",
        "frame_id": f"frame_{frame_index - 1:06d}",
        "frame_index": frame_index,
        "frame_total": 11,
        "elapsed_ms": elapsed_ms,
        "observer_mode": "child_stream",
        "metrics": {
            "completed_frames": completed,
            "successful_frames": completed,
            "rejected_frames": 0,
            "failed_frames": 0,
        },
        "completed_frames": completed,
        "successful_frames": completed,
        "rejected_frames": 0,
        "failed_frames": 0,
    }


def test_reducer_exposes_frame_subphase_eta_and_child_id_domain(tmp_path: Path) -> None:
    store = ProgressStore(tmp_path / "progress")
    store.emit("upload_started", scan_id="scan_a")
    for child_id, frame, duration in ((1, 1, 1000), (2, 2, 1200)):
        store.emit(
            "pipeline_progress", scan_id="scan_a", status="success",
            elapsed_ms=duration, message=f"第{frame}帧完成",
            details=child_event(child_id, "completed", frame_index=frame, elapsed_ms=duration),
        )
    duplicate = store.emit(
        "pipeline_progress", scan_id="scan_a", status="success", message="duplicate",
        details=child_event(2, "completed", frame_index=2, elapsed_ms=1200),
    )
    summary = store.snapshot(active_user_scan_id="scan_a")["current_scan"]
    assert duplicate == {}
    assert summary["current_frame_index"] == 2
    assert summary["frame_total"] == 11
    assert summary["completed_frames"] == 2
    assert summary["rolling_frame_median_ms"] == 1100
    assert summary["eta_seconds"] == 9.9
    assert summary["fine_progress_available"] is True


def test_legacy_history_does_not_invent_frame_total(tmp_path: Path) -> None:
    store = ProgressStore(tmp_path / "progress")
    store.emit("upload_started", scan_id="old_scan")
    old = store.snapshot(active_user_scan_id="old_scan")["current_scan"]
    assert old["fine_progress_available"] is False
    assert old["frame_total"] is None
    assert old["current_frame_index"] is None
