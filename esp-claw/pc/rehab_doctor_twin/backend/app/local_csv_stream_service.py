from __future__ import annotations

import asyncio
import csv
import io
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import PC_STREAM_STALE_SECONDS
from .pc_udp_ingest_service import CSV_COLUMNS, CsvFrameParser

FrameCallback = Callable[
    [dict[str, Any] | None, tuple[str, int] | None],
    Awaitable[None],
]


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalCsvStreamService:
    def __init__(
        self,
        callback: FrameCallback,
        mode: str,
        csv_path: str,
        replay_speed: float,
    ) -> None:
        self._callback = callback
        self._mode = mode
        self._path = Path(csv_path) if csv_path else None
        self._replay_speed = replay_speed if replay_speed in {0.5, 1.0, 2.0} else 1.0
        self._task: asyncio.Task | None = None
        self._parser = CsvFrameParser()
        self._recent_valid: list[float] = []
        self._last_valid_monotonic: float | None = None
        self._last_file_check_monotonic: float | None = None
        self._file_offset = 0
        self._tail_buffer = ""
        self._replay_rows: list[dict[str, Any]] = []
        self._replay_index = 0
        self._replay_state = "idle"
        self._replay_paused = True
        self._replay_ready = False
        self._replay_ready_event = asyncio.Event()
        self._play_requested = False
        self._replay_generation = 0
        self._replay_run_reset_done = False
        self._replay_total_rows = 0
        self._last_stat_signature: tuple[int, int] | None = None
        self._last_file_identity: tuple[int, int] | None = None
        self.csv_valid_frame_count = 0
        self.csv_invalid_frame_count = 0
        self.csv_duplicate_frame_count = 0
        self.csv_last_column_count: int | None = None
        self.csv_last_parse_error: str | None = None
        self.csv_last_log_seq: int | None = None
        self.csv_last_time_ms: float | None = None
        self.csv_last_received_at: str | None = None
        self.csv_current_row = 0
        self.csv_total_rows: int | None = None
        self.csv_rows_read = 0
        self.csv_file_size: int | None = None
        self.csv_file_status = "not_configured" if not csv_path else "waiting"
        self.csv_last_new_data_at_ms: int | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name=f"pc-{self._mode}")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def wait_until_replay_ready(self, timeout_seconds: float = 2.0) -> bool:
        if self._mode != "csv_replay":
            return True
        if self._replay_ready or self._replay_state == "error":
            return self._replay_ready
        try:
            await asyncio.wait_for(self._replay_ready_event.wait(), timeout_seconds)
        except asyncio.TimeoutError:
            return False
        return self._replay_ready

    def stats(self) -> dict[str, Any]:
        now = time.monotonic()
        self._recent_valid = [
            item for item in self._recent_valid if now - item <= 2.0
        ]
        valid_age_ms = (
            int((now - self._last_valid_monotonic) * 1000)
            if self._last_valid_monotonic is not None
            else None
        )
        return {
            "stream_source_mode": self._mode,
            "stream_source_label": self.source_label(),
            "data_stream_status": self.stream_status(),
            "udp_stream_status": "waiting",
            "parse_status": self.parse_status(valid_age_ms),
            "has_current_valid_frame": self.stream_status() == "receiving",
            "receive_rate_hz": round(len(self._recent_valid) / 2.0, 1),
            "valid_frame_rate_hz": round(len(self._recent_valid) / 2.0, 1),
            "udp_datagram_rate_hz": 0.0,
            "udp_datagram_count": 0,
            "udp_bytes_total": 0,
            "udp_last_source_ip": None,
            "udp_last_source_port": None,
            "udp_last_received_at_ms": None,
            "udp_last_age_ms": None,
            "valid_frame_count": self.csv_valid_frame_count,
            "invalid_frame_count": self.csv_invalid_frame_count,
            "duplicate_frame_count": self.csv_duplicate_frame_count,
            "last_source_ip": self.source_origin(),
            "last_source_port": 0,
            "last_log_seq": self.csv_last_log_seq,
            "last_time_ms": self.csv_last_time_ms,
            "last_received_at": self.csv_last_received_at,
            "last_valid_frame_at_ms": self.csv_last_new_data_at_ms,
            "last_valid_frame_age_ms": valid_age_ms,
            "last_column_count": self.csv_last_column_count,
            "last_error": self.csv_last_parse_error,
            "last_parse_error": self.csv_last_parse_error,
            "last_invalid_frame_at_ms": None,
            "last_duplicate_frame_at_ms": None,
            "csv_valid_frame_count": self.csv_valid_frame_count,
            "csv_invalid_frame_count": self.csv_invalid_frame_count,
            "csv_duplicate_frame_count": self.csv_duplicate_frame_count,
            "csv_last_column_count": self.csv_last_column_count,
            "csv_last_parse_error": self.csv_last_parse_error,
            "csv_current_row": self.csv_current_row,
            "csv_total_rows": self.csv_total_rows,
            "csv_last_log_seq": self.csv_last_log_seq,
            "csv_last_time_ms": self.csv_last_time_ms,
            "csv_file_name": self.file_name(),
            "csv_file_size": self.csv_file_size,
            "csv_read_offset": self._file_offset,
            "csv_rows_read": self.csv_rows_read,
            "csv_last_new_data_at_ms": self.csv_last_new_data_at_ms,
            "csv_file_status": self.csv_file_status,
            "csv_replay_state": self._replay_state,
            "csv_replay_ready": self._replay_ready,
            "csv_play_requested": self._play_requested,
            "csv_replay_generation": self._replay_generation,
            "csv_replay_speed": self._replay_speed,
        }

    def stream_status(self) -> str:
        if self._last_valid_monotonic is None:
            return "waiting"
        if time.monotonic() - self._last_valid_monotonic > PC_STREAM_STALE_SECONDS:
            return "stale"
        return "receiving"

    def parse_status(self, valid_age_ms: int | None = None) -> str:
        stale_ms = int(PC_STREAM_STALE_SECONDS * 1000)
        if self.csv_valid_frame_count == 0:
            return "invalid" if self.csv_invalid_frame_count else "waiting"
        if valid_age_ms is not None and valid_age_ms <= stale_ms:
            return "normal"
        if self.csv_invalid_frame_count > 0 and self.stream_status() == "stale":
            return "invalid_active"
        return "stale"

    def control(self, action: str, speed: float | None = None) -> dict[str, Any]:
        if speed in {0.5, 1.0, 2.0}:
            self._replay_speed = speed
        if self._mode != "csv_replay":
            return {"accepted": False, "reason": "not_csv_replay", **self.stats()}
        if action == "play":
            if (
                self._replay_state in {"idle", "complete", "error"}
                or (self._replay_generation == 0 and self.csv_current_row == 0)
            ):
                self._reset_replay_run_state()
            self._play_requested = True
            if self._replay_ready:
                self._replay_paused = False
                self._replay_state = "playing"
            else:
                self._replay_paused = True
                self._replay_state = "pending_play"
        elif action == "pause":
            self._play_requested = False
            self._replay_paused = True
            self._replay_state = "paused"
        elif action == "resume":
            self._play_requested = True
            if self._replay_ready:
                self._replay_paused = False
                self._replay_state = "playing"
            else:
                self._replay_paused = True
                self._replay_state = "pending_play"
        elif action == "restart":
            self._reset_replay_run_state()
            self._play_requested = True
            if self._replay_ready:
                self._replay_paused = False
                self._replay_state = "playing"
            else:
                self._replay_paused = True
                self._replay_state = "pending_play"
        else:
            return {"accepted": False, "reason": "unsupported_action", **self.stats()}
        return {"accepted": True, **self.stats()}

    def file_name(self) -> str:
        return self._path.name if self._path else ""

    def source_origin(self) -> str:
        return "local_csv_tail" if self._mode == "csv_tail" else "local_csv_replay"

    def source_label(self) -> str:
        if self._mode == "csv_tail":
            return "local_csv_tail"
        if self._mode == "csv_replay":
            return "local_csv_replay"
        return "udp"

    async def _run(self) -> None:
        try:
            if self._mode == "csv_tail":
                await self._run_tail()
            elif self._mode == "csv_replay":
                await self._run_replay()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.csv_file_status = "error"
            self.csv_last_parse_error = f"{self._mode}_runtime_error:{type(exc).__name__}"
            if self._mode == "csv_replay":
                self._replay_state = "error"
                self._replay_ready = False
                self._play_requested = False
                self._replay_ready_event.set()
            self._notify(None, None)

    async def _run_tail(self) -> None:
        while True:
            try:
                await self._poll_tail_once()
            except (OSError, UnicodeError, csv.Error) as exc:
                self._invalid(f"csv_tail_error:{type(exc).__name__}")
                self.csv_file_status = "error"
            await asyncio.sleep(0.1)

    async def _poll_tail_once(self) -> None:
        if self._path is None:
            self.csv_file_status = "not_configured"
            return
        path = self._path
        if not path.is_file():
            self.csv_file_status = "missing"
            self._file_offset = 0
            self._tail_buffer = ""
            self._parser.reset()
            return
        stat = path.stat()
        self.csv_file_size = stat.st_size
        signature = (stat.st_size, int(stat.st_mtime_ns))
        identity = (int(stat.st_dev), int(stat.st_ino))
        replaced = (
            self._last_file_identity is not None
            and identity != self._last_file_identity
        )
        if self._file_offset == 0 or stat.st_size < self._file_offset or replaced:
            self._parser.reset()
            self._tail_buffer = ""
            self._validate_header(path)
            self._file_offset = self._header_offset(path)
            self._last_stat_signature = signature
            self._last_file_identity = identity
            self.csv_file_status = "waiting_new_rows"
            return
        if signature == self._last_stat_signature:
            self.csv_file_status = (
                "stale" if self.stream_status() == "stale" else "waiting_new_rows"
            )
            return
        self._last_stat_signature = signature
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            handle.seek(self._file_offset)
            chunk = handle.read()
            self._file_offset = handle.tell()
        if not chunk:
            self.csv_file_status = "waiting_new_rows"
            return
        text = self._tail_buffer + chunk
        complete, self._tail_buffer = self._split_complete_lines(text)
        if not complete:
            self.csv_file_status = "waiting_new_rows"
            return
        self._accept_csv_text(complete, "local_csv_tail", 0)

    async def _run_replay(self) -> None:
        try:
            self._replay_ready = False
            self._replay_ready_event.clear()
            if self._play_requested:
                self._replay_state = "pending_play"
            else:
                self._replay_state = "loading"
            self._replay_rows = self._load_replay_rows()
            self.csv_total_rows = len(self._replay_rows)
            self._replay_total_rows = len(self._replay_rows)
            self._replay_ready = True
            if self._play_requested:
                if not self._replay_run_reset_done:
                    self._reset_replay_run_state()
                self._replay_state = "playing"
                self._replay_paused = False
            else:
                self._replay_state = "paused"
                self._replay_paused = True
            self.csv_file_status = "ready"
            self._replay_ready_event.set()
            self._notify(None, None)
        except (OSError, UnicodeError, csv.Error, ValueError) as exc:
            self.csv_file_status = "error"
            self.csv_last_parse_error = f"csv_replay_load_error:{type(exc).__name__}"
            self._replay_state = "error"
            self._replay_ready = False
            self._play_requested = False
            self._replay_ready_event.set()
            self._notify(None, None)
        while True:
            await asyncio.sleep(0.02)
            if self._replay_state != "playing" or self._replay_paused:
                continue
            if self._replay_index >= len(self._replay_rows):
                self._replay_state = "complete"
                self._replay_paused = True
                self._play_requested = False
                self.csv_file_status = "replay_complete"
                self._notify(None, None)
                continue
            row = deepcopy(self._replay_rows[self._replay_index])
            row["_received_at_ms"] = int(time.time() * 1000)
            row["_source_ip"] = "local_csv_replay"
            row["_source_port"] = 0
            self._accept_parsed_row(row)
            self._replay_index += 1
            self.csv_current_row = self._replay_index
            if self._replay_index < len(self._replay_rows):
                previous_time = float(row["_time_ms_float"])
                next_time = float(self._replay_rows[self._replay_index]["_time_ms_float"])
                gap = max(0.0, min(1.0, (next_time - previous_time) / 1000.0))
                await asyncio.sleep(gap / self._replay_speed)

    def _load_replay_rows(self) -> list[dict[str, Any]]:
        if self._path is None:
            raise ValueError("csv_path_not_configured")
        path = self._path
        if not path.is_file():
            raise FileNotFoundError(str(path))
        self.csv_file_size = path.stat().st_size
        rows: list[dict[str, Any]] = []
        self._parser.reset()
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if tuple((value or "").strip() for value in header or []) != CSV_COLUMNS:
                raise ValueError("csv_header_contract_invalid")
            for values in reader:
                if not values or all(not value.strip() for value in values):
                    continue
                self.csv_last_column_count = len(values)
                row, reason, duplicate = self._parser.parse_row(
                    values, "local_csv_replay", 0
                )
                if duplicate:
                    self.csv_duplicate_frame_count += 1
                    continue
                if reason or row is None:
                    self._invalid(reason or "row_parse_error", notify=False)
                    continue
                rows.append(row)
        self._parser.reset()
        return rows

    def _reset_replay_run_state(self) -> None:
        self._replay_generation += 1
        self._replay_run_reset_done = True
        self._replay_index = 0
        self.csv_current_row = 0
        self.csv_valid_frame_count = 0
        self.csv_invalid_frame_count = 0
        self.csv_duplicate_frame_count = 0
        self.csv_rows_read = 0
        self.csv_last_log_seq = None
        self.csv_last_time_ms = None
        self.csv_last_new_data_at_ms = None
        self.csv_last_received_at = None
        self.csv_last_parse_error = None
        self._last_valid_monotonic = None
        self._recent_valid.clear()
        self._parser.reset()

    def _validate_header(self, path: Path) -> None:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if tuple((value or "").strip() for value in header or []) != CSV_COLUMNS:
                raise ValueError("csv_header_contract_invalid")

    @staticmethod
    def _header_offset(path: Path) -> int:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            handle.readline()
            return handle.tell()

    @staticmethod
    def _split_complete_lines(text: str) -> tuple[str, str]:
        if not text:
            return "", ""
        last_newline = max(text.rfind("\n"), text.rfind("\r"))
        if last_newline < 0:
            return "", text
        return text[: last_newline + 1], text[last_newline + 1 :]

    def _accept_csv_text(self, text: str, source_id: str, source_port: int) -> None:
        saw_valid = False
        reader = csv.reader(io.StringIO(text, newline=""))
        for values in reader:
            if not values or all(not value.strip() for value in values):
                continue
            if tuple(value.strip() for value in values) == CSV_COLUMNS:
                continue
            self.csv_last_column_count = len(values)
            row, reason, duplicate = self._parser.parse_row(
                values, source_id, source_port
            )
            if duplicate:
                self.csv_duplicate_frame_count += 1
                continue
            if reason or row is None:
                self._invalid(reason or "row_parse_error")
                continue
            saw_valid = True
            self.csv_rows_read += 1
            self.csv_current_row = self.csv_rows_read
            self._accept_parsed_row(row)
        self.csv_file_status = "reading" if saw_valid else "parse_failed"

    def _accept_parsed_row(self, row: dict[str, Any]) -> None:
        now = time.monotonic()
        self._last_valid_monotonic = now
        self._recent_valid.append(now)
        self.csv_valid_frame_count += 1
        self.csv_last_log_seq = int(row["_log_seq_int"])
        self.csv_last_time_ms = float(row["_time_ms_float"])
        self.csv_last_new_data_at_ms = int(row["_received_at_ms"])
        self.csv_last_received_at = _iso_now()
        self.csv_last_parse_error = None
        row["_source_mode"] = self.source_origin()
        row["_source_origin"] = self.source_origin()
        self._notify(row, (self.source_origin(), 0))

    def _invalid(self, reason: str, notify: bool = True) -> None:
        self.csv_invalid_frame_count += 1
        self.csv_last_parse_error = reason
        if notify:
            self._notify(None, None)

    def _notify(
        self, row: dict[str, Any] | None, addr: tuple[str, int] | None
    ) -> None:
        asyncio.get_running_loop().create_task(self._callback(row, addr))
