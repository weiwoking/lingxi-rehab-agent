from __future__ import annotations

import json
import os
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "visionpc.pipeline.progress.v1"
MAX_EVENT_BYTES = 4096
MAX_EVENTS = 256
VALID_STATUSES = {"started", "progress", "completed", "failed"}
VALID_PHASES = {
    "pipeline_setup", "yolo", "frame_processing", "all_object_geometry",
    "selection", "package", "visualization", "pipeline",
}
VALID_SUBPHASES = {
    "asset_validation", "source_decode", "detect", "segment", "frame",
    "input_prepare", "deltar_direct", "deltar_letterbox", "support_plane",
    "frame_object_geometry", "all_object_accumulate",
    "frame_result_write", "geometry_document_write", "gate1", "gate2",
    "selected_write", "package_write", "render", "terminal",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _small(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if depth < 1 and isinstance(value, dict):
        return {str(k)[:64]: _small(v, depth=depth + 1) for k, v in list(value.items())[:24]}
    return str(value)[:240]


def validate_progress_event(event: dict[str, Any]) -> None:
    if event.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported progress schema")
    if type(event.get("event_id")) is not int or event["event_id"] < 1:
        raise ValueError("event_id must be a positive integer")
    if event.get("status") not in VALID_STATUSES:
        raise ValueError("invalid status")
    if event.get("phase") not in VALID_PHASES:
        raise ValueError("invalid phase")
    if event.get("subphase") not in VALID_SUBPHASES:
        raise ValueError("invalid subphase")
    frame_index = event.get("frame_index")
    if frame_index is not None and (type(frame_index) is not int or frame_index < 1):
        raise ValueError("frame_index must be 1-based")
    raw = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) + 1 > MAX_EVENT_BYTES:
        raise ValueError("progress event exceeds 4096 bytes")


def replay_progress(path: Path, *, strict: bool = False) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    last_id = 0
    try:
        lines: Iterable[str] = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        if strict:
            raise
        return events
    for line in lines:
        try:
            if len(line.encode("utf-8")) + 1 > MAX_EVENT_BYTES:
                raise ValueError("oversize progress line")
            event = json.loads(line)
            if type(event) is not dict:
                raise ValueError("event root")
            validate_progress_event(event)
            if int(event["event_id"]) <= last_id:
                raise ValueError("non-monotonic event_id")
            last_id = int(event["event_id"])
            events.append(event)
        except (ValueError, TypeError, json.JSONDecodeError):
            if strict:
                raise
    return events


class PipelineProgressReporter:
    """Best-effort child progress writer. A writer failure permanently disables it."""

    def __init__(self, path: Path, scan_id: str, *, enabled: bool = True) -> None:
        self.path = Path(path)
        self.scan_id = str(scan_id)
        self.enabled = bool(enabled)
        self.failed = False
        self.event_id = 0
        self._handle: Any = None
        self._last_fsync = 0.0
        self.serialize_ms = 0.0
        self.flush_ms = 0.0
        self.fsync_ms = 0.0
        self._warned = False
        if self.enabled:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._handle = self.path.open("a", encoding="utf-8", newline="\n")
            except Exception as exc:
                self._disable(exc)

    @classmethod
    def for_output(cls, output_dir: Path, scan_id: str) -> "PipelineProgressReporter":
        disabled = os.environ.get("VISIONPC_PROGRESS_DISABLED", "").strip() == "1"
        return cls(Path(output_dir) / "logs" / "pipeline_progress.jsonl", scan_id, enabled=not disabled)

    def _disable(self, exc: BaseException) -> None:
        self.failed = True
        self.enabled = False
        if not self._warned:
            warnings.warn(f"pipeline progress disabled: {type(exc).__name__}: {exc}", RuntimeWarning, stacklevel=2)
            self._warned = True
        try:
            if self._handle is not None:
                self._handle.close()
        except Exception:
            pass
        self._handle = None
        try:
            marker = self.path.with_suffix(self.path.suffix + ".disabled")
            marker.write_text(
                json.dumps({"schema_version": SCHEMA_VERSION, "failed": True,
                            "error_type": type(exc).__name__}, separators=(",", ":")),
                encoding="utf-8",
            )
        except Exception:
            pass

    def emit(
        self, phase: str, subphase: str, status: str, *, frame_id: str | None = None,
        frame_index: int | None = None, frame_total: int | None = None,
        current: int | float | None = None, total: int | float | None = None,
        elapsed_ms: int | float = 0, message: str = "", metrics: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None, checkpoint: bool = False,
    ) -> dict[str, Any] | None:
        if not self.enabled or self.failed or self.event_id >= MAX_EVENTS:
            return None
        try:
            next_id = self.event_id + 1
            event = {
                "schema_version": SCHEMA_VERSION, "event_id": next_id, "timestamp": utc_now(),
                "scan_id": self.scan_id, "phase": phase, "subphase": subphase, "status": status,
                "frame_id": frame_id, "frame_index": frame_index, "frame_total": frame_total,
                "current": current, "total": total, "elapsed_ms": round(float(elapsed_ms), 3),
                "message": " ".join(str(message or subphase).split())[:240],
                "metrics": _small(dict(metrics or {})),
                "error": None if error is None else {
                    "code": str(error.get("code") or "E_PIPELINE_FAILED")[:64],
                    "type": str(error.get("type") or "unknown")[:120],
                    "message": " ".join(str(error.get("message") or "pipeline failed").split())[:240],
                },
            }
            t0 = time.perf_counter()
            validate_progress_event(event)
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            self.serialize_ms += (time.perf_counter() - t0) * 1000.0
            t0 = time.perf_counter()
            self._handle.write(line)
            self._handle.flush()
            self.flush_ms += (time.perf_counter() - t0) * 1000.0
            now = time.monotonic()
            durable = checkpoint or status == "failed" or phase == "pipeline" or (
                status == "completed" and subphase in {"frame", "terminal"}
            )
            if durable or now - self._last_fsync >= 1.0:
                t0 = time.perf_counter()
                os.fsync(self._handle.fileno())
                self.fsync_ms += (time.perf_counter() - t0) * 1000.0
                self._last_fsync = now
            self.event_id = next_id
            return event
        except Exception as exc:
            self._disable(exc)
            return None

    def close(self) -> None:
        try:
            if self._handle is not None:
                self._handle.flush()
                self._handle.close()
        except Exception as exc:
            self._disable(exc)
        finally:
            self._handle = None

    def __enter__(self) -> "PipelineProgressReporter":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()
