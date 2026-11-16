from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import math
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import (
    DEVICE_ID,
    PATIENT_ID,
    PC_AUTO_START_MIN_VALID_ROWS,
    PC_AUTO_START_WINDOW_SECONDS,
    PC_AUTO_STOP_STALE_SECONDS,
    PC_NODE_STALE_MS,
    PC_SESSION_WATCH_INTERVAL_SECONDS,
    PC_STREAM_CSV_PATH,
    PC_STREAM_CSV_REPLAY_SPEED,
    PC_STREAM_SOURCE_MODE,
    SCHEMA_VERSION,
    SCORE_BASE_URL,
    SCORE_FULL_RESULT_POLL_INTERVAL_SECONDS,
    SCORE_FULL_RESULT_WAIT_SECONDS,
    TRAINING_DATA_ROOT,
    TRAINING_MIN_SESSION_SECONDS,
    TRAINING_MIN_VALID_ROWS,
    TRAINING_SCORE_POLL_INTERVAL_SECONDS,
    TRAINING_SCORE_TIMEOUT_SECONDS,
)
from rehab_training_data.resolver import TrainingDataResolver

from .device_history_store import device_history_store
from .local_csv_stream_service import LocalCsvStreamService
from .models import RealtimeMessage
from .pc_udp_ingest_service import CSV_COLUMNS, CsvFrameParser, PcUdpIngestService
from .session_store import store
from .session_v12_scoring_service import session_v12_scoring_service
from .task_registry_adapter import legacy_task_fields, resolved_task_fields
from .task_library_service import task_library_service
from .websocket_hub import hub

IDLE_SESSION_ID = "SESSION_IDLE"
MAX_LOG_ROWS = 500
BUSY_STATES = {"RECORDING", "FINALIZING", "SCORING"}
SESSION_ID_RE = re.compile(r"^[0-9]{8}_TASK[0-9]{3}$")
CSV_FILE_RE = re.compile(r"^DATA_SEQ[0-9]{10}\.csv$")
TASK_FIELDS = frozenset(
    {
        "task_name",
        "csv_file",
        "start_time",
        "end_time",
        "start_log_seq",
        "end_log_seq",
        "end_reason",
    }
)
AUTO_SCORE_MAX_RETRIES = 3
AUTO_SCORE_RETRY_DELAYS_SECONDS = (2, 5, 10)
TRANSIENT_SCORE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}
NON_RETRYABLE_SCORE_ERROR_CODES = {
    "invalid_session_id",
    "invalid_task_contract",
    "invalid_csv_size",
    "invalid_csv_sha256",
    "task_too_large",
    "csv_too_large",
    "session_content_conflict",
    "csv_header_contract_invalid",
    "csv_log_seq_mismatch",
    "csv_sha256_mismatch",
}


def _score_path_resolver() -> TrainingDataResolver:
    return TrainingDataResolver(training_root=TRAINING_DATA_ROOT)


IMU_CHANNEL_SEMANTICS = {
    1: {"segment": "upper_arm", "label": "上臂"},
    2: {"segment": "trunk", "label": "后腰/躯干"},
    3: {"segment": "forearm", "label": "前臂"},
    4: {"segment": "wrist", "label": "手腕"},
}
EMG_CHANNEL_SEMANTICS = {
    1: {"muscle_id": "finger_extensors_right", "label": "指伸肌"},
    2: {"muscle_id": "biceps_brachii_right", "label": "肱二头肌"},
    3: {"muscle_id": "triceps_brachii_right", "label": "肱三头肌"},
    4: {"muscle_id": "extensor_carpi_ulnaris_right", "label": "尺侧腕伸肌"},
}
class ScoreJobRequestError(ValueError):
    def __init__(self, phase: str, status_code: int, payload: dict[str, Any]):
        self.phase = phase
        self.status_code = status_code
        self.payload = payload
        self.error_code = str(payload.get("error_code") or payload.get("code") or "")
        message = payload.get("message") or payload.get("detail") or payload
        super().__init__(f"Score job {phase} failed: {status_code} {message}")


