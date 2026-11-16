from __future__ import annotations

import json
import queue
import re
import threading
import time
from statistics import median
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SENSITIVE_RE = re.compile(
    r"(?i)(authorization|bearer|token|password|secret|cookie)\s*[:=]\s*[^\s,;]+"
)
FINAL_EVENTS = {"failed", "package_ready", "package_send_completed"}
JOB_ROLES = {"user", "canary", "replay", "diagnostic", "test"}
VISIBILITIES = {"user", "diagnostics"}
COMPLETED_FOCUS_SECONDS = 300.0
TELEMETRY_EVENTS = {
    "upload_progress",
    "geometry_progress",
    "p4_status_polled",
    "package_send_progress",
}
BUSINESS_EVENT_STAGES = {
    "upload_started": ("upload", "running"),
    "upload_completed": ("upload", "success"),
    "validation_started": ("validation", "running"),
    "validation_completed": ("validation", "success"),
    "job_queued": ("processing", "running"),
    "processing_started": ("processing", "running"),
    "detection_started": ("detection", "running"),
    "detection_completed": ("detection", "success"),
    "geometry_started": ("geometry", "running"),
    "geometry_completed": ("geometry", "success"),
    "selection_completed": ("selection", "success"),
    "package_build_started": ("package", "running"),
    "package_ready": ("package", "success"),
    "p4_package_requested": ("p4_delivery", "running"),
    "package_send_started": ("p4_delivery", "running"),
    "package_send_completed": ("p4_delivery", "success"),
}
STEP_ORDER = (
    "upload", "validation", "detection", "geometry", "selection", "package",
    "p4_delivery",
)
STEP_COMPLETIONS = {
    "upload_completed": "upload",
    "validation_completed": "validation",
    "detection_completed": "detection",
    "geometry_completed": "geometry",
    "selection_completed": "selection",
    "package_ready": "package",
    "package_send_completed": "p4_delivery",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_detail(value: Any, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return SENSITIVE_RE.sub(r"\1=[redacted]", text)[:limit]


class ProgressStore:
    """Best-effort observation store; failures never change scan outcomes."""

    def __init__(
        self,
        root: Path,
        *,
        max_events: int = 1000,
        max_scans: int = 20,
        client_queue_size: int = 128,
        max_file_bytes: int = 1024 * 1024,
    ) -> None:
        self.root = root.resolve()
        self.max_scans = max_scans
        self.client_queue_size = client_queue_size
        self.max_file_bytes = max_file_bytes
        self._lock = threading.RLock()
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._summaries: dict[str, dict[str, Any]] = {}
        self._request_scan: dict[str, str] = {}
        self._request_identity: dict[str, tuple[str, str]] = {}
        self._active_user_requests: set[str] = set()
        self._child_event_keys: set[tuple[str, str, int]] = set()
        self._subscribers: set[queue.Queue[dict[str, Any]]] = set()
        self._next_id = 1
        self._write_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=512)
        self.persistence_error: str | None = None
        self.root.mkdir(parents=True, exist_ok=True)
        self._recover()
        # Startup and isolated performance canaries must not mutate user history.
        # Retention is enforced only after a real scan event is persisted.
        threading.Thread(
            target=self._writer_loop, name="scan-progress-writer", daemon=True
        ).start()

    def _event_path(self, scan_id: str) -> Path:
        return self.root / f"{scan_id}.progress_events.jsonl"

    def _recover(self) -> None:
        paths = sorted(
            self.root.glob("*.progress_events.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[: self.max_scans]
        recovered: list[dict[str, Any]] = []
        for path in reversed(paths):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()[-200:]
            except (OSError, UnicodeError):
                continue
            for line in lines:
                try:
                    event = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                if type(event) is dict and type(event.get("event_id")) is int:
                    recovered.append(event)
        recovered.sort(key=lambda event: int(event["event_id"]))
        unique: dict[int, dict[str, Any]] = {}
        for event in recovered:
            unique[int(event["event_id"])] = event
        for event in list(unique.values())[-self._events.maxlen :]:
            self._events.append(event)
            self._next_id = max(self._next_id, int(event["event_id"]) + 1)
            self._update_summary(event, recovered=True)

    def _writer_loop(self) -> None:
        while True:
            event = self._write_queue.get()
            try:
                scan_id = event.get("scan_id")
                if not scan_id:
                    continue
                path = self._event_path(str(scan_id))
                if path.exists() and path.stat().st_size >= self.max_file_bytes:
                    rotated = path.with_suffix(path.suffix + ".1")
                    if rotated.exists():
                        rotated.unlink()
                    path.replace(rotated)
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(
                        json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
                    handle.flush()
                self._prune_files()
                self.persistence_error = None
            except OSError as exc:
                self.persistence_error = _safe_detail(exc)
            finally:
                self._write_queue.task_done()

    def _prune_files(self) -> None:
        try:
            paths = sorted(
                self.root.glob("*.progress_events.jsonl"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for stale in paths[self.max_scans :]:
                stale.unlink()
                rotated = stale.with_suffix(stale.suffix + ".1")
                if rotated.exists():
                    rotated.unlink()
        except OSError as exc:
            self.persistence_error = _safe_detail(exc)

    def bind_request(self, request_id: str, scan_id: str) -> None:
        with self._lock:
            self._request_scan[request_id] = scan_id
            pending_key = f"request:{request_id}"
            pending = self._summaries.pop(pending_key, None)
            if pending is not None:
                pending["scan_id"] = scan_id
                self._summaries[scan_id] = pending
            prior = [
                dict(event, scan_id=scan_id)
                for event in self._events
                if event.get("request_id") == request_id and not event.get("scan_id")
            ]
        for event in prior:
            try:
                self._write_queue.put_nowait(event)
            except queue.Full:
                break

    def set_request_identity(
        self,
        request_id: str,
        job_role: str,
        visibility: str,
        *,
        active: bool,
    ) -> None:
        role = job_role if job_role in JOB_ROLES else "user"
        visible = visibility if visibility in VISIBILITIES else (
            "user" if role == "user" else "diagnostics"
        )
        with self._lock:
            self._request_identity[request_id] = (role, visible)
            if active and visible == "user":
                self._active_user_requests.add(request_id)
            else:
                self._active_user_requests.discard(request_id)

    def emit(
        self,
        stage: str,
        *,
        request_id: str | None = None,
        scan_id: str | None = None,
        status: str = "info",
        current: int | float | None = None,
        total: int | float | None = None,
        bytes_current: int | None = None,
        bytes_total: int | None = None,
        elapsed_ms: int | float | None = None,
        message: str = "",
        error_code: str | None = None,
        error_detail: Any = None,
        details: dict[str, Any] | None = None,
        job_role: str | None = None,
        visibility: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        try:
            with self._lock:
                if scan_id is None and request_id:
                    scan_id = self._request_scan.get(request_id)
                request_identity = self._request_identity.get(str(request_id or ""))
                if request_identity is not None:
                    job_role = job_role or request_identity[0]
                    visibility = visibility or request_identity[1]
                existing = self._summaries.get(
                    str(scan_id) if scan_id else f"request:{request_id}"
                )
                if existing is not None:
                    job_role = job_role or existing.get("job_role")
                    visibility = visibility or existing.get("visibility")
                role = job_role if job_role in JOB_ROLES else "user"
                visible = visibility if visibility in VISIBILITIES else (
                    "user" if role == "user" else "diagnostics"
                )
                detail_values = dict(details or {})
                if stage == "pipeline_progress":
                    child_id = detail_values.get("pipeline_event_id")
                    schema = detail_values.get("pipeline_schema_version")
                    if scan_id and type(child_id) is int and isinstance(schema, str):
                        child_key = (str(scan_id), schema, child_id)
                        if child_key in self._child_event_keys:
                            return {}
                        self._child_event_keys.add(child_key)
                event = {
                    "event_id": self._next_id,
                    "request_id": request_id,
                    "scan_id": scan_id,
                    "timestamp": _utc_now(),
                    "stage": stage,
                    "status": status,
                    "current": current,
                    "total": total,
                    "bytes_current": bytes_current,
                    "bytes_total": bytes_total,
                    "elapsed_ms": elapsed_ms,
                    "message": _safe_detail(message, 240) or stage,
                    "error_code": _safe_detail(error_code, 64),
                    "error_detail": _safe_detail(error_detail),
                    "details": detail_values,
                    "job_role": role,
                    "visibility": visible,
                }
                self._next_id += 1
                self._events.append(event)
                self._update_summary(event)
                subscribers = tuple(self._subscribers)
            for subscriber in subscribers:
                try:
                    subscriber.put_nowait(event)
                except queue.Full:
                    try:
                        subscriber.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        subscriber.put_nowait(event)
                    except queue.Full:
                        pass
            if persist and scan_id:
                try:
                    self._write_queue.put_nowait(event)
                except queue.Full:
                    self.persistence_error = "progress persistence queue is full"
            return event
        except Exception:
            return {}

    def _update_summary(self, event: dict[str, Any], *, recovered: bool = False) -> None:
        scan_id = event.get("scan_id")
        request_id = event.get("request_id")
        if not scan_id and not request_id:
            return
        summary_key = str(scan_id) if scan_id else f"request:{request_id}"
        stage = str(event.get("stage") or "")
        if stage == "pipeline_progress":
            recovered_details = event.get("details") or {}
            recovered_child_id = recovered_details.get("pipeline_event_id")
            recovered_schema = recovered_details.get("pipeline_schema_version")
            if scan_id and type(recovered_child_id) is int and isinstance(recovered_schema, str):
                self._child_event_keys.add((str(scan_id), recovered_schema, recovered_child_id))
        if stage in TELEMETRY_EVENTS and summary_key not in self._summaries:
            return
        summary = self._summaries.setdefault(
            summary_key,
            {
                "scan_id": str(scan_id) if scan_id else None,
                "request_id": request_id,
                "started_at": event["timestamp"],
                "scan_started_at": event["timestamp"],
                "finished_at": None,
                "upload_started_at": None,
                "upload_completed_at": None,
                "processing_started_at": None,
                "processing_completed_at": None,
                "package_ready_at": None,
                "p4_request_at": None,
                "p4_send_started_at": None,
                "p4_send_completed_at": None,
                "user_scan_finished_at": None,
                "final_status": "active",
                "delivery_state": "waiting",
                "job_role": (
                    event.get("job_role")
                    if event.get("job_role") in JOB_ROLES
                    else "diagnostic" if recovered else "user"
                ),
                "visibility": (
                    event.get("visibility")
                    if event.get("visibility") in VISIBILITIES
                    else "diagnostics" if recovered else "user"
                ),
                "business_stage": "upload",
                "business_status": "running",
                "stage": stage,
                "status": event["status"],
                "message": event["message"],
                "last_business_event_at": event["timestamp"],
                "last_business_event_id": event["event_id"],
                "last_event_stage": stage,
                "last_event_status": event["status"],
                "last_event_message": event["message"],
                "last_event_at": event["timestamp"],
                "active_stage": "upload",
                "step_statuses": {name: "pending" for name in STEP_ORDER},
                "metrics": {},
                "error": None,
                "restart_incomplete": False,
                "pipeline_phase": None,
                "pipeline_subphase": None,
                "current_frame_id": None,
                "current_frame_index": None,
                "frame_total": None,
                "completed_frames": 0,
                "successful_frames": 0,
                "rejected_frames": 0,
                "failed_frames": 0,
                "current_frame_started_at": None,
                "last_pipeline_progress_at": None,
                "rolling_frame_median_ms": None,
                "eta_seconds": None,
                "observer_mode": "legacy",
                "fine_progress_available": False,
                "pipeline_step_statuses": {
                    name: "pending" for name in (
                        "upload", "validation", "yolo", "frame_processing",
                        "all_object_geometry", "selection", "package", "p4_delivery",
                    )
                },
            },
        )
        summary.update(
            {
                "request_id": summary.get("request_id") or event.get("request_id"),
                "last_event_stage": stage,
                "last_event_status": event["status"],
                "last_event_message": event["message"],
                "last_event_at": event["timestamp"],
            }
        )
        if event.get("job_role") in JOB_ROLES:
            summary["job_role"] = event["job_role"]
        if event.get("visibility") in VISIBILITIES:
            summary["visibility"] = event["visibility"]
        metrics = summary["metrics"]
        for key in ("current", "total", "bytes_current", "bytes_total", "elapsed_ms"):
            if event.get(key) is not None:
                metrics[key] = event[key]
        event_details = event.get("details") or {}
        metrics.update(event_details)
        if event_details.get("object_count") is not None:
            metrics["geometry_object_count"] = event_details["object_count"]
        if event_details.get("geometry_success_count") is not None:
            metrics["geometry_success_count"] = event_details["geometry_success_count"]
        if event_details.get("final_selected_count") is not None:
            metrics["final_selected_count"] = event_details["final_selected_count"]
        if stage == "pipeline_progress":
            details = event.get("details") or {}
            child_metrics = (
                details.get("metrics")
                if isinstance(details.get("metrics"), dict)
                else {}
            )
            for key, value in child_metrics.items():
                if value is not None:
                    metrics[key] = value
            if child_metrics.get("object_count") is not None:
                metrics["geometry_object_count"] = child_metrics["object_count"]
            if child_metrics.get("geometry_success_count") is not None:
                metrics["geometry_success_count"] = child_metrics["geometry_success_count"]
            elif child_metrics.get("object_count") is not None:
                metrics["geometry_success_count"] = child_metrics["object_count"]
            if child_metrics.get("selected_count") is not None:
                metrics["final_selected_count"] = child_metrics["selected_count"]
            if child_metrics.get("package_bytes") is not None:
                metrics["package_size_bytes"] = child_metrics["package_bytes"]
            phase = details.get("phase")
            subphase = details.get("subphase")
            pipeline_status = details.get("pipeline_status")
            observer_mode = str(details.get("observer_mode") or "child_stream")
            summary["observer_mode"] = observer_mode
            summary["fine_progress_available"] = observer_mode == "child_stream"
            summary["last_pipeline_progress_at"] = details.get("pipeline_timestamp") or event["timestamp"]
            if phase:
                summary["pipeline_phase"] = phase
            if subphase:
                summary["pipeline_subphase"] = subphase
            for source, target in (
                ("frame_id", "current_frame_id"), ("frame_index", "current_frame_index"),
                ("frame_total", "frame_total"), ("completed_frames", "completed_frames"),
                ("successful_frames", "successful_frames"), ("rejected_frames", "rejected_frames"),
                ("failed_frames", "failed_frames"),
            ):
                if details.get(source) is not None:
                    summary[target] = details[source]
            step_for_phase = {
                "pipeline_setup": "validation", "yolo": "yolo",
                "frame_processing": "frame_processing",
                "all_object_geometry": "all_object_geometry", "selection": "selection",
                "package": "package", "visualization": "package",
            }.get(str(phase))
            if step_for_phase:
                statuses = summary["pipeline_step_statuses"]
                order = list(statuses)
                position = order.index(step_for_phase)
                for prior in order[:position]:
                    if statuses[prior] == "pending":
                        statuses[prior] = "success"
                completed_subphase = (
                    pipeline_status == "completed"
                    and subphase in {
                        "asset_validation", "segment", "frame", "geometry_document_write",
                        "selected_write", "package_write", "render",
                    }
                )
                if step_for_phase == "frame_processing" and subphase == "frame":
                    completed_subphase = bool(
                        completed_subphase
                        and summary.get("frame_total") is not None
                        and int(summary.get("completed_frames") or 0)
                        >= int(summary["frame_total"])
                    )
                next_status = (
                    "failed" if pipeline_status == "failed" else
                    "success" if completed_subphase else "running"
                )
                self._advance_stage(statuses, step_for_phase, next_status)
            if phase == "frame_processing" and subphase == "frame" and pipeline_status == "started":
                summary["current_frame_started_at"] = details.get("pipeline_timestamp") or event["timestamp"]
            if phase == "frame_processing" and subphase == "frame" and pipeline_status in {"completed", "failed"}:
                durations = list(metrics.get("_frame_durations_ms") or [])
                duration = details.get("elapsed_ms")
                if isinstance(duration, (int, float)) and duration >= 0:
                    durations.append(float(duration))
                    durations = durations[-5:]
                    metrics["_frame_durations_ms"] = durations
                summary["current_frame_started_at"] = None
                if len(durations) >= 2:
                    rolling = float(median(durations))
                    summary["rolling_frame_median_ms"] = rolling
                    remaining = max(0, int(summary.get("frame_total") or 0) - int(summary.get("completed_frames") or 0))
                    summary["eta_seconds"] = round((rolling * remaining + float(metrics.get("postprocess_median_ms") or 0)) / 1000.0, 1)
            summary["business_stage"] = str(phase or summary.get("business_stage"))
            summary["business_status"] = (
                "failed" if pipeline_status == "failed" else
                "success" if pipeline_status == "completed" and phase == "pipeline" else
                "running"
            )
            summary["active_stage"] = None if pipeline_status == "completed" and phase == "pipeline" else phase
            summary["stage"] = stage
            summary["status"] = event["status"]
            summary["message"] = event["message"]
            summary["last_business_event_at"] = event["timestamp"]
            summary["last_business_event_id"] = event["event_id"]
        if stage in {"upload_started", "upload_progress", "upload_completed"}:
            if stage == "upload_started" and summary.get("upload_started_at") is None:
                summary["upload_started_at"] = event["timestamp"]
            if stage == "upload_completed":
                summary["upload_completed_at"] = event["timestamp"]
                if event.get("elapsed_ms") is not None:
                    metrics["upload_elapsed_ms"] = event["elapsed_ms"]
            if event.get("bytes_current") is not None:
                metrics["upload_bytes_current"] = event["bytes_current"]
            if event.get("bytes_total") is not None:
                metrics["upload_bytes_total"] = event["bytes_total"]
        if stage in {"package_send_started", "package_send_progress", "package_send_completed"}:
            if event.get("bytes_current") is not None:
                metrics["package_response_bytes"] = event["bytes_current"]
        if stage == "p4_status_polled":
            previous = metrics.get("last_p4_poll_at")
            if previous:
                try:
                    before = datetime.fromisoformat(str(previous).replace("Z", "+00:00"))
                    current_at = datetime.fromisoformat(
                        str(event["timestamp"]).replace("Z", "+00:00")
                    )
                    metrics["seconds_since_previous_poll"] = max(
                        0.0, (current_at - before).total_seconds()
                    )
                except ValueError:
                    pass
            metrics["last_p4_poll_at"] = event["timestamp"]
            metrics["p4_poll_count"] = int(metrics.get("p4_poll_count", 0)) + 1
            if (
                (event.get("details") or {}).get("last_status_returned")
                == "decision_ready"
                and "first_ready_poll_at" not in metrics
            ):
                metrics["first_ready_poll_at"] = event["timestamp"]
        if stage == "p4_package_requested":
            metrics["package_requested_at"] = event["timestamp"]
            summary["p4_request_at"] = event["timestamp"]
            summary["delivery_state"] = "sending"
        if stage == "package_send_started":
            summary["p4_send_started_at"] = event["timestamp"]
            summary["delivery_state"] = "sending"
        if stage == "processing_started" and summary.get("processing_started_at") is None:
            summary["processing_started_at"] = event["timestamp"]
        if stage in BUSINESS_EVENT_STAGES:
            business_stage, business_status = BUSINESS_EVENT_STAGES[stage]
            summary["business_stage"] = business_stage
            summary["business_status"] = business_status
            summary["stage"] = stage
            summary["status"] = event["status"]
            summary["message"] = event["message"]
            summary["last_business_event_at"] = event["timestamp"]
            summary["last_business_event_id"] = event["event_id"]
            summary["active_stage"] = business_stage if business_status == "running" else None
            statuses = summary["step_statuses"]
            if business_stage in statuses and business_status == "running":
                self._advance_stage(statuses, business_stage, "running")
            completed_step = STEP_COMPLETIONS.get(stage)
            if completed_step:
                completed_index = STEP_ORDER.index(completed_step)
                for step in STEP_ORDER[: completed_index + 1]:
                    statuses[step] = "success"
                if stage == "package_ready":
                    statuses["p4_delivery"] = "pending"
                    summary["active_stage"] = None
            pipeline_alias = {
                "upload": "upload", "validation": "validation", "detection": "yolo",
                "geometry": "all_object_geometry", "selection": "selection",
                "package": "package", "p4_delivery": "p4_delivery",
            }.get(business_stage)
            if pipeline_alias:
                pipeline_statuses = summary["pipeline_step_statuses"]
                if business_status == "running":
                    self._advance_stage(pipeline_statuses, pipeline_alias, "running")
                else:
                    self._advance_stage(pipeline_statuses, pipeline_alias, "success")
        if stage == "package_ready":
            summary["processing_completed_at"] = event["timestamp"]
            summary["package_ready_at"] = event["timestamp"]
            summary["finished_at"] = event["timestamp"]
            summary["final_status"] = "pc_completed"
            summary["delivery_state"] = "waiting_p4"
            summary["business_stage"] = "p4_delivery"
            summary["business_status"] = "success"
            summary["active_stage"] = None
            summary["eta_seconds"] = None
            summary["current_frame_started_at"] = None
            details = event.get("details") or {}
            if details.get("selected_count") is not None:
                metrics["final_selected_count"] = details["selected_count"]
            pipeline_statuses = summary["pipeline_step_statuses"]
            for pipeline_stage in (
                "upload",
                "validation",
                "yolo",
                "frame_processing",
                "all_object_geometry",
                "selection",
                "package",
            ):
                pipeline_statuses[pipeline_stage] = "success"
            pipeline_statuses["p4_delivery"] = "pending"
        if stage == "package_send_completed":
            metrics["package_sent_at"] = event["timestamp"]
            summary["final_status"] = "sent"
            summary["business_status"] = "success"
            summary["business_stage"] = "p4_delivery"
            summary["active_stage"] = None
            summary["step_statuses"] = {name: "success" for name in STEP_ORDER}
            summary["pipeline_step_statuses"] = {
                name: "success" for name in summary["pipeline_step_statuses"]
            }
            summary["finished_at"] = event["timestamp"]
            summary["p4_send_completed_at"] = event["timestamp"]
            summary["user_scan_finished_at"] = event["timestamp"]
            summary["delivery_state"] = "done"
            if event.get("elapsed_ms") is not None:
                metrics["p4_send_elapsed_ms"] = event["elapsed_ms"]
            if recovered and event.get("job_role") not in JOB_ROLES:
                summary["job_role"] = "user"
                summary["visibility"] = "user"
        elif stage == "failed":
            failed_stage = str(
                summary.get("active_stage") or summary.get("business_stage") or "upload"
            )
            summary["final_status"] = "failed"
            summary["business_status"] = "failed"
            summary["stage"] = stage
            summary["status"] = event["status"]
            summary["message"] = event["message"]
            summary["last_business_event_at"] = event["timestamp"]
            summary["last_business_event_id"] = event["event_id"]
            summary["active_stage"] = None
            current_stage = str(summary.get("business_stage") or "upload")
            if current_stage in summary["step_statuses"]:
                summary["step_statuses"][current_stage] = "failed"
            failed_pipeline_stage = {
                "upload": "upload",
                "validation": "validation",
                "detection": "yolo",
                "yolo": "yolo",
                "processing": "frame_processing",
                "frame_processing": "frame_processing",
                "all_object_geometry": "all_object_geometry",
                "geometry": "all_object_geometry",
                "selection": "selection",
                "package": "package",
                "p4_delivery": "p4_delivery",
            }.get(failed_stage)
            if failed_pipeline_stage:
                self._advance_stage(
                    summary["pipeline_step_statuses"], failed_pipeline_stage, "failed"
                )
            summary["finished_at"] = event["timestamp"]
            summary["user_scan_finished_at"] = event["timestamp"]
            summary["delivery_state"] = (
                "failed" if summary.get("processing_completed_at") else "waiting"
            )
            summary["error"] = {
                "error_code": event.get("error_code"),
                "message": event.get("message"),
                "detail": event.get("error_detail"),
            }
        if len(self._summaries) > self.max_scans:
            oldest = min(
                self._summaries,
                key=lambda value: self._summaries[value].get("last_event_at", ""),
            )
            del self._summaries[oldest]
        if recovered and stage not in FINAL_EVENTS:
            summary["restart_incomplete"] = True

    @staticmethod
    def _advance_stage(
        statuses: dict[str, str], stage: str, next_status: str
    ) -> None:
        current = statuses.get(stage, "pending")
        if current in {"success", "failed", "skipped"}:
            return
        if next_status == "running" and current != "pending":
            return
        statuses[stage] = next_status

    @staticmethod
    def _summary_order(summary: dict[str, Any]) -> tuple[str, str, str, int]:
        return (
            str(summary.get("started_at") or ""),
            str(summary.get("last_business_event_at") or ""),
            str(summary.get("finished_at") or ""),
            int(summary.get("last_business_event_id") or 0),
        )

    @staticmethod
    def _copy_summary(summary: dict[str, Any]) -> dict[str, Any]:
        copied = dict(
            summary,
            metrics=dict(summary["metrics"]),
            step_statuses=dict(summary.get("step_statuses") or {}),
            pipeline_step_statuses=dict(summary.get("pipeline_step_statuses") or {}),
        )
        now = datetime.now(timezone.utc)
        def seconds_from(value: Any) -> float | None:
            if not isinstance(value, str):
                return None
            try:
                return max(0.0, (now - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds())
            except ValueError:
                return None
        copied["current_frame_elapsed_ms"] = (
            None if copied.get("final_status") != "active"
            else None if seconds_from(copied.get("current_frame_started_at")) is None
            else round(float(seconds_from(copied.get("current_frame_started_at"))) * 1000.0, 1)
        )
        if copied.get("final_status") == "active":
            copied["seconds_since_last_progress"] = seconds_from(
                copied.get("last_pipeline_progress_at")
            )
        else:
            copied["seconds_since_last_progress"] = (
                None
                if ProgressStore._duration_ms(
                    copied.get("last_pipeline_progress_at"),
                    copied.get("user_scan_finished_at")
                    or copied.get("processing_completed_at")
                    or copied.get("finished_at"),
                ) is None
                else round(
                    float(
                        ProgressStore._duration_ms(
                            copied.get("last_pipeline_progress_at"),
                            copied.get("user_scan_finished_at")
                            or copied.get("processing_completed_at")
                            or copied.get("finished_at"),
                        )
                    ) / 1000.0,
                    1,
                )
            )
        started_elapsed = seconds_from(copied.get("scan_started_at") or copied.get("started_at"))
        total_end = (
            copied.get("user_scan_finished_at")
            or copied.get("processing_completed_at")
            or copied.get("finished_at")
        )
        if total_end:
            try:
                started = datetime.fromisoformat(str(copied.get("scan_started_at") or copied["started_at"]).replace("Z", "+00:00"))
                finished = datetime.fromisoformat(str(total_end).replace("Z", "+00:00"))
                copied["total_elapsed_ms"] = round(max(0.0, (finished - started).total_seconds()) * 1000.0, 1)
            except (ValueError, TypeError):
                copied["total_elapsed_ms"] = None
        else:
            copied["total_elapsed_ms"] = None if started_elapsed is None else round(started_elapsed * 1000.0, 1)
        copied["upload_elapsed_ms"] = ProgressStore._duration_ms(
            copied.get("upload_started_at"), copied.get("upload_completed_at")
        ) or copied["metrics"].get("upload_elapsed_ms")
        copied["processing_elapsed_ms"] = ProgressStore._duration_ms(
            copied.get("processing_started_at"), copied.get("processing_completed_at")
        )
        copied["p4_delivery_elapsed_ms"] = ProgressStore._duration_ms(
            copied.get("p4_send_started_at"), copied.get("p4_send_completed_at")
        ) or copied["metrics"].get("p4_send_elapsed_ms")
        rolling = copied.get("rolling_frame_median_ms")
        if (
            copied.get("final_status") == "active"
            and isinstance(rolling, (int, float))
            and copied.get("frame_total") is not None
        ):
            remaining = max(0, int(copied["frame_total"]) - int(copied.get("completed_frames") or 0))
            current_elapsed = float(copied.get("current_frame_elapsed_ms") or 0.0)
            postprocess = float(copied["metrics"].get("postprocess_median_ms") or 0.0)
            current_budget = max(0.0, float(rolling) - current_elapsed) if copied.get("current_frame_started_at") else 0.0
            other_frames = max(0, remaining - (1 if copied.get("current_frame_started_at") else 0))
            copied["eta_seconds"] = round((current_budget + other_frames * float(rolling) + postprocess) / 1000.0, 1)
        else:
            copied["eta_seconds"] = None
        copied["stage_states"] = ProgressStore._stage_states(copied)
        return copied

    @staticmethod
    def _duration_ms(start: Any, end: Any) -> float | None:
        if not isinstance(start, str) or not isinstance(end, str):
            return None
        try:
            first = datetime.fromisoformat(start.replace("Z", "+00:00"))
            last = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError:
            return None
        return round(max(0.0, (last - first).total_seconds()) * 1000.0, 1)

    @staticmethod
    def _stage_states(summary: dict[str, Any]) -> dict[str, str]:
        source = summary.get("pipeline_step_statuses") or {}
        translate = {"pending": "WAITING", "running": "RUNNING", "success": "DONE",
                     "failed": "FAILED", "skipped": "SKIPPED"}
        values = {
            "upload": translate.get(source.get("upload"), "WAITING"),
            "validation": translate.get(source.get("validation"), "WAITING"),
            "model_yolo": translate.get(source.get("yolo"), "WAITING"),
            "frame_scene": translate.get(source.get("frame_processing"), "WAITING"),
            "all_object_summary": translate.get(source.get("all_object_geometry"), "WAITING"),
            "dual_gate_selection": translate.get(source.get("selection"), "WAITING"),
            "package": translate.get(source.get("package"), "WAITING"),
            "p4_communication": translate.get(source.get("p4_delivery"), "WAITING"),
        }
        delivery = summary.get("delivery_state")
        if delivery == "waiting_p4":
            values["p4_communication"] = "WAITING_P4"
        elif delivery == "sending":
            values["p4_communication"] = "SENDING"
        elif delivery == "done":
            values["p4_communication"] = "DONE"
        elif delivery == "failed":
            values["p4_communication"] = "FAILED"
        return values

    @staticmethod
    def _event_matches(
        event: dict[str, Any], scan_id: str | None, request_id: str | None
    ) -> bool:
        return bool(
            (scan_id and event.get("scan_id") == scan_id)
            or (request_id and event.get("request_id") == request_id)
        )

    @classmethod
    def _dedupe_events(cls, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[int] = set()
        unique: list[dict[str, Any]] = []
        for event in events:
            event_id = int(event["event_id"])
            if event_id not in seen:
                unique.append(event)
                seen.add(event_id)
        return unique

    def snapshot(
        self,
        *,
        active_user_scan_id: str | None = None,
        records: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._reconcile_records(records or [])
            active_request_values = [
                self._summaries.get(
                    self._request_scan.get(request_id, f"request:{request_id}")
                )
                for request_id in self._active_user_requests
            ]
            current_value = self._summaries.get(active_user_scan_id or "")
            if current_value is None:
                current_value = next(
                    (value for value in active_request_values if value is not None),
                    None,
                )
            if current_value is not None and current_value.get("visibility") != "user":
                current_value = None
            if current_value is not None and current_value.get("final_status") != "active":
                current_value = None
            current = (
                None
                if current_value is None
                else self._copy_summary(current_value)
            )
            user_values = [
                value for value in self._summaries.values()
                if value.get("visibility") == "user"
            ]
            summaries = sorted(
                (self._copy_summary(value) for value in user_values),
                key=self._summary_order,
                reverse=True,
            )
            terminal = [
                value for value in user_values
                if value.get("final_status") in {"pc_completed", "sent", "failed"}
            ]
            last_value = max(terminal, key=self._summary_order) if terminal else None
            last_completed = None if last_value is None else self._copy_summary(last_value)
            display_value = current_value or last_value
            if display_value is None:
                events = []
            else:
                events = self._dedupe_events([
                    event for event in self._events
                    if self._event_matches(
                        event,
                        display_value.get("scan_id"),
                        display_value.get("request_id"),
                    )
                ])[-100:]
        ui_state = "active" if current is not None else "idle"
        if current is None and last_completed is not None:
            end = last_completed.get("user_scan_finished_at") or last_completed.get("processing_completed_at") or last_completed.get("finished_at")
            age = self._seconds_since(end)
            if age is not None and age <= COMPLETED_FOCUS_SECONDS:
                ui_state = "failed" if last_completed.get("final_status") == "failed" else "completed"
        return {
            "schema_version": "vision_console_state.v2",
            "generated_at": _utc_now(),
            "current_scan": current,
            "ui_state": ui_state,
            "current_user_scan": current,
            "last_completed_user_scan": last_completed,
            "recent_events": events,
            "scans": [
                summary for summary in summaries
                if current is None or summary.get("scan_id") != current.get("scan_id")
            ],
            "persistence_error": self.persistence_error,
        }

    @staticmethod
    def _seconds_since(value: Any) -> float | None:
        if not isinstance(value, str):
            return None
        try:
            then = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return max(0.0, (datetime.now(timezone.utc) - then).total_seconds())

    def _reconcile_records(self, records: list[dict[str, Any]]) -> None:
        for record in records:
            scan_id = record.get("scan_id")
            summary = self._summaries.get(str(scan_id or ""))
            if summary is None:
                continue
            role = record.get("job_role")
            visibility = record.get("visibility")
            if role in JOB_ROLES:
                summary["job_role"] = role
            if visibility in VISIBILITIES:
                summary["visibility"] = visibility
            status = record.get("status")
            if status in {"decision_ready", "completed"} and summary.get("visibility") == "user":
                summary["pipeline_result_status"] = record.get("pipeline_result_status")
                summary["metrics"]["full_scene_ready"] = bool(
                    record.get("full_scene_ready", False)
                )
                if record.get("package_length") is not None:
                    summary["metrics"]["package_size_bytes"] = record["package_length"]
                completed_at = (
                    record.get("decision_ready_at")
                    or summary.get("processing_completed_at")
                    or record.get("finished_at")
                )
                if completed_at:
                    summary["processing_completed_at"] = completed_at
                    summary["package_ready_at"] = summary.get("package_ready_at") or completed_at
                    if summary.get("final_status") != "sent":
                        summary["final_status"] = "pc_completed"
                        summary["finished_at"] = completed_at
                        summary["delivery_state"] = "waiting_p4"
                        summary["active_stage"] = None
                        summary["eta_seconds"] = None
                        self._advance_stage(summary["pipeline_step_statuses"], "package", "success")
            elif status == "failed" and summary.get("visibility") == "user":
                summary["final_status"] = "failed"
                summary["finished_at"] = record.get("finished_at") or summary.get("finished_at")

    def event_visible_to_user(self, event: dict[str, Any]) -> bool:
        if event.get("visibility") in VISIBILITIES:
            return event.get("visibility") == "user"
        scan_id = event.get("scan_id")
        request_id = event.get("request_id")
        with self._lock:
            summary = self._summaries.get(
                str(scan_id) if scan_id else f"request:{request_id}"
            )
            return bool(summary and summary.get("visibility") == "user")

    def scan_detail(self, scan_id: str) -> dict[str, Any] | None:
        with self._lock:
            summary = self._summaries.get(scan_id)
            if summary is None:
                return None
            request_id = summary.get("request_id")
            events = self._dedupe_events([
                event
                for event in self._events
                if self._event_matches(event, scan_id, request_id)
            ])
            return {"summary": self._copy_summary(summary), "events": events}

    def events_since(
        self, last_event_id: int | None, *, user_only: bool = False
    ) -> list[dict[str, Any]]:
        with self._lock:
            return [
                event
                for event in self._events
                if (last_event_id is None or int(event["event_id"]) > last_event_id)
                and (not user_only or self.event_visible_to_user(event))
            ]

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(
            maxsize=self.client_queue_size
        )
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)


class ScanTrafficProgressMiddleware:
    """Transparent ASGI receive/send observer for the two frozen P4 routes."""

    def __init__(
        self,
        app: Any,
        store: ProgressStore,
        on_real_traffic_start: Any = None,
        on_upload_without_job: Any = None,
        on_package_completed: Any = None,
    ) -> None:
        self.app = app
        self.store = store
        self.on_real_traffic_start = on_real_traffic_start
        self.on_upload_without_job = on_upload_without_job
        self.on_package_completed = on_package_completed

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        method = str(scope.get("method", "")).upper()
        if method == "POST" and path == "/api/v1/scan/jobs":
            await self._upload(scope, receive, send)
            return
        match = re.fullmatch(r"/api/v1/scans/([^/]+)/decision-package", path)
        if method == "GET" and match:
            await self._package(scope, receive, send, match.group(1))
            return
        await self.app(scope, receive, send)

    async def _upload(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        request_id = "req_" + __import__("uuid").uuid4().hex
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_role = headers.get(b"x-visionpc-job-role", b"user").decode(
            "ascii", "ignore"
        ).lower()
        job_role = raw_role if raw_role in JOB_ROLES else "user"
        visibility = "user" if job_role == "user" else "diagnostics"
        state = scope.setdefault("state", {})
        state["progress_request_id"] = request_id
        state["visionpc_job_role"] = job_role
        state["visionpc_visibility"] = visibility
        self.store.set_request_identity(
            request_id, job_role, visibility, active=True
        )
        if job_role == "user" and self.on_real_traffic_start is not None:
            self.on_real_traffic_start()
        try:
            total = int(headers[b"content-length"]) if b"content-length" in headers else None
        except ValueError:
            total = None
        received = 0
        started = time.monotonic()
        last_bytes = 0
        last_emit = started
        emitted_start = False
        response_status: int | None = None

        async def observed_receive() -> dict[str, Any]:
            nonlocal received, last_bytes, last_emit, emitted_start
            message = await receive()
            message_type = message.get("type")
            if message_type == "http.disconnect":
                self.store.emit(
                    "failed", request_id=request_id, status="error",
                    bytes_current=received, bytes_total=total,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    message="上传中断", error_code="upload_client_disconnected",
                    job_role=job_role, visibility=visibility,
                )
                return message
            if message_type != "http.request":
                return message
            chunk = message.get("body", b"")
            if not emitted_start:
                emitted_start = True
                self.store.emit(
                    "upload_started", request_id=request_id,
                    bytes_current=0, bytes_total=total,
                    message="P4 开始上传扫描数据",
                    job_role=job_role, visibility=visibility,
                )
            received += len(chunk)
            now = time.monotonic()
            if received - last_bytes >= 256 * 1024 or now - last_emit >= 0.15:
                self.store.emit(
                    "upload_progress", request_id=request_id,
                    bytes_current=received, bytes_total=total,
                    elapsed_ms=int((now - started) * 1000),
                    message=("正在接收扫描数据" if total is not None else "正在接收扫描数据（总量未知）"),
                    job_role=job_role, visibility=visibility,
                )
                last_bytes, last_emit = received, now
            if not message.get("more_body", False):
                self.store.emit(
                    "upload_completed", request_id=request_id,
                    bytes_current=received, bytes_total=total,
                    elapsed_ms=int((now - started) * 1000),
                    message="扫描上传字节接收完成",
                    job_role=job_role, visibility=visibility,
                )
            return message

        async def observed_send(message: dict[str, Any]) -> None:
            nonlocal response_status
            if message.get("type") == "http.response.start":
                response_status = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, observed_receive, observed_send)
        finally:
            self.store.set_request_identity(
                request_id, job_role, visibility, active=False
            )
            if (
                job_role == "user"
                and response_status != 202
                and self.on_upload_without_job is not None
            ):
                self.on_upload_without_job()

    async def _package(
        self, scope: dict[str, Any], receive: Any, send: Any, scan_id: str
    ) -> None:
        sent = 0
        expected: int | None = None
        successful = False
        started = time.monotonic()

        async def observed_send(message: dict[str, Any]) -> None:
            nonlocal sent, expected, successful
            if message.get("type") == "http.response.start":
                successful = int(message.get("status", 500)) == 200
                headers = {key.lower(): value for key, value in message.get("headers", [])}
                try:
                    expected = int(headers[b"content-length"]) if b"content-length" in headers else None
                except ValueError:
                    expected = None
                if successful:
                    self.store.emit(
                        "package_send_started", scan_id=scan_id,
                        bytes_current=0, bytes_total=expected,
                        message="开始发送 package 响应",
                    )
            try:
                await send(message)
            except Exception as exc:
                if successful:
                    self.store.emit(
                        "failed", scan_id=scan_id, status="error",
                        bytes_current=sent, bytes_total=expected,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        message="package发送中断",
                        error_code="package_send_disconnected",
                        error_detail=type(exc).__name__,
                    )
                raise
            if message.get("type") == "http.response.body" and successful:
                sent += len(message.get("body", b""))
                self.store.emit(
                    "package_send_progress", scan_id=scan_id,
                    bytes_current=sent, bytes_total=expected,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    message="正在发送 package 响应",
                )
                if not message.get("more_body", False):
                    self.store.emit(
                        "package_send_completed", scan_id=scan_id,
                        status="success", bytes_current=sent, bytes_total=expected,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        message="PC已完整发送package响应",
                    )

        await self.app(scope, receive, observed_send)
        if successful and self.on_package_completed is not None:
            self.on_package_completed()