def _reject_duplicate_task_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate task.json field: {key}")
        result[key] = value
    return result


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def _request_json(
    method: str,
    url: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload if isinstance(payload, dict) else {}
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            payload = {"message": str(exc)}
        return exc.code, payload if isinstance(payload, dict) else {}


class PcLocalRuntimeService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._configured_stream_source_mode = PC_STREAM_SOURCE_MODE
        self._stream_source_mode = self._configured_stream_source_mode
        self._udp = PcUdpIngestService(self._receive_udp_row)
        self._csv_stream = (
            LocalCsvStreamService(
                self._receive_udp_row,
                PC_STREAM_SOURCE_MODE,
                PC_STREAM_CSV_PATH,
                PC_STREAM_CSV_REPLAY_SPEED,
            )
            if PC_STREAM_SOURCE_MODE in {"csv_tail", "csv_replay"}
            else None
        )
        self._monitor_task: asyncio.Task | None = None
        self._score_task: asyncio.Task | None = None
        self._runtime_signatures: dict[str, tuple[Any, ...]] = {}
        self._active_runtime_csv: tuple[str, str, str] | None = None
        self._auto_replay_started_sessions: set[str] = set()
        self._auto_replay_completed_sessions: set[str] = set()
        self._score_submission_started: set[str] = set()
        self._auto_score_tasks: dict[str, asyncio.Task] = {}
        self._auto_score_submission_state: dict[str, str] = {}
        self._auto_score_last_error: dict[str, str] = {}
        self._auto_score_retry_count: dict[str, int] = {}
        self._auto_score_next_retry_at_ms: dict[str, int | None] = {}
        self._selected_session_id = ""
        self._selected_csv_file = ""
        self._csv_activation_state = "discovering"
        self._csv_activation_error: str | None = None
        self._training_state = "IDLE"
        self._scoring_status = "idle"
        self._active_session_id = IDLE_SESSION_ID
        self._last_completed_session_id = ""
        self._active_task: dict[str, Any] | None = None
        self._rows: list[dict[str, Any]] = []
        self._recording_started_at_ms: int | None = None
        self._recording_ended_at_ms: int | None = None
        self._counter_at_start = {"invalid": 0, "duplicate": 0}
        self._counter_at_stop: dict[str, int] | None = None
        self._last_row: dict[str, Any] | None = None
        self._prestart_valid_rows: deque[tuple[float, dict[str, Any]]] = deque(
            maxlen=max(PC_AUTO_START_MIN_VALID_ROWS * 4, PC_AUTO_START_MIN_VALID_ROWS)
        )
        self._last_recording_valid_monotonic: float | None = None
        self._sessions: dict[str, dict[str, Any]] = {}
        self._reports: dict[str, dict[str, Any]] = {}
        self._logs: dict[str, dict[str, deque[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=MAX_LOG_ROWS))
        )
        self._history_loaded = False
        self._snapshot = self._build_snapshot()

    async def start(self) -> None:
        total_started = time.perf_counter()
        history_load_ms = 0.0
        score_reconcile_ms = 0.0
        stream_bind_ms = 0.0
        publish_ms = 0.0
        if not self._history_loaded:
            phase_started = time.perf_counter()
            self._load_history()
            history_load_ms = (time.perf_counter() - phase_started) * 1000.0
        phase_started = time.perf_counter()
        await self._refresh_score_runtime_sessions()
        score_reconcile_ms = (time.perf_counter() - phase_started) * 1000.0
        phase_started = time.perf_counter()
        if self._csv_stream is not None:
            await self._csv_stream.start()
        else:
            await self._udp.start()
        stream_bind_ms = (time.perf_counter() - phase_started) * 1000.0
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(
                self._monitor_stream_state(),
                name=f"pc-{self._stream_source_mode}-stream-state-monitor",
            )
        phase_started = time.perf_counter()
        await self._publish()
        publish_ms = (time.perf_counter() - phase_started) * 1000.0
        total_ms = (time.perf_counter() - total_started) * 1000.0
        print(
            "PC_RUNTIME_STARTUP: "
            f"history_load_ms={history_load_ms:.1f} "
            f"score_reconcile_ms={score_reconcile_ms:.1f} "
            f"stream_bind_ms={stream_bind_ms:.1f} "
            f"publish_ms={publish_ms:.1f} "
            f"total_ms={total_ms:.1f}",
            flush=True,
        )

    async def stop(self) -> None:
        tasks = [
            task
            for task in (
                self._monitor_task,
                self._score_task,
                *self._auto_score_tasks.values(),
            )
            if task
        ]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._monitor_task = None
        self._score_task = None
        self._auto_score_tasks = {}
        await self._udp.stop()
        if self._csv_stream is not None:
            await self._csv_stream.stop()
            self._csv_stream = None

    async def start_training(self) -> dict[str, Any]:
        async with self._lock:
            if self._training_state in BUSY_STATES:
                raise ValueError("A training session is already active.")
            if self._score_task is not None and not self._score_task.done():
                raise ValueError("A training session is already active.")
            if self._stream_source_mode != "udp":
                raise ValueError(
                    "Local data viewing mode cannot start formal training."
                )
            if self._udp.stream_status() != "receiving" or self._last_row is None:
                raise ValueError("No current valid device data is available.")
            task = task_library_service.active_task()
            if task is None:
                raise ValueError("No unique active supported task is configured.")
            session_id = self._reserve_session_id()
            self._begin_training_locked(
                task,
                session_id,
                initial_rows=[],
                started_at_ms=_now_ms(),
                last_valid_monotonic=time.monotonic(),
            )
        await self._publish()
        return self.current_training()

    async def stop_training(self) -> dict[str, Any]:
        async with self._lock:
            if self._training_state != "RECORDING":
                raise ValueError("No recording session is active.")
            self._begin_finalizing_locked(_now_ms())
        await self._publish()
        return self.current_training()

    def _begin_training_locked(
        self,
        task: dict[str, Any],
        session_id: str,
        initial_rows: list[dict[str, Any]],
        started_at_ms: int,
        last_valid_monotonic: float,
    ) -> None:
        self._training_state = "RECORDING"
        self._scoring_status = "recording"
        self._active_session_id = session_id
        self._active_task = deepcopy(task)
        self._rows = list(initial_rows)
        self._recording_started_at_ms = started_at_ms
        self._recording_ended_at_ms = None
        self._last_recording_valid_monotonic = last_valid_monotonic
        self._prestart_valid_rows.clear()
        self._counter_at_start = {
            "invalid": self._udp.invalid_frame_count,
            "duplicate": self._udp.duplicate_frame_count,
        }
        self._counter_at_stop = None
        for item in initial_rows:
            self._capture_logs(session_id, item)
        self._save_session(status="RECORDING", report_ready=False)
        self._snapshot = self._build_snapshot()

    def _begin_finalizing_locked(self, ended_at_ms: int) -> bool:
        if self._training_state != "RECORDING":
            return False
        if self._score_task is not None and not self._score_task.done():
            return False
        self._training_state = "FINALIZING"
        self._scoring_status = "finalizing"
        self._recording_ended_at_ms = ended_at_ms
        self._counter_at_stop = {
            "invalid": self._udp.invalid_frame_count,
            "duplicate": self._udp.duplicate_frame_count,
        }
        rows = list(self._rows)
        self._save_session(status="FINALIZING", report_ready=False)
        self._snapshot = self._build_snapshot()
        self._score_task = asyncio.create_task(
            self._finalize(rows),
            name=f"score-{self._active_session_id}",
        )
        return True

    def current_training(self) -> dict[str, Any]:
        active = self._sessions.get(self._active_session_id) or {}
        stats = self._active_stats()
        row_count = (
            active.get("valid_row_count")
            or stats.get("valid_frame_count")
            or len(self._rows)
        )
        duration_seconds = active.get("duration_seconds") or (
            round(float(stats["csv_last_time_ms"]) / 1000, 3)
            if isinstance(stats.get("csv_last_time_ms"), (int, float))
            else self._recording_duration_seconds()
        )
        return {
            "schema_version": "doctor_training_report_v1",
            "session_id": (
                self._active_session_id
                if self._active_session_id != IDLE_SESSION_ID
                else None
            ),
            "state": self._training_state,
            "recording_row_count": row_count,
            "recording_duration_seconds": duration_seconds,
            "report_ready": bool(active.get("report_ready"))
            or self._training_state == "COMPLETED",
            "can_start": False,
            "stream_source_mode": self._stream_source_mode,
            "data_stream_status": stats["data_stream_status"],
            "udp_stream_status": self._udp.udp_stream_status(),
            "parse_status": stats["parse_status"],
        }

    async def csv_replay_control(
        self, action: str, speed: float | None = None
    ) -> dict[str, Any]:
        if self._csv_stream is None:
            control = {"accepted": False, "reason": "not_csv_replay"}
            return {"csv_replay": control, "snapshot": self.snapshot(), **control}
        control = self._csv_stream.control(action, speed)
        async with self._lock:
            if control.get("accepted") is True:
                self._csv_activation_state = self._csv_activation_state_from_stats(
                    control
                )
                self._csv_activation_error = None
            else:
                self._csv_activation_error = str(control.get("reason") or "")
            self._mark_auto_replay_complete_if_ready(control)
            self._snapshot = self._build_snapshot()
            snapshot = self.snapshot()
        await self._publish()
        return {"csv_replay": control, "snapshot": snapshot, **control}

    def _active_stats(self) -> dict[str, Any]:
        if self._csv_stream is not None:
            return self._csv_stream.stats()
        stats = self._udp.stats()
        return {
            "stream_source_mode": "udp",
            "stream_source_label": "device_udp",
            "csv_valid_frame_count": None,
            "csv_invalid_frame_count": None,
            "csv_duplicate_frame_count": None,
            "csv_last_column_count": None,
            "csv_last_parse_error": None,
            "csv_current_row": None,
            "csv_total_rows": None,
            "csv_last_log_seq": None,
            "csv_last_time_ms": None,
            "csv_file_name": "",
            "csv_file_size": None,
            "csv_read_offset": None,
            "csv_rows_read": None,
            "csv_last_new_data_at_ms": None,
            "csv_file_status": "",
            "csv_replay_state": "",
            "csv_replay_speed": None,
            **stats,
        }

    def _active_stream_status(self) -> str:
        return str(self._active_stats()["data_stream_status"])

    def _active_source_origin(self) -> str:
        if self._stream_source_mode == "csv_tail":
            return "local_csv_tail"
        if self._stream_source_mode == "csv_replay":
            return "local_csv_replay"
        return "pc_udp_6006_ingest"

    async def _receive_udp_row(
        self,
        row: dict[str, Any] | None,
        addr: tuple[str, int] | None,
    ) -> None:
        del addr
        received_monotonic = time.monotonic()
        async with self._lock:
            if row is not None:
                self._last_row = row
                if (
                    self._stream_source_mode == "udp"
                    and self._training_state == "RECORDING"
                ):
                    self._rows.append(row)
                    self._last_recording_valid_monotonic = received_monotonic
                    self._capture_logs(self._active_session_id, row)
                elif (
                    self._stream_source_mode == "udp"
                    and self._training_state not in BUSY_STATES
                ):
                    self._record_prestart_row_locked(row, received_monotonic)
                elif (
                    self._stream_source_mode == "csv_tail"
                    and self._active_session_id != IDLE_SESSION_ID
                ):
                    self._capture_logs(self._active_session_id, row)
            else:
                self._mark_auto_replay_complete_if_ready()
            self._snapshot = self._build_snapshot()
        await self._publish()

    def _record_prestart_row_locked(
        self,
        row: dict[str, Any],
        received_monotonic: float,
    ) -> None:
        self._prune_prestart_rows_locked(received_monotonic)
        self._prestart_valid_rows.append((received_monotonic, row))
        if len(self._prestart_valid_rows) < PC_AUTO_START_MIN_VALID_ROWS:
            return
        if self._score_task is not None and not self._score_task.done():
            return
        task = task_library_service.active_task()
        if task is None:
            return
        rows = [item[1] for item in self._prestart_valid_rows]
        first_row = rows[0]
        started_at_ms = int(first_row.get("_received_at_ms") or _now_ms())
        self._begin_training_locked(
            task,
            self._reserve_session_id(),
            initial_rows=rows,
            started_at_ms=started_at_ms,
            last_valid_monotonic=received_monotonic,
        )

    def _prune_prestart_rows_locked(self, now_monotonic: float) -> None:
        while (
            self._prestart_valid_rows
            and now_monotonic - self._prestart_valid_rows[0][0]
            > PC_AUTO_START_WINDOW_SECONDS
        ):
            self._prestart_valid_rows.popleft()

    async def _monitor_stream_state(self) -> None:
        initial_stats = self._active_stats()
        previous = (
            initial_stats["data_stream_status"],
            initial_stats["udp_stream_status"],
            initial_stats["parse_status"],
        )
        while True:
            await asyncio.sleep(PC_SESSION_WATCH_INTERVAL_SECONDS)
            runtime_changed = await self._refresh_score_runtime_sessions()
            stats = self._active_stats()
            self._mark_auto_replay_complete_if_ready(stats)
            auto_finalized = False
            if (
                self._stream_source_mode == "udp"
                and self._training_state == "RECORDING"
                and self._last_recording_valid_monotonic is not None
                and time.monotonic() - self._last_recording_valid_monotonic
                >= PC_AUTO_STOP_STALE_SECONDS
            ):
                async with self._lock:
                    auto_finalized = self._begin_finalizing_locked(_now_ms())
                if auto_finalized:
                    stats = self._active_stats()
            current = (
                stats["data_stream_status"],
                stats["udp_stream_status"],
                stats["parse_status"],
            )
            if (
                auto_finalized
                or
                runtime_changed
                or current != previous
                or self._training_state == "RECORDING"
            ):
                previous = current
                async with self._lock:
                    self._snapshot = self._build_snapshot()
                await self._publish()

    async def _finalize(self, rows: list[dict[str, Any]]) -> None:
        session_id = self._active_session_id
        reason = self._invalid_reason(rows)
        if reason:
            async with self._lock:
                self._training_state = "INVALID"
                self._scoring_status = reason
                self._save_session(
                    status="INVALID",
                    report_ready=False,
                    error=reason,
                )
                device_history_store.save_runtime_summary(
                    session_id, self._runtime_summary(reason)
                )
                self._last_completed_session_id = session_id
                self._snapshot = self._build_snapshot()
            await self._publish()
            return

        csv_bytes = self._csv_bytes(rows)
        first_seq = int(rows[0]["_log_seq_int"])
        csv_name = f"DATA_SEQ{first_seq:010d}.csv"
        task = self._score_task_contract(rows, csv_name)
        task_bytes = self._task_bytes_from_contract(task)
        async with self._lock:
            self._training_state = "SCORING"
            self._scoring_status = "processing"
            self._save_session(status="SCORING", report_ready=False)
            self._snapshot = self._build_snapshot()
        await self._publish()

        try:
            result, job_result = await asyncio.to_thread(
                self._run_score_job,
                session_id,
                task_bytes,
                csv_bytes,
            )
            if result.get("valid") is not True:
                async with self._lock:
                    self._training_state = "INVALID"
                    self._scoring_status = "score_invalid"
                    self._save_session(
                        status="INVALID",
                        report_ready=False,
                        score=result,
                        error=str(result.get("invalid_reason") or "score_invalid"),
                    )
                    device_history_store.save_runtime_summary(
                        session_id, self._runtime_summary("score_invalid")
                    )
                    self._last_completed_session_id = session_id
                    self._snapshot = self._build_snapshot()
                await self._publish()
                return

            report = self._build_report(result)
            device_history_store.save_report(session_id, report)
            async with self._lock:
                self._reports[session_id] = report
                self._training_state = "COMPLETED"
                self._scoring_status = "succeeded"
                self._save_session(
                    status="COMPLETED",
                    report_ready=True,
                    score=result,
                )
                device_history_store.save_runtime_summary(
                    session_id, self._runtime_summary("")
                )
                self._last_completed_session_id = session_id
                self._snapshot = self._build_snapshot(result)
            await self._publish()
        except Exception as exc:
            error = str(exc)
            async with self._lock:
                self._training_state = "FAILED"
                self._scoring_status = "failed"
                self._save_session(
                    status="COMPLETED",
                    report_ready=False,
                    error=error,
                )
                device_history_store.save_runtime_summary(
                    session_id, self._runtime_summary(error)
                )
                self._last_completed_session_id = session_id
                self._snapshot = self._build_snapshot()
            await self._publish()

    def _invalid_reason(self, rows: list[dict[str, Any]]) -> str:
        if len(rows) < TRAINING_MIN_VALID_ROWS:
            return "valid_row_count_below_minimum"
        if self._recording_duration_seconds() < TRAINING_MIN_SESSION_SECONDS:
            return "session_duration_below_minimum"
        if any("_log_seq_int" not in row for row in rows):
            return "log_seq_missing"
        if any("_time_ms_float" not in row for row in rows):
            return "time_ms_missing"
        captured_span = (
            float(rows[-1]["_time_ms_float"])
            - float(rows[0]["_time_ms_float"])
        ) / 1000
        if captured_span < TRAINING_MIN_SESSION_SECONDS:
            return "captured_data_duration_below_minimum"
        if any(len(row.get("_raw_values", ())) != len(CSV_COLUMNS) for row in rows):
            return "csv_column_contract_invalid"
        return ""

    def _score_task_contract(
        self, rows: list[dict[str, Any]], csv_name: str
    ) -> dict[str, Any]:
        started = self._recording_started_at_ms or int(rows[0]["_received_at_ms"])
        ended = self._recording_ended_at_ms or int(rows[-1]["_received_at_ms"])
        return {
            "task_name": self._active_task["internal_score_task_name"],
            "csv_file": csv_name,
            "start_time": _iso_from_ms(started),
            "end_time": _iso_from_ms(ended),
            "start_log_seq": int(rows[0]["_log_seq_int"]),
            "end_log_seq": int(rows[-1]["_log_seq_int"]),
            "end_reason": "user_stop",
        }

    @classmethod
    def _task_bytes_from_contract(cls, task: dict[str, Any]) -> bytes:
        cls._validate_task_contract(task)
        return json.dumps(
            task, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")

    @classmethod
    def _parse_task_contract_bytes(cls, task_bytes: bytes) -> dict[str, Any]:
        try:
            task = json.loads(
                task_bytes.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_task_keys,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("task.json is not valid UTF-8 JSON") from exc
        if not isinstance(task, dict):
            raise ValueError("task.json must contain one JSON object")
        cls._validate_task_contract(task)
        return task

    @staticmethod
    def _validate_task_contract(task: dict[str, Any]) -> None:
        if set(task) != TASK_FIELDS:
            raise ValueError("task.json must contain exactly seven frozen fields")
        for field in ("task_name", "csv_file", "start_time", "end_time", "end_reason"):
            if not isinstance(task[field], str) or not task[field].strip():
                raise ValueError(f"{field} must be a non-empty string")
        for field in ("start_log_seq", "end_log_seq"):
            if isinstance(task[field], bool) or not isinstance(task[field], int):
                raise ValueError(f"{field} must be an integer")
        if task["end_log_seq"] < task["start_log_seq"]:
            raise ValueError("end_log_seq precedes start_log_seq")
        if not CSV_FILE_RE.fullmatch(task["csv_file"]):
            raise ValueError("csv_file does not match the frozen filename contract")
        if task["end_reason"] not in {"user_stop", "completed"}:
            raise ValueError("end_reason is not a finalized successful session")

    def _run_score_job(
        self,
        session_id: str,
        task_bytes: bytes,
        csv_bytes: bytes,
        on_job_accepted: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        digest = hashlib.sha256(csv_bytes).hexdigest()
        status, created = _request_json(
            "POST",
            f"{SCORE_BASE_URL}/api/v1/score/jobs",
            task_bytes,
            {
                "Content-Type": "application/json",
                "Content-Length": str(len(task_bytes)),
                "X-Session-Id": session_id,
                "X-CSV-Size": str(len(csv_bytes)),
                "X-CSV-SHA256": digest,
            },
        )
        if status not in {200, 201}:
            raise ScoreJobRequestError("creation", status, created)
        job_id = str(created.get("job_id") or "")
        if job_id != session_id:
            raise ValueError("Score job identity does not match session_id.")
        if on_job_accepted is not None:
            on_job_accepted()
        if created.get("upload_required") is True:
            upload_status, upload = _request_json(
                "PUT",
                f"{SCORE_BASE_URL}/api/v1/score/jobs/{job_id}/csv",
                csv_bytes,
                {
                    "Content-Type": "text/csv",
                    "Content-Length": str(len(csv_bytes)),
                },
                timeout=30.0,
            )
            if upload_status not in {200, 202}:
                raise ScoreJobRequestError("csv_upload", upload_status, upload)

        deadline = time.monotonic() + TRAINING_SCORE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            status_code, job = _request_json(
                "GET", f"{SCORE_BASE_URL}/api/v1/score/jobs/{job_id}"
            )
            if status_code != 200:
                raise ScoreJobRequestError("status_poll", status_code, job)
            state = str(job.get("state") or "")
            if state == "succeeded":
                result_status, compact = _request_json(
                    "GET",
                    f"{SCORE_BASE_URL}/api/v1/score/jobs/{job_id}/result",
                )
                if result_status != 200:
                    raise ScoreJobRequestError("result_fetch", result_status, compact)
                if compact.get("schema_version") != "rehab_score_result_v1":
                    raise ValueError("Unexpected compact score result schema.")
                if compact.get("session_id") != session_id:
                    raise ValueError("Compact score result session_id mismatch.")
                full = self._wait_for_full_score_result(session_id)
                self._validate_full_score_result(session_id, full)
                return full, compact
            if state == "failed":
                raise ValueError(
                    f"Score job failed: {job.get('error_code')} {job.get('message')}"
                )
            time.sleep(TRAINING_SCORE_POLL_INTERVAL_SECONDS)
        raise TimeoutError("Score job timed out.")

    def _wait_for_full_score_result(self, session_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + SCORE_FULL_RESULT_WAIT_SECONDS
        last_error = ""
        while True:
            try:
                return self._read_full_score_result(session_id)
            except FileNotFoundError as exc:
                last_error = str(exc)
            except ValueError:
                raise
            except OSError as exc:
                last_error = str(exc)
            if time.monotonic() >= deadline:
                break
            time.sleep(SCORE_FULL_RESULT_POLL_INTERVAL_SECONDS)
        raise TimeoutError(
            f"Full score_result.json was not available before timeout: {last_error}"
        )

    @staticmethod
    def _full_score_result_path(session_id: str) -> Path:
        if not SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("Invalid score session_id.")
        resolver = _score_path_resolver()
        root = resolver.get_score_root()
        result_path = (
            resolver.session_scoring_path(session_id)
            / "full_result.json"
        ).resolve()
        try:
            result_path.relative_to(root)
        except ValueError as exc:
            raise ValueError("Resolved score result path escapes TRAINING_DATA_ROOT.") from exc
        return result_path

    @classmethod
    def _read_full_score_result(cls, session_id: str) -> dict[str, Any]:
        result_path = cls._full_score_result_path(session_id)
        if not result_path.is_file():
            raise FileNotFoundError(str(result_path))
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Full score_result.json is not a JSON object.")
        return payload

    @classmethod
    def _validate_full_score_result(
        cls, session_id: str, result: dict[str, Any]
    ) -> None:
        if result.get("session_id") != session_id:
            raise ValueError("Full score result session_id mismatch.")
        if result.get("valid") is True and cls._score_final_score(result) is None:
            raise ValueError("Valid full score result has no finite final score.")

    @staticmethod
    def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(row["_raw_values"])
        return output.getvalue().encode("utf-8")

    def _reserve_session_id(self) -> str:
        prefix = datetime.now().strftime("%Y%m%d_TASK")
        occupied = set(self._sessions)
        resolver = _score_path_resolver()
        for root in (
            resolver.score_sessions_root(),
            resolver.score_staging_root(),
        ):
            if root.is_dir():
                occupied.update(
                    item.name for item in root.iterdir() if item.is_dir()
                )
        for index in range(1, 1000):
            session_id = f"{prefix}{index:03d}"
            if session_id in occupied:
                continue
            path = device_history_store.session_directory(session_id)
            try:
                path.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                continue
            return session_id
        raise ValueError("No available session sequence remains for today.")

    def _save_session(
        self,
        status: str,
        report_ready: bool,
        score: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        session_id = self._active_session_id
        source_mode = "device" if self._rows else "pc_local"
        source_origin = "pc_udp_6006_ingest" if self._rows else ""
        started = self._recording_started_at_ms
        ended = self._recording_ended_at_ms
        now_iso = datetime.now(timezone.utc).isoformat()
        previous = self._sessions.get(session_id) or {}
        csv_file = (
            f"DATA_SEQ{int(self._rows[0]['_log_seq_int']):010d}.csv"
            if self._rows and "_log_seq_int" in self._rows[0]
            else None
        )
        final_score = (
                self._score_final_score(score) if isinstance(score, dict) else None
            )
        score_status = "completed" if report_ready and score else self._scoring_status
        summary = {
            "patient_id": PATIENT_ID,
            "session_id": session_id,
            "scenario_id": self._active_task.get("task_id") if self._active_task else "",
            "task": self._active_task.get("display_name") if self._active_task else "",
            "task_id": self._active_task.get("task_id") if self._active_task else "",
            "task_name": self._active_task.get("display_name") if self._active_task else "",
            "internal_score_task_name": self._active_task.get(
                "internal_score_task_name"
            ) if self._active_task else "",
            "status": status,
            "started_at_ms": started,
            "ended_at_ms": ended,
            "started_at": _iso_from_ms(started) if started else None,
            "completed_at": _iso_from_ms(ended) if ended else None,
            "duration_ms": int(self._recording_duration_seconds() * 1000),
            "duration_seconds": self._recording_duration_seconds(),
            "rows_written": len(self._rows),
            "valid_row_count": len(self._rows),
            "invalid_row_count": self._session_invalid_count(),
            "duplicate_row_count": self._session_duplicate_count(),
            "final_score": final_score,
            "report_ready": report_ready,
            "source_mode": source_mode,
            "source_origin": source_origin,
            "source_ip": self._rows[0].get("_source_ip") if self._rows else None,
            "input_row_count": len(self._rows),
            "valid_frame_count": len(self._rows),
            "invalid_frame_count": self._session_invalid_count(),
            "scoring_status": score_status,
            "score_status": score_status,
            "score_valid": bool(score and score.get("valid")),
            "log_drop_count": 0,
            "report_path": "report.json" if report_ready else None,
            "score_result_path": "score_result.json" if score else None,
            "score_job_result_path": "score_job_result.json" if score else None,
            "csv_file": csv_file,
            "csv_relative_path": (
                str(Path("sessions") / session_id / "input" / csv_file)
                if csv_file
                else None
            ),
            "end_reason": "user_stop" if ended else None,
            "formal": bool(self._rows),
            "record_kind": "formal" if self._rows else None,
            "training_status": (
                "completed"
                if ended is not None
                else status.lower()
            ),
            "score_job_id": session_id if self._rows else None,
            "score_error_code": "scoring_failed" if error else None,
            "score_error_message": self._friendly_score_error(error) if error else None,
            "created_at": previous.get("created_at") or now_iso,
            "updated_at": now_iso,
            "error": error or None,
        }
        summary.update(
            resolved_task_fields(
                summary.get("internal_score_task_name") or summary.get("task_name")
            )
        )
        summary.update(
            {
                key: value
                for key, value in legacy_task_fields(summary).items()
                if not summary.get(key)
            }
        )
        self._sessions[session_id] = summary
        device_history_store.save_session(summary)

    @staticmethod
    def _friendly_score_error(error: str) -> str:
        if not error:
            return ""
        if "trajectories directory" in error:
            return "评分服务未生成结果文件"
        if "score_result" in error or "Full score" in error:
            return "评分服务未生成结果文件"
        return "评分未完成"

    def _runtime_summary(self, error: str) -> dict[str, Any]:
        return {
            "schema_version": "rehab_pc_runtime_summary_v1",
            "session_id": self._active_session_id,
            "training_state": self._training_state,
            "score_status": self._scoring_status,
            "valid_row_count": len(self._rows),
            "invalid_row_count": self._session_invalid_count(),
            "duplicate_row_count": self._session_duplicate_count(),
            "duration_seconds": self._recording_duration_seconds(),
            "source_ip": self._rows[0].get("_source_ip") if self._rows else None,
            "error": error or None,
        }

    def _build_report(self, score: dict[str, Any]) -> dict[str, Any]:
        return self._build_report_for_context(
            score,
            task_name=self._active_task["display_name"],
            session_id=self._active_session_id,
            training_started_at_ms=self._recording_started_at_ms,
            duration_seconds=self._recording_duration_seconds(),
        )

    def _build_report_for_context(
        self,
        score: dict[str, Any],
        task_name: str,
        session_id: str,
        training_started_at_ms: int | None,
        duration_seconds: float,
    ) -> dict[str, Any]:
        mapped_score = self._mapped_score(score)
        action_summaries = self._action_summaries(score)
        return {
            "title": "上肢康复训练评估报告",
            "patient": {
                "patient_id": PATIENT_ID,
                "affected_side": "右侧上肢",
                "brs": "",
                "task": task_name,
                "difficulty": "",
                "training_time": _iso_from_ms(training_started_at_ms or _now_ms()),
                "session_id": session_id,
                "device_id": DEVICE_ID,
            },
            "completion": {
                "planned_reps": score.get("target_repetition_count"),
                "completed_reps": score.get("repetition_count"),
                "valid_reps": score.get(
                    "valid_repetition_count", score.get("repetition_count")
                ),
                "duration_sec": duration_seconds,
                "pause_count": None,
                "fully_completed": score.get("valid") is True,
                "end_reason": "user_stop",
            },
            "score": mapped_score,
            "available_metrics": mapped_score["available_metrics"],
            "action_summaries": action_summaries,
            "rep_details": self._legacy_rep_details(action_summaries),
            "kinematics": {},
            "emg_analysis": {},
            "compensation_analysis": mapped_score["compensation"],
            "fatigue_analysis": mapped_score["fatigue"],
            "summary": "",
            "next_recommendations": mapped_score["feedback"],
            "versions": {
                "source_mode": "device",
                "source_origin": "pc_udp_6006_ingest",
                "score_version": score.get("score_version", ""),
                "schema_version": str(score.get("schema_version") or ""),
                "device_id": DEVICE_ID,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "raw_score_result_path": "score_result.json",
                "compact_score_result_path": "score_job_result.json",
            },
            "disclaimer": "报告仅供康复专业人员参考。",
            "demo_notice": "",
        }

    @classmethod
    def _legacy_rep_details(
        cls, action_summaries: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        details = []
        for item in action_summaries:
            compensation = item.get("compensation")
            detected = (
                compensation.get("detected")
                if isinstance(compensation, dict)
                else None
            )
            details.append(
                {
                    "rep_index": item.get("rep_index"),
                    "final_score": item.get("final_score"),
                    "ROM": "暂无数据",
                    "smoothness": "暂无数据",
                    "coordination": "暂无数据",
                    "muscle_performance": "暂无数据",
                    "fatigue": {"level": "暂无数据", "trend": "暂无数据"},
                    "compensation": {
                        "risk": "暂无数据",
                        "status": (
                            "detected"
                            if detected
                            else "not_detected"
                            if detected is False
                            else "暂无数据"
                        ),
                    },
                }
            )
        return details

    def _build_snapshot(
        self, score: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        row = self._last_row
        stats = self._active_stats()
        session_id = (
            self._active_session_id
            if self._active_session_id != IDLE_SESSION_ID
            else IDLE_SESSION_ID
        )
        active_summary = self._sessions.get(session_id) or {}
        active_row_count = (
            active_summary.get("valid_row_count")
            or stats.get("valid_frame_count")
            or len(self._rows)
        )
        active_duration_seconds = (
            active_summary.get("duration_seconds")
            or (
                round(float(stats["csv_last_time_ms"]) / 1000, 3)
                if isinstance(stats.get("csv_last_time_ms"), (int, float))
                else self._recording_duration_seconds()
            )
        )
        stored_report = self._reports.get(self._active_session_id)
        report_score = (
            stored_report.get("score") if isinstance(stored_report, dict) else None
        )
        score_payload = self._mapped_score(score) if score else report_score
        auto_score = {
            "submission_state": active_summary.get("auto_score_submission_state"),
            "last_error": active_summary.get("auto_score_last_error"),
            "retry_count": active_summary.get("auto_score_retry_count", 0),
            "next_retry_at_ms": active_summary.get("auto_score_next_retry_at_ms"),
        }
        return {
            "status": self._training_state,
            "phase": "",
            "next_phase": "",
            "session_id": session_id,
            "patient": {
                "patient_id": PATIENT_ID,
                "profile_label": "当前患者",
                "diagnosis_stage": "",
                "affected_side": "",
                "brs": "",
                "age_band": "",
                "today_sessions": self._today_completed_count(),
                "today_average_score": self._today_average_score(),
                "risk_level": "",
            },
            "task": {
                "task_id": "",
                "name": "",
                "difficulty": "",
                "target_reps": 0,
                "completed_reps": 0,
                "prescription": "",
                "target_angles": {},
            },
            "telemetry": self._telemetry(row),
            "emg": self._emg_payload(row),
            "quality": self._quality(score_payload),
            "devices": self._devices(row),
            "sensor_samples": self._sensor_samples(row),
            "scores": (
                [{"rep_index": 0, "score": score_payload, "quality": self._quality(score_payload)}]
                if score_payload
                else []
            ),
            "events": [],
            "alert": (
                {
                    "level": "warning",
                    "title": "设备数据中断",
                    "message": "实时曲线已停留在最后一条真实数据，请检查设备连接。",
                }
                if stats["data_stream_status"] == "stale"
                else None
            ),
            "report_ready": bool(active_summary.get("report_ready"))
            or self._training_state == "COMPLETED",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "connection_message": self._connection_message(),
            "software_status": "ready",
            **stats,
            "selected_session_id": self._selected_session_id or None,
            "selected_csv_file": self._selected_csv_file or "",
            "csv_activation_state": self._csv_activation_state_from_stats(stats),
            "csv_activation_error": self._csv_activation_error,
            "control_mode": "pc_local",
            "training_status": {
                "session_id": (
                    self._active_session_id
                    if self._active_session_id != IDLE_SESSION_ID
                    else None
                ),
                "state": self._training_state,
                "recording_row_count": active_row_count,
                "recording_duration_seconds": active_duration_seconds,
                "score_state": self._scoring_status,
                "can_start": False,
                "active_task_available": task_library_service.active_task()
                is not None,
                "auto_score": auto_score,
            },
            "raw_log_summary": {
                "rows": active_row_count,
                "sampling_rate_hz": stats["receive_rate_hz"],
                "job_state": self._training_state,
                "score_state": self._scoring_status,
                "udp_datagram_count": stats["udp_datagram_count"],
                "valid_frame_count": stats["valid_frame_count"],
                "invalid_frame_count": stats["invalid_frame_count"],
                "duplicate_frame_count": stats["duplicate_frame_count"],
            },
        }

    @staticmethod
    def _score_summary(score: dict[str, Any]) -> dict[str, Any]:
        summary = score.get("summary")
        return summary if isinstance(summary, dict) else {}

    @staticmethod
    def _score_data_quality(score: dict[str, Any]) -> dict[str, Any]:
        quality = score.get("data_quality")
        return quality if isinstance(quality, dict) else {}

    @classmethod
    def _score_final_score(cls, score: dict[str, Any]) -> float | None:
        summary = cls._score_summary(score)
        for value in (
            summary.get("average_score"),
            score.get("average_score"),
            score.get("final_score"),
        ):
            parsed = cls._number(value)
            if parsed is not None and math.isfinite(parsed):
                return parsed
        return None

    @classmethod
    def _score_subscores(cls, score: dict[str, Any]) -> dict[str, float]:
        raw = score.get("subscores")
        if not isinstance(raw, dict):
            return {}
        result: dict[str, float] = {}
        for key in (
            "speed_score",
            "ROM_score",
            "smoothness_score",
            "coordination_score",
            "muscle_score",
        ):
            value = cls._number(raw.get(key))
            if value is not None and math.isfinite(value):
                result[key] = value
        return result

    @classmethod
    def _score_feedback(cls, score: dict[str, Any]) -> list[str]:
        feedback = score.get("feedback")
        if isinstance(feedback, list):
            return [str(item) for item in feedback]
        items: list[str] = []
        actions = score.get("actions")
        if isinstance(actions, list):
            for action in actions:
                if not isinstance(action, dict):
                    continue
                action_feedback = action.get("feedback")
                if isinstance(action_feedback, list):
                    for item in action_feedback:
                        text = str(item)
                        if text not in items:
                            items.append(text)
        return items

    @classmethod
    def _score_compensation(cls, score: dict[str, Any]) -> dict[str, Any]:
        summary = cls._score_summary(score)
        compensation = score.get("compensation")
        result = deepcopy(compensation) if isinstance(compensation, dict) else {}
        if "compensation_events" in score:
            result["events"] = deepcopy(score.get("compensation_events"))
        if "compensation_action_count" in summary:
            result["action_count"] = summary.get("compensation_action_count")
            result["detected"] = bool(summary.get("compensation_action_count") or 0)
        elif "compensation_action_count" in score:
            result["action_count"] = score.get("compensation_action_count")
            result["detected"] = bool(score.get("compensation_action_count") or 0)
        return result

    @classmethod
    def _available_metrics(cls, score: dict[str, Any]) -> list[str]:
        metrics: list[str] = []
        if cls._score_final_score(score) is not None:
            metrics.append("final_score")
        if cls._score_subscores(score):
            metrics.extend(cls._score_subscores(score).keys())
        actions = score.get("actions")
        if isinstance(actions, list):
            for action in actions:
                if not isinstance(action, dict):
                    continue
                subscores = action.get("subscores")
                if not isinstance(subscores, dict):
                    continue
                for key in ("imu_dtw_score", "emg_score"):
                    if cls._number(subscores.get(key)) is not None and key not in metrics:
                        metrics.append(key)
        if cls._score_compensation(score) and "compensation" not in metrics:
            metrics.append("compensation")
        if cls._score_feedback(score) and "feedback" not in metrics:
            metrics.append("feedback")
        return metrics

    @classmethod
    def _action_summaries(cls, score: dict[str, Any]) -> list[dict[str, Any]]:
        actions = score.get("actions")
        if not isinstance(actions, list):
            return []
        result: list[dict[str, Any]] = []
        for index, action in enumerate(actions, start=1):
            if not isinstance(action, dict):
                continue
            subscores = action.get("subscores")
            subscores = subscores if isinstance(subscores, dict) else {}
            feedback = action.get("feedback")
            result.append(
                {
                    "rep_index": action.get("rep_index", index),
                    "valid": action.get("valid"),
                    "final_score": cls._number(action.get("final_score")),
                    "imu_dtw_score": cls._number(subscores.get("imu_dtw_score")),
                    "emg_score": cls._number(subscores.get("emg_score")),
                    "compensation": deepcopy(action.get("compensation") or {}),
                    "feedback": list(feedback) if isinstance(feedback, list) else [],
                }
            )
        return result

    @classmethod
    def _mapped_score(cls, score: dict[str, Any]) -> dict[str, Any]:
        quality = cls._score_data_quality(score)
        return {
            "session_id": score.get("session_id"),
            "score_version": score.get("score_version", ""),
            "valid": score.get("valid") is True,
            "final_score": cls._score_final_score(score),
            "subscores": cls._score_subscores(score),
            "available_metrics": cls._available_metrics(score),
            "compensation": cls._score_compensation(score),
            "fatigue": deepcopy(score.get("fatigue") or {}),
            "brs": score.get("brs"),
            "feedback": cls._score_feedback(score),
            "data_quality_level": quality.get("level", score.get("data_quality_level")),
            "warnings": list(quality.get("warnings") or score.get("warnings") or []),
            "completed_at": score.get("completed_at"),
        }

    @staticmethod
    def _telemetry(row: dict[str, Any] | None) -> dict[str, Any]:
        if row is None:
            return {
                key: None
                for key in (
                    "shoulder_flexion_deg",
                    "shoulder_abduction_deg",
                    "shoulder_rotation_deg",
                    "elbow_flexion_deg",
                    "forearm_pronation_deg",
                    "wrist_flexion_deg",
                    "trunk_flexion_deg",
                    "trajectory_match",
                    "phase_progress",
                    "spatial_error_cm",
                )
            }
        upper_arm_index = PcLocalRuntimeService._imu_index_for("upper_arm")
        trunk_index = PcLocalRuntimeService._imu_index_for("trunk")
        forearm_index = PcLocalRuntimeService._imu_index_for("forearm")
        wrist_index = PcLocalRuntimeService._imu_index_for("wrist")
        return {
            "shoulder_flexion_deg": PcLocalRuntimeService._imu_number(row, upper_arm_index, "pitch"),
            "shoulder_abduction_deg": PcLocalRuntimeService._imu_number(row, upper_arm_index, "roll"),
            "shoulder_rotation_deg": PcLocalRuntimeService._imu_number(row, upper_arm_index, "yaw"),
            "elbow_flexion_deg": PcLocalRuntimeService._imu_number(row, forearm_index, "pitch"),
            "forearm_pronation_deg": PcLocalRuntimeService._imu_number(row, forearm_index, "roll"),
            "wrist_flexion_deg": PcLocalRuntimeService._imu_number(row, wrist_index, "pitch"),
            "trunk_flexion_deg": PcLocalRuntimeService._imu_number(row, trunk_index, "pitch"),
            "trajectory_match": None,
            "phase_progress": None,
            "spatial_error_cm": None,
        }

    @staticmethod
    def _emg_payload(row: dict[str, Any] | None) -> dict[str, Any]:
        return {
            f"CH{index}": {
                "label": EMG_CHANNEL_SEMANTICS[index]["label"],
                "activation": PcLocalRuntimeService._number(
                    row.get(f"emg_rms_{index}") if row else None
                ),
                "quality": (
                    1 if row and PcLocalRuntimeService._truthy(row.get("emg_valid")) else 0
                ),
                "primary": index == 2,
                "muscle_id": EMG_CHANNEL_SEMANTICS[index]["muscle_id"],
            }
            for index in range(1, 5)
        }

    @staticmethod
    def _imu_index_for(segment: str) -> int:
        for index, value in IMU_CHANNEL_SEMANTICS.items():
            if value["segment"] == segment:
                return index
        raise KeyError(segment)

    @staticmethod
    def _imu_number(row: dict[str, Any], index: int, field: str) -> float | None:
        return PcLocalRuntimeService._number(row.get(f"imu{index}_{field}"))

    def _sensor_samples(
        self, row: dict[str, Any] | None
    ) -> dict[str, list[dict[str, Any]]]:
        if row is None:
            return {"imu": [], "emg": []}
        sequence = int(row["_log_seq_int"])
        received = int(row["_received_at_ms"])
        age_ms = max(0, _now_ms() - received)
        current = self._active_stream_status() == "receiving"
        source = self._active_source_origin()
        imu = []
        for index in range(1, 5):
            valid = self._truthy(row.get(f"imu{index}_valid"))
            semantic = IMU_CHANNEL_SEMANTICS[index]
            imu.append(
                {
                    "sensor_id": f"imu{index}",
                    "label": f"IMU{index} - {semantic['label']}",
                    "source": source,
                    "session_id": self._active_session_id,
                    "sequence": sequence,
                    "received_at_ms": received,
                    "age_ms": age_ms,
                    "tx_ts_us": None,
                    "online": valid and current,
                    "valid": valid,
                    "roll_deg": self._number(row.get(f"imu{index}_roll")),
                    "pitch_deg": self._number(row.get(f"imu{index}_pitch")),
                    "yaw_deg": self._number(row.get(f"imu{index}_yaw")),
                    "gx": self._number(row.get(f"imu{index}_gx")),
                    "gy": self._number(row.get(f"imu{index}_gy")),
                    "gz": self._number(row.get(f"imu{index}_gz")),
                    "ax": self._number(row.get(f"imu{index}_ax")),
                    "ay": self._number(row.get(f"imu{index}_ay")),
                    "az": self._number(row.get(f"imu{index}_az")),
                    "lax": self._number(row.get(f"imu{index}_lax")),
                    "lay": self._number(row.get(f"imu{index}_lay")),
                    "laz": self._number(row.get(f"imu{index}_laz")),
                }
            )
        emg_valid = self._truthy(row.get("emg_valid"))
        emg = [
            {
                "sensor_id": f"emg{index}",
                "label": f"EMG{index} - {EMG_CHANNEL_SEMANTICS[index]['label']}",
                "source": source,
                "session_id": self._active_session_id,
                "sequence": sequence,
                "received_at_ms": received,
                "age_ms": age_ms,
                "tx_ts_us": None,
                "online": emg_valid and current,
                "valid": emg_valid,
                "rms": self._number(row.get(f"emg_rms_{index}")),
                "mav": self._number(row.get(f"emg_mav_{index}")),
                "peak": self._number(row.get(f"emg_peak_{index}")),
                "latest": self._number(row.get(f"emg_latest_{index}")),
            }
            for index in range(1, 5)
        ]
        return {"imu": imu, "emg": emg}

    def _devices(self, row: dict[str, Any] | None) -> list[dict[str, Any]]:
        updated = (
            _iso_from_ms(int(row["_received_at_ms"])) if row else ""
        )
        stream_stale = self._active_stream_status() == "stale"
        devices = []
        for node in (1, 2):
            node_state = self._node_state(row, node)
            node_status = (
                "stale"
                if stream_stale and node_state["status"] == "normal"
                else node_state["status"]
            )
            devices.append(
                self._device(
                    f"node{node}",
                    f"Node {node}",
                    "sensor_node",
                    node_status == "normal",
                    row.get(f"node{node}_tx_seq") if row else None,
                    row.get(f"node{node}_age_ms") if row else None,
                    updated,
                    row.get(f"node{node}_tx_gap") if row else None,
                    status=node_status,
                    packet_count=node_state["rx"],
                )
            )
        for index in range(1, 5):
            online = bool(
                row and self._truthy(row.get(f"imu{index}_valid"))
            )
            status = (
                "stale"
                if online and stream_stale
                else "normal"
                if online
                else "unknown"
            )
            devices.append(
                self._device(
                    f"imu{index}",
                    f"IMU{index} - {IMU_CHANNEL_SEMANTICS[index]['label']}",
                    "imu",
                    online and not stream_stale,
                    row.get("log_seq") if row else None,
                    row.get(f"imu{index}_age_ms") if row else None,
                    updated,
                    status=status,
                )
            )
        emg_online = bool(row and self._truthy(row.get("emg_valid")))
        for index in range(1, 5):
            status = (
                "stale"
                if emg_online and stream_stale
                else "normal"
                if emg_online
                else "unknown"
            )
            devices.append(
                self._device(
                    f"emg{index}",
                    f"EMG{index} - {EMG_CHANNEL_SEMANTICS[index]['label']}",
                    "semg",
                    emg_online and not stream_stale,
                    row.get("log_seq") if row else None,
                    row.get("node1_age_ms") if row else None,
                    updated,
                    status=status,
                )
            )
        return devices

    @staticmethod
    def _device(
        device_id: str,
        name: str,
        category: str,
        online: bool,
        sequence: Any,
        age_ms: Any,
        updated: str,
        tx_gap: Any = None,
        status: str | None = None,
        packet_count: int | None = None,
    ) -> dict[str, Any]:
        try:
            last_seq = int(sequence)
        except (TypeError, ValueError):
            last_seq = 0
        return {
            "device_id": device_id,
            "name": name,
            "category": category,
            "sensor_type": category,
            "mode": (
                "pc_udp"
                if PC_STREAM_SOURCE_MODE == "udp"
                else "local_csv"
            ),
            "online": online,
            "sample_rate": "",
            "latency_ms": PcLocalRuntimeService._number(age_ms),
            "tx_gap": PcLocalRuntimeService._number(tx_gap),
            "last_seq": last_seq,
            "connection_status": status or ("normal" if online else "unknown"),
            "packet_count": packet_count,
            "updated_at": updated,
        }

    @classmethod
    def _node_state(
        cls, row: dict[str, Any] | None, node: int
    ) -> dict[str, Any]:
        if row is None:
            return {"status": "unknown", "rx": None, "age_ms": None}
        rx = cls._non_negative_int(row.get(f"node{node}_rx"))
        age_ms = cls._number(row.get(f"node{node}_age_ms"))
        if rx is None or age_ms is None or rx <= 0 or age_ms < 0:
            return {"status": "unknown", "rx": rx, "age_ms": age_ms}
        if age_ms > PC_NODE_STALE_MS:
            return {"status": "stale", "rx": rx, "age_ms": age_ms}
        return {"status": "normal", "rx": rx, "age_ms": age_ms}

    def _capture_logs(self, session_id: str, row: dict[str, Any]) -> None:
        samples = self._sensor_samples(row)
        for item in samples["imu"]:
            self._logs[session_id]["imu"].append(deepcopy(item))
        for item in samples["emg"]:
            self._logs[session_id]["emg"].append(deepcopy(item))

    def _recording_duration_seconds(self) -> float:
        if self._recording_started_at_ms is None:
            return 0.0
        end = (
            _now_ms()
            if self._training_state == "RECORDING"
            else self._recording_ended_at_ms or self._recording_started_at_ms
        )
        return round(max(0, end - self._recording_started_at_ms) / 1000, 3)

    def _session_invalid_count(self) -> int:
        current = (
            self._counter_at_stop["invalid"]
            if self._counter_at_stop is not None
            else self._udp.invalid_frame_count
        )
        return max(
            0,
            current - self._counter_at_start["invalid"],
        )

    def _session_duplicate_count(self) -> int:
        current = (
            self._counter_at_stop["duplicate"]
            if self._counter_at_stop is not None
            else self._udp.duplicate_frame_count
        )
        return max(
            0,
            current - self._counter_at_start["duplicate"],
        )

    def _can_start(self) -> bool:
        return (
            self._stream_source_mode == "udp"
            and self._training_state not in BUSY_STATES
            and self._udp.stream_status() == "receiving"
            and self._last_row is not None
            and task_library_service.active_task() is not None
        )

    def _connection_message(self) -> str:
        stats = self._udp.stats()
        if self._csv_stream is not None:
            stats = self._csv_stream.stats()
            if stats["data_stream_status"] == "receiving":
                return "正在播放当前训练真实波形"
            if stats.get("csv_file_status") in {"missing", "not_configured"}:
                return "等待评分服务生成训练 CSV"
            if stats.get("csv_replay_state") == "complete":
                return "训练波形回放完成"
            return "等待当前训练数据"
        if stats["udp_datagram_count"] == 0:
            return "服务已就绪，等待真实设备数据"
        if stats["valid_frame_count"] == 0:
            return "已收到 UDP 数据包，但尚无有效传感器帧"
        if stats["data_stream_status"] == "stale":
            return "真实设备有效数据已中断"
        return "正在接收真实设备数据"

    @staticmethod
    def _quality(score: dict[str, Any] | None) -> dict[str, Any]:
        if not score:
            return {
                "final_score": None,
                "subscores": {},
                "fatigue_level": "",
                "compensation_risk": "",
                "suggestion": "",
            }
        return {
            "final_score": score.get("final_score"),
            "subscores": deepcopy(score.get("subscores") or {}),
            "fatigue_level": json.dumps(
                score.get("fatigue") or {}, ensure_ascii=False
            ),
            "compensation_risk": json.dumps(
                score.get("compensation") or {}, ensure_ascii=False
            ),
            "suggestion": "；".join(
                str(item) for item in score.get("feedback") or []
            ),
        }

    def _today_completed_count(self) -> int:
        today = datetime.now().strftime("%Y%m%d")
        return sum(
            1
            for session in self._sessions.values()
            if str(session.get("session_id", "")).startswith(today)
            and session.get("status") == "COMPLETED"
        )

    def _today_average_score(self) -> float | None:
        today = datetime.now().strftime("%Y%m%d")
        scores = [
            float(session["final_score"])
            for session in self._sessions.values()
            if str(session.get("session_id", "")).startswith(today)
            and session.get("status") == "COMPLETED"
            and isinstance(session.get("final_score"), (int, float))
        ]
        return round(sum(scores) / len(scores), 2) if scores else None


    @staticmethod
    def _read_runtime_json(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _timestamp_ms(value: Any) -> int | None:
        if not value:
            return None
        try:
            text = str(value).replace("Z", "+00:00")
            return int(datetime.fromisoformat(text).timestamp() * 1000)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _job_error_message(
        cls, job_status: dict[str, Any] | None, score_status: str
    ) -> str | None:
        if score_status != "failed":
            return None
        return cls._friendly_score_error(cls._job_raw_error(job_status) or "")

    @staticmethod
    def _job_raw_error(job_status: dict[str, Any] | None) -> str | None:
        if not isinstance(job_status, dict):
            return None
        value = (
            job_status.get("message")
            or job_status.get("error_message")
            or job_status.get("detail")
            or job_status.get("error")
        )
        return str(value) if value else None

    @staticmethod
    def _csv_session_stats(path: Path) -> dict[str, Any]:
        parser = CsvFrameParser()
        valid = 0
        invalid = 0
        duplicate = 0
        start_seq: int | None = None
        end_seq: int | None = None
        start_time: float | None = None
        end_time: float | None = None
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if tuple((value or "").strip() for value in header or []) != CSV_COLUMNS:
                return {
                    "valid_row_count": 0,
                    "invalid_row_count": 0,
                    "duplicate_row_count": 0,
                    "start_log_seq": None,
                    "end_log_seq": None,
                    "start_time_ms": 0.0,
                    "end_time_ms": 0.0,
                }
            for values in reader:
                if not values or all(not value.strip() for value in values):
                    continue
                row, reason, is_duplicate = parser.parse_row(
                    values, "score_runtime_csv", 0
                )
                if is_duplicate:
                    duplicate += 1
                    continue
                if reason or row is None:
                    invalid += 1
                    continue
                seq = int(row["_log_seq_int"])
                time_ms = float(row["_time_ms_float"])
                if start_seq is None:
                    start_seq = seq
                    start_time = time_ms
                end_seq = seq
                end_time = time_ms
                valid += 1
        return {
            "valid_row_count": valid,
            "invalid_row_count": invalid,
            "duplicate_row_count": duplicate,
            "start_log_seq": start_seq,
            "end_log_seq": end_seq,
            "start_time_ms": start_time or 0.0,
            "end_time_ms": end_time or start_time or 0.0,
        }

    async def _refresh_score_runtime_sessions(self) -> bool:
        changed = self._reconcile_score_runtime_sessions()
        self._submit_ready_unscored_sessions()
        if self._configured_stream_source_mode in {"csv_tail", "csv_replay"}:
            selected = self._select_active_runtime_session()
            if selected is not None:
                session_id, csv_path, mode, autoplay = selected
                changed = (
                    await self._activate_runtime_csv(session_id, csv_path, mode, autoplay)
                    or changed
                )
        return changed

    def _submit_ready_unscored_sessions(self) -> None:
        resolver = _score_path_resolver()
        fixed_root = resolver.get_score_root()
        now_ms = _now_ms()
        for session in self._sessions.values():
            session_id = str(session.get("session_id") or "")
            next_retry_at = self._auto_score_next_retry_at_ms.get(session_id)
            if (
                not SESSION_ID_RE.fullmatch(session_id)
                or session_id in self._score_submission_started
                or session_id in self._auto_score_tasks
                or str(session.get("training_status") or "") != "uploaded"
                or str(session.get("score_status") or "") != "unknown"
                or self._auto_score_submission_state.get(session_id) == "failed"
                or (next_retry_at is not None and now_ms < next_retry_at)
            ):
                continue
            csv_file = str(session.get("csv_file") or "")
            if not csv_file:
                continue
            session_dir = resolver.resolve_score_session(session_id).resolve()
            try:
                csv_path = resolver.session_input_file_path(session_id, csv_file).resolve()
            except ValueError:
                continue
            try:
                session_dir.relative_to(fixed_root / "sessions")
                csv_path.relative_to(resolver.session_input_path(session_id).resolve())
            except ValueError:
                continue
            task_path = resolver.session_input_path(session_id) / "task.json"
            if not csv_path.is_file():
                continue
            try:
                task_bytes = task_path.read_bytes()
                task = self._parse_task_contract_bytes(task_bytes)
                if task["csv_file"] != csv_path.name:
                    raise ValueError("task.json csv_file does not match session CSV")
                csv_bytes = csv_path.read_bytes()
            except (OSError, ValueError) as exc:
                self._record_auto_score_failure(session_id, exc, retryable=False)
                continue
            self._auto_score_submission_state[session_id] = "scheduled"
            self._auto_score_tasks[session_id] = asyncio.create_task(
                self._auto_submit_score_job(session_id, task_bytes, csv_bytes),
                name=f"auto-score-{session_id}",
            )

    async def _auto_submit_score_job(
        self,
        session_id: str,
        task_bytes: bytes,
        csv_bytes: bytes,
    ) -> None:
        job_accepted = False

        def mark_job_accepted() -> None:
            nonlocal job_accepted
            job_accepted = True

        try:
            self._auto_score_submission_state[session_id] = "submitting"
            await asyncio.to_thread(
                self._run_score_job,
                session_id,
                task_bytes,
                csv_bytes,
                mark_job_accepted,
            )
            self._score_submission_started.add(session_id)
            self._auto_score_submission_state[session_id] = "completed"
            self._auto_score_last_error.pop(session_id, None)
            self._auto_score_retry_count.pop(session_id, None)
            self._auto_score_next_retry_at_ms.pop(session_id, None)
        except Exception as exc:
            if job_accepted:
                self._score_submission_started.add(session_id)
                self._auto_score_submission_state[session_id] = "submitted"
                self._auto_score_last_error[session_id] = str(exc)
                self._auto_score_next_retry_at_ms[session_id] = None
                self._touch_auto_score_session(session_id)
            else:
                self._record_auto_score_failure(
                    session_id,
                    exc,
                    retryable=self._is_transient_auto_score_error(exc),
                )
        finally:
            self._auto_score_tasks.pop(session_id, None)

    @staticmethod
    def _is_transient_auto_score_error(exc: Exception) -> bool:
        if isinstance(exc, (urllib.error.URLError, TimeoutError)):
            return True
        if isinstance(exc, ScoreJobRequestError):
            if exc.error_code in NON_RETRYABLE_SCORE_ERROR_CODES:
                return False
            return exc.status_code in TRANSIENT_SCORE_HTTP_STATUS
        return False

    def _record_auto_score_failure(
        self,
        session_id: str,
        exc: Exception,
        retryable: bool,
    ) -> None:
        failures = self._auto_score_retry_count.get(session_id, 0) + 1
        self._auto_score_retry_count[session_id] = failures
        self._auto_score_last_error[session_id] = str(exc)
        if retryable and failures <= AUTO_SCORE_MAX_RETRIES:
            delay = AUTO_SCORE_RETRY_DELAYS_SECONDS[
                min(failures - 1, len(AUTO_SCORE_RETRY_DELAYS_SECONDS) - 1)
            ]
            self._auto_score_submission_state[session_id] = "retry_wait"
            self._auto_score_next_retry_at_ms[session_id] = _now_ms() + delay * 1000
        else:
            self._auto_score_submission_state[session_id] = "failed"
            self._auto_score_next_retry_at_ms[session_id] = None
        self._touch_auto_score_session(session_id)

    def _auto_score_diagnostics(self, session_id: str) -> dict[str, Any]:
        return {
            "auto_score_submission_state": self._auto_score_submission_state.get(
                session_id
            ),
            "auto_score_last_error": self._auto_score_last_error.get(session_id),
            "auto_score_retry_count": self._auto_score_retry_count.get(session_id, 0),
            "auto_score_next_retry_at_ms": self._auto_score_next_retry_at_ms.get(
                session_id
            ),
        }

    def _touch_auto_score_session(self, session_id: str) -> None:
        summary = self._sessions.get(session_id)
        if not isinstance(summary, dict):
            return
        summary.update(self._auto_score_diagnostics(session_id))
        if (
            summary.get("score_status") == "unknown"
            and summary.get("auto_score_submission_state") == "failed"
        ):
            summary["status"] = "FAILED"
            summary["training_status"] = "failed"
            summary["scoring_status"] = "failed"
            summary["score_status"] = "failed"
            summary["score_error_code"] = "auto_score_submission_failed"
            summary["score_error_message"] = summary.get("auto_score_last_error")
            summary["error"] = summary.get("auto_score_last_error")
        summary["updated_at"] = datetime.now(timezone.utc).isoformat()
        device_history_store.save_session(summary)

    @staticmethod
    def _csv_activation_state_from_stats(stats: dict[str, Any]) -> str:
        if stats.get("csv_file_status") == "error" or stats.get("csv_replay_state") == "error":
            return "error"
        state = str(stats.get("csv_replay_state") or "")
        if state in {"pending_play", "loading", "playing", "paused", "complete"}:
            return "loading" if state == "pending_play" else state
        if stats.get("csv_replay_ready") is True:
            return "ready"
        if stats.get("stream_source_mode") == "csv_replay":
            return "loading"
        if stats.get("stream_source_mode") == "csv_tail":
            return "ready"
        return "discovering"

    async def _activate_runtime_csv(
        self,
        session_id: str,
        csv_path: Path,
        mode: str,
        autoplay: bool,
    ) -> bool:
        if self._configured_stream_source_mode == "udp":
            self._csv_activation_state = "disabled_in_udp_mode"
            self._csv_activation_error = "runtime_csv_activation_disabled_in_udp_mode"
            return False
        key = (session_id, mode, str(csv_path))
        if self._active_runtime_csv == key:
            self._mark_auto_replay_complete_if_ready()
            return False
        if self._csv_stream is not None:
            await self._csv_stream.stop()
        await self._udp.stop()
        self._stream_source_mode = mode
        self._active_runtime_csv = key
        self._active_session_id = session_id
        self._selected_session_id = session_id
        self._selected_csv_file = csv_path.name
        self._csv_activation_state = "loading" if mode == "csv_replay" else "ready"
        self._csv_activation_error = None
        self._csv_stream = LocalCsvStreamService(
            self._receive_udp_row,
            mode,
            str(csv_path),
            PC_STREAM_CSV_REPLAY_SPEED,
        )
        async with self._lock:
            self._snapshot = self._build_snapshot()
        await self._publish()
        await self._csv_stream.start()
        if (
            autoplay
            and session_id not in self._auto_replay_started_sessions
            and session_id not in self._auto_replay_completed_sessions
        ):
            control = self._csv_stream.control("play", PC_STREAM_CSV_REPLAY_SPEED)
            self._csv_activation_state = self._csv_activation_state_from_stats(control)
            self._auto_replay_started_sessions.add(session_id)
        if mode == "csv_replay":
            await self._csv_stream.wait_until_replay_ready()
            stats = self._csv_stream.stats()
            self._csv_activation_state = self._csv_activation_state_from_stats(stats)
            self._csv_activation_error = stats.get("last_error")
        async with self._lock:
            self._snapshot = self._build_snapshot()
        await self._publish()
        return True

    def _mark_auto_replay_complete_if_ready(
        self, stats: dict[str, Any] | None = None
    ) -> None:
        if (
            self._stream_source_mode != "csv_replay"
            or self._active_session_id == IDLE_SESSION_ID
            or self._active_session_id not in self._auto_replay_started_sessions
            or self._active_session_id in self._auto_replay_completed_sessions
        ):
            return
        current_stats = stats or (
            self._csv_stream.stats() if self._csv_stream is not None else {}
        )
        if current_stats.get("csv_replay_state") == "complete":
            self._auto_replay_completed_sessions.add(self._active_session_id)

    def _select_active_runtime_session(self) -> tuple[str, Path, str, bool] | None:
        fixed_root = _score_path_resolver().get_score_root()
        candidates: list[tuple[int, int, str, Path, str, bool]] = []
        for session in self._sessions.values():
            session_id = str(session.get("session_id") or "")
            csv_file = str(session.get("csv_file") or "")
            if not SESSION_ID_RE.fullmatch(session_id) or not csv_file:
                continue
            try:
                csv_path = _score_path_resolver().session_input_file_path(session_id, csv_file).resolve()
                csv_path.relative_to(fixed_root)
            except ValueError:
                continue
            if not csv_path.is_file():
                continue
            finalized = self._session_task_finalized(session)
            active = not finalized
            priority = 2 if active else 1
            timestamp = int(
                session.get("ended_at_ms")
                or session.get("started_at_ms")
                or csv_path.stat().st_mtime_ns // 1_000_000
            )
            mode = "csv_tail" if active else "csv_replay"
            candidates.append((priority, timestamp, session_id, csv_path, mode, not active))
        if not candidates:
            return None
        _, _, session_id, csv_path, mode, autoplay = max(candidates)
        return session_id, csv_path, mode, autoplay

    @staticmethod
    def _session_task_finalized(session: dict[str, Any]) -> bool:
        if session.get("task_finalized") is True:
            return True
        return bool(session.get("completed_at") and session.get("end_reason"))

    def _reconcile_score_runtime_sessions(self) -> bool:
        resolver = _score_path_resolver()
        sessions_root = resolver.score_sessions_root().resolve()
        try:
            fixed_root = resolver.get_score_root()
            sessions_root.relative_to(fixed_root)
        except (OSError, ValueError):
            return False
        if not sessions_root.is_dir():
            return self._prune_deleted_runtime_sessions(set())
        changed = False
        present_session_ids: set[str] = set()
        for session_dir in sessions_root.iterdir():
            session_id = session_dir.name
            if not session_dir.is_dir() or not SESSION_ID_RE.fullmatch(session_id):
                continue
            try:
                resolved_session_dir = session_dir.resolve()
                resolved_session_dir.relative_to(sessions_root)
            except (OSError, ValueError):
                continue
            present_session_ids.add(session_id)

            cached = self._sessions.get(session_id)
            cached_csv_path = self._cached_runtime_csv_path(
                resolved_session_dir,
                cached,
            )
            if cached_csv_path is not None:
                cached_signature = self._runtime_signature_or_none(
                    resolved_session_dir,
                    cached_csv_path,
                )
                if (
                    cached_signature is not None
                    and self._runtime_signatures.get(session_id) == cached_signature
                ):
                    if self._is_immutable_canonical_completed_session(cached):
                        continue
                    if self._should_refresh_session_v12_summary(cached):
                        if self._reconcile_session_v12_summary(cached):
                            changed = True
                    continue
            input_dir = resolver.session_input_path(session_id).resolve()
            task = self._read_runtime_json(input_dir / "task.json")
            if not isinstance(task, dict):
                continue
            csv_name = str(task.get("csv_file") or "")
            if not self._is_safe_runtime_csv_name(csv_name):
                continue
            csv_path = (input_dir / csv_name).resolve()
            try:
                csv_path.relative_to(input_dir)
            except ValueError:
                continue
            if not csv_path.is_file():
                continue
            signature = self._runtime_signature(resolved_session_dir, csv_path)
            if self._runtime_signatures.get(session_id) == signature:
                cached = self._sessions.get(session_id)
                if cached:
                    cached_job_status = self._read_runtime_json(
                        resolver.job_status_path(session_id)
                    )
                    cached_score_status = self._runtime_score_status(cached_job_status)
                    if self._adopt_score_job_session_summary(
                        cached,
                        resolved_session_dir,
                        cached_score_status,
                    ):
                        changed = True
                        continue
                if cached and self._reconcile_session_v12_summary(cached):
                    changed = True
                continue
            self._runtime_signatures[session_id] = signature
            job_status = self._read_runtime_json(
                resolver.job_status_path(session_id)
            )
            task_finalized = self._task_finalized(task)
            finalized_for_stats = task_finalized or self._job_has_final_state(job_status)
            csv_stats = (
                self._csv_session_stats(csv_path)
                if finalized_for_stats
                else self._csv_quick_stats(csv_path)
            )
            started_ms = self._timestamp_ms(task.get("start_time"))
            ended_ms = self._timestamp_ms(task.get("end_time"))
            duration_seconds = self._runtime_duration_seconds(
                started_ms,
                ended_ms,
                csv_stats,
            )
            score_status = self._runtime_score_status(job_status)
            score_payload = self._runtime_score_payload(resolved_session_dir, score_status)
            final_score = self._score_final_score(score_payload) if score_payload else None
            score_valid = bool(score_payload and score_payload.get("valid") is True)
            report_ready = bool(score_valid and final_score is not None)
            auto_score = self._auto_score_diagnostics(session_id)
            auto_submission_failed = (
                score_status == "unknown"
                and auto_score.get("auto_score_submission_state") == "failed"
            )
            effective_score_status = "failed" if auto_submission_failed else score_status
            training_status = self._runtime_training_status(
                task_finalized,
                effective_score_status,
                score_payload,
            )
            ui_status = self._runtime_ui_status(
                training_status, effective_score_status
            )
            previous = self._sessions.get(session_id) or {}
            now_iso = datetime.now(timezone.utc).isoformat()
            error_message = (
                auto_score.get("auto_score_last_error")
                if auto_submission_failed
                else self._job_error_message(job_status, effective_score_status)
            )
            summary = {
                "patient_id": PATIENT_ID,
                "session_id": session_id,
                "scenario_id": task.get("task_name") or "",
                "task": task.get("task_name") or "",
                "task_id": task.get("task_name") or "",
                "task_name": task.get("task_name") or "",
                "internal_score_task_name": task.get("task_name") or "",
                "status": ui_status,
                "started_at_ms": started_ms,
                "ended_at_ms": ended_ms,
                "started_at": task.get("start_time"),
                "completed_at": task.get("end_time"),
                "duration_ms": int(duration_seconds * 1000),
                "duration_seconds": duration_seconds,
                "rows_written": csv_stats["valid_row_count"],
                "valid_row_count": csv_stats["valid_row_count"],
                "invalid_row_count": csv_stats["invalid_row_count"],
                "duplicate_row_count": csv_stats["duplicate_row_count"],
                "final_score": final_score,
                "report_ready": report_ready,
                "source_mode": "device",
                "source_origin": "pc_udp_6006_ingest",
                "source_ip": None,
                "input_row_count": csv_stats["valid_row_count"],
                "valid_frame_count": csv_stats["valid_row_count"],
                "invalid_frame_count": csv_stats["invalid_row_count"],
                "scoring_status": effective_score_status,
                "score_status": effective_score_status,
                "score_valid": score_valid,
                "log_drop_count": 0,
                "report_path": "report.json" if report_ready else None,
                "score_result_path": "score_result.json" if score_payload else None,
                "score_job_result_path": "runtime/job_state.json",
                "csv_file": csv_name,
                "csv_relative_path": str(csv_path.relative_to(fixed_root)),
                "end_reason": task.get("end_reason"),
                "formal": True,
                "record_kind": "formal",
                "task_finalized": task_finalized,
                "training_status": training_status,
                "score_job_id": session_id,
                "score_error_code": (
                    "auto_score_submission_failed"
                    if auto_submission_failed
                    else "scoring_failed"
                    if effective_score_status == "failed"
                    else None
                ),
                "score_error_message": error_message,
                "created_at": previous.get("created_at") or now_iso,
                "updated_at": now_iso,
                "error": (
                    auto_score.get("auto_score_last_error")
                    if auto_submission_failed
                    else self._job_raw_error(job_status)
                ),
                "start_log_seq": csv_stats["start_log_seq"],
                "end_log_seq": csv_stats["end_log_seq"],
                **auto_score,
            }
            summary.update(resolved_task_fields(task.get("task_name") or ""))
            summary.update(
                {
                    key: value
                    for key, value in legacy_task_fields(summary).items()
                    if not summary.get(key)
                }
            )
            if self._adopt_score_job_session_summary(
                summary,
                resolved_session_dir,
                score_status,
            ):
                changed = True
                continue
            if summary != previous:
                self._sessions[session_id] = summary
                changed = True
            if self._reconcile_session_v12_summary(summary):
                summary = self._sessions.get(session_id) or summary
                changed = True
            if score_payload and score_valid and session_id not in self._reports:
                if self._configured_stream_source_mode == "udp":
                    report = self._build_report_for_context(
                        score_payload,
                        task_name=summary["task_name"],
                        session_id=session_id,
                        training_started_at_ms=started_ms,
                        duration_seconds=duration_seconds,
                    )
                else:
                    self._active_task = {
                        "display_name": summary["task_name"],
                        "task_id": summary["task_id"],
                    }
                    self._active_session_id = session_id
                    self._recording_started_at_ms = started_ms
                    self._recording_ended_at_ms = ended_ms
                    report = self._build_report(score_payload)
                self._reports[session_id] = report
                changed = True
        if self._prune_deleted_runtime_sessions(present_session_ids):
            changed = True
        latest = self._select_latest_session_summary()
        if latest is not None:
            if latest.get("ended_at_ms") is not None:
                self._last_completed_session_id = str(latest["session_id"])
            if self._configured_stream_source_mode != "udp":
                self._active_session_id = str(latest["session_id"])
                self._training_state = str(latest.get("status") or "IDLE")
                self._scoring_status = str(latest.get("score_status") or "unknown")
        return changed

    def _prune_deleted_runtime_sessions(self, present_session_ids: set[str]) -> bool:
        deleted = set(self._sessions) - present_session_ids
        if not deleted:
            return False
        for session_id in deleted:
            self._sessions.pop(session_id, None)
            self._reports.pop(session_id, None)
            self._logs.pop(session_id, None)
            self._runtime_signatures.pop(session_id, None)
            self._auto_replay_started_sessions.discard(session_id)
            self._auto_replay_completed_sessions.discard(session_id)
            self._score_submission_started.discard(session_id)
            self._auto_score_submission_state.pop(session_id, None)
            self._auto_score_last_error.pop(session_id, None)
            self._auto_score_retry_count.pop(session_id, None)
            self._auto_score_next_retry_at_ms.pop(session_id, None)
        if self._last_completed_session_id in deleted:
            self._last_completed_session_id = ""
        return True

    def _adopt_score_job_session_summary(
        self,
        summary: dict[str, Any],
        resolved_session_dir: Path,
        score_status: str,
    ) -> bool:
        session_id = str(summary.get("session_id") or "")
        resolver = _score_path_resolver()
        dedicated_dir = (
            resolver.session_scoring_path(session_id)
            / "intermediate"
            / "dedicated_scoring"
            / session_id
        )
        if (
            score_status != "completed"
            or not session_id
            or not resolver.job_status_path(session_id).is_file()
            or not (resolver.session_scoring_path(session_id) / "full_result.json").is_file()
            or not (dedicated_dir / "session_summary.json").is_file()
        ):
            return False
        result = session_v12_scoring_service.adopt_from_score_runtime(
            summary,
            resolved_session_dir,
        )
        status = session_v12_scoring_service.status(session_id)
        if result.get("accepted") is not True:
            summary.update(
                {
                    "session_v12_status": status.get("status"),
                    "session_v12_error": result.get("reason") or status.get("error"),
                    "session_v12_baseline_version": status.get("baseline_version") or status.get("session_version"),
                    "scoring_status": "failed",
                    "score_status": "failed",
                    "score_error_code": "score_job_artifact_adoption_failed",
                    "score_error_message": result.get("reason") or status.get("error"),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self._sessions[session_id] = summary
            return True

        summary.update(
            {
                "session_v12_status": status.get("status"),
                "session_v12_error": status.get("error"),
                "session_v12_baseline_version": status.get("baseline_version") or status.get("session_version"),
                "session_v12_scoring_authority": "score_service_dedicated_job",
                "session_v12_artifact_origin": "score_service_runtime",
                "source_integrity_status": "verified",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        device_history_store.save_session(summary)
        canonical = self._canonical_session(summary)
        canonical.update(
            {
                "session_v12_scoring_authority": "score_service_dedicated_job",
                "session_v12_artifact_origin": "score_service_runtime",
            }
        )
        self._sessions[session_id] = canonical
        device_history_store.save_session(canonical)
        return True

    def _reconcile_session_v12_summary(self, summary: dict[str, Any]) -> bool:
        session_id = str(summary.get("session_id") or "")
        if not session_id:
            return False
        session_v12_scoring_service.submit_from_runtime_summary(summary)
        session_v12_status = session_v12_scoring_service.status(session_id)
        if session_v12_status.get("status") in {None, "not_started"}:
            return False
        summary_with_session_score = self._canonical_session({
            **summary,
            "session_v12_status": session_v12_status.get("status"),
            "session_v12_error": session_v12_status.get("error"),
            "session_v12_baseline_version": session_v12_status.get("baseline_version"),
        })
        if summary_with_session_score == self._sessions.get(session_id):
            return False
        self._sessions[session_id] = summary_with_session_score
        return True

    @staticmethod
    def _mtime_ns(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    def _runtime_signature(self, session_dir: Path, csv_path: Path) -> tuple[Any, ...]:
        csv_stat = csv_path.stat()
        resolver = _score_path_resolver()
        session_id = session_dir.name
        scoring_dir = resolver.session_scoring_path(session_id)
        return (
            self._mtime_ns(resolver.session_input_path(session_id) / "task.json"),
            self._mtime_ns(resolver.job_status_path(session_id)),
            self._mtime_ns(scoring_dir / "full_result.json"),
            self._mtime_ns(resolver.session_doctor_path(session_id) / "report.json"),
            self._mtime_ns(
                scoring_dir
                / "intermediate"
                / "dedicated_scoring"
                / session_id
                / "session_summary.json"
            ),
            self._mtime_ns(
                scoring_dir
                / "intermediate"
                / "dedicated_scoring"
                / session_id
                / "session_manifest.json"
            ),
            csv_stat.st_size,
            csv_stat.st_mtime_ns,
        )

    def _runtime_signature_or_none(
        self,
        session_dir: Path,
        csv_path: Path,
    ) -> tuple[Any, ...] | None:
        try:
            return self._runtime_signature(session_dir, csv_path)
        except OSError:
            return None

    @staticmethod
    def _is_safe_runtime_csv_name(csv_name: str) -> bool:
        return (
            csv_name.startswith("DATA_SEQ")
            and csv_name.endswith(".csv")
            and "/" not in csv_name
            and "\\" not in csv_name
        )

    def _cached_runtime_csv_path(
        self,
        session_dir: Path,
        cached: dict[str, Any] | None,
    ) -> Path | None:
        if not isinstance(cached, dict):
            return None
        csv_name = str(cached.get("csv_file") or "")
        if not self._is_safe_runtime_csv_name(csv_name):
            return None
        csv_path = (
            _score_path_resolver().session_input_path(session_dir.name) / csv_name
        ).resolve()
        try:
            csv_path.relative_to(
                _score_path_resolver().session_input_path(session_dir.name).resolve()
            )
        except ValueError:
            return None
        return csv_path if csv_path.is_file() else None

    @staticmethod
    def _is_immutable_canonical_completed_session(summary: dict[str, Any] | None) -> bool:
        if not isinstance(summary, dict):
            return False
        return (
            summary.get("session_v12_status") == "completed"
            and summary.get("session_v12_scoring_authority")
            == "score_service_dedicated_job"
            and summary.get("session_v12_artifact_origin") == "score_service_runtime"
        )

    def _should_refresh_session_v12_summary(self, summary: dict[str, Any] | None) -> bool:
        if not isinstance(summary, dict):
            return False
        if self._is_immutable_canonical_completed_session(summary):
            return False
        status = str(summary.get("session_v12_status") or "")
        if status in {"", "not_started", "pending", "running", "failed"}:
            return True
        return summary.get("rescore_allowed") is True

    @classmethod
    def _task_finalized(cls, task: dict[str, Any]) -> bool:
        return bool(task.get("end_reason") and cls._timestamp_ms(task.get("end_time")))

    @staticmethod
    def _job_has_final_state(job_status: dict[str, Any] | None) -> bool:
        if not isinstance(job_status, dict):
            return False
        return str(job_status.get("state") or "").lower() in {
            "succeeded",
            "failed",
        }

    @staticmethod
    def _runtime_score_status(job_status: dict[str, Any] | None) -> str:
        if not isinstance(job_status, dict):
            return "unknown"
        raw = str(
            job_status.get("state")
            or job_status.get("status")
            or job_status.get("score_status")
            or ""
        ).lower()
        if raw in {"succeeded", "success", "completed", "done"}:
            return "completed"
        if raw in {"failed", "error"} or job_status.get("error_code"):
            return "failed"
        if raw in {"running", "processing", "scoring"}:
            return "processing"
        if raw in {"accepted", "queued", "pending"}:
            return "pending"
        if raw == "uploading":
            return "uploading"
        return raw or "unknown"

    @staticmethod
    def _runtime_training_status(
        finalized: bool,
        score_status: str,
        score_payload: dict[str, Any] | None,
    ) -> str:
        if score_status == "failed":
            return "failed"
        if score_status == "completed":
            return (
                "completed"
                if score_payload and score_payload.get("valid") is True
                else "invalid"
            )
        if score_status in {"processing", "pending"}:
            return "scoring"
        if score_status == "uploading":
            return "uploading"
        return "uploaded" if finalized else "collecting"

    @staticmethod
    def _runtime_ui_status(training_status: str, score_status: str) -> str:
        if training_status in {"collecting", "uploading"}:
            return "RECORDING"
        if training_status == "scoring" or score_status in {"pending", "processing"}:
            return "SCORING"
        if training_status == "invalid":
            return "INVALID"
        if training_status == "failed":
            return "FAILED"
        return "COMPLETED"

    @staticmethod
    def _runtime_duration_seconds(
        started_ms: int | None,
        ended_ms: int | None,
        csv_stats: dict[str, Any],
    ) -> float:
        if started_ms is not None and ended_ms is not None:
            return round(max(0, ended_ms - started_ms) / 1000, 3)
        return round(
            max(
                0.0,
                (
                    float(csv_stats["end_time_ms"])
                    - float(csv_stats["start_time_ms"])
                )
                / 1000.0,
            ),
            3,
        )

    def _runtime_score_payload(
        self, session_dir: Path, score_status: str
    ) -> dict[str, Any] | None:
        if score_status != "completed":
            return None
        result_path = (
            _score_path_resolver().session_scoring_path(session_dir.name)
            / "full_result.json"
        ).resolve()
        try:
            result_path.relative_to(session_dir)
        except ValueError:
            return None
        payload = self._read_runtime_json(result_path)
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _csv_quick_stats(path: Path) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader, None)
                valid_header = (
                    tuple((value or "").strip() for value in header or [])
                    == CSV_COLUMNS
                )
        except (OSError, UnicodeError, csv.Error):
            valid_header = False
        return {
            "valid_row_count": 0,
            "invalid_row_count": 0 if valid_header else 1,
            "duplicate_row_count": 0,
            "start_log_seq": None,
            "end_log_seq": None,
            "start_time_ms": 0.0,
            "end_time_ms": 0.0,
        }

    def _select_latest_session_summary(self) -> dict[str, Any] | None:
        if not self._sessions:
            return None
        return max(
            self._sessions.values(),
            key=lambda item: item.get("ended_at_ms")
            or item.get("started_at_ms")
            or 0,
        )

    def _load_history(self) -> None:
        sessions, reports = device_history_store.load()
        self._sessions.update(sessions)
        self._reports.update(reports)
        completed = [
            item
            for item in self._sessions.values()
            if item.get("ended_at_ms") is not None
        ]
        if completed:
            latest = max(
                completed,
                key=lambda item: item.get("ended_at_ms")
                or item.get("started_at_ms")
                or 0,
            )
            self._last_completed_session_id = str(latest["session_id"])
        self._history_loaded = True

    async def _publish(self) -> None:
        await store.set_snapshot(self._snapshot)
        await hub.broadcast(await self.state_message())
        await hub.broadcast(
            await self._message(
                "device.status", {"devices": self._snapshot["devices"]}
            )
        )

    async def _message(
        self, message_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return RealtimeMessage(
            schema_version=SCHEMA_VERSION,
            type=message_type,
            device_id=DEVICE_ID,
            patient_id=PATIENT_ID,
            session_id=self._snapshot["session_id"],
            seq=max(1, await store.next_seq()),
            timestamp_ms=_now_ms(),
            source_mode="pc_local",
            payload=payload,
        ).model_dump()

    async def state_message(self) -> dict[str, Any]:
        return await self._message(
            "rehab.state", {"snapshot": self.snapshot()}
        )

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self._snapshot)

    def sessions(self) -> list[dict[str, Any]]:
        return sorted(
            (
                deepcopy(item)
                for session_id, item in self._sessions.items()
                if device_history_store.session_directory(session_id).is_dir()
            ),
            key=lambda item: item.get("started_at_ms") or 0,
            reverse=True,
        )

    def session(self, session_id: str) -> dict[str, Any] | None:
        try:
            if not device_history_store.session_directory(session_id).is_dir():
                self._prune_deleted_runtime_sessions(set(self._sessions) - {session_id})
                return None
        except ValueError:
            return None
        value = self._sessions.get(session_id)
        return self._canonical_session(value) if value else None

    def report(
        self, session_id: str | None = None
    ) -> dict[str, Any] | None:
        target = (
            session_id
            or (
                self._active_session_id
                if self._active_session_id in self._reports
                else self._last_completed_session_id
            )
        )
        if not target:
            return None
        try:
            if not device_history_store.session_directory(target).is_dir():
                self._prune_deleted_runtime_sessions(set(self._sessions) - {target})
                return None
        except ValueError:
            return None
        value = self._reports.get(target)
        session = self._sessions.get(target)
        report_resolver = getattr(session_v12_scoring_service, "canonical_report", None)
        canonical = report_resolver(target, value, session) if callable(report_resolver) else None
        return canonical if canonical is not None else deepcopy(value) if value else None

    @staticmethod
    def _canonical_session(session: dict[str, Any]) -> dict[str, Any]:
        resolver = getattr(session_v12_scoring_service, "canonical_session_summary", None)
        return resolver(session) if callable(resolver) else deepcopy(session)

    def logs(
        self, session_id: str, log_type: str, limit: int, offset: int
    ) -> dict[str, Any]:
        rows = list(self._logs[session_id][log_type])
        return {
            "items": deepcopy(rows[offset : offset + limit]),
            "total": len(rows),
            "limit": limit,
            "offset": offset,
        }

    def patients(self) -> list[dict[str, Any]]:
        return [self.patient(PATIENT_ID)]

    def patient(self, patient_id: str) -> dict[str, Any]:
        if patient_id != PATIENT_ID:
            raise KeyError(patient_id)
        return {
            "patient_id": PATIENT_ID,
            "display_name": "当前患者",
            "profile_label": "当前患者",
            "diagnosis_stage": "",
            "affected_side": "",
            "brs": "",
            "age_band": "",
            "risk_level": "",
        }

    @staticmethod
    def _truthy(value: Any) -> bool:
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _non_negative_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            text = str(value).strip()
            if not text:
                return None
            number = int(text)
        except (TypeError, ValueError):
            return None
        return number if number >= 0 else None

    @staticmethod
    def _number(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None


pc_local_runtime_service = PcLocalRuntimeService()
