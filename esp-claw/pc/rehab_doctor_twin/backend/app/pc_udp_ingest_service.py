from __future__ import annotations

import asyncio
import csv
import io
import math
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .config import (
    PC_STREAM_STALE_SECONDS,
    PC_STREAM_UDP_HOST,
    PC_STREAM_UDP_PORT,
)

CSV_COLUMNS = tuple(
    (
        "log_seq,time_ms,output_rate_hz,flags,"
        "node1_rx,node1_tx_seq,node1_tx_gap,node1_age_ms,"
        "node2_rx,node2_tx_seq,node2_tx_gap,node2_age_ms,"
        "emg_valid,emg_latest_1,emg_latest_2,emg_latest_3,emg_latest_4,"
        "emg_rms_1,emg_rms_2,emg_rms_3,emg_rms_4,"
        "emg_mav_1,emg_mav_2,emg_mav_3,emg_mav_4,"
        "emg_peak_1,emg_peak_2,emg_peak_3,emg_peak_4,emg_n,"
        "imu1_valid,imu1_age_ms,imu1_roll,imu1_pitch,imu1_yaw,"
        "imu1_gx,imu1_gy,imu1_gz,imu1_ax,imu1_ay,imu1_az,"
        "imu1_lax,imu1_lay,imu1_laz,"
        "imu2_valid,imu2_age_ms,imu2_roll,imu2_pitch,imu2_yaw,"
        "imu2_gx,imu2_gy,imu2_gz,imu2_ax,imu2_ay,imu2_az,"
        "imu2_lax,imu2_lay,imu2_laz,"
        "imu3_valid,imu3_age_ms,imu3_roll,imu3_pitch,imu3_yaw,"
        "imu3_gx,imu3_gy,imu3_gz,imu3_ax,imu3_ay,imu3_az,"
        "imu3_lax,imu3_lay,imu3_laz,"
        "imu4_valid,imu4_age_ms,imu4_roll,imu4_pitch,imu4_yaw,"
        "imu4_gx,imu4_gy,imu4_gz,imu4_ax,imu4_ay,imu4_az,"
        "imu4_lax,imu4_lay,imu4_laz"
    ).split(",")
)

SIGNAL_COLUMNS = tuple(
    column
    for column in CSV_COLUMNS
    if column.startswith("emg_latest_")
    or column.startswith("emg_rms_")
    or column.startswith("emg_mav_")
    or column.startswith("emg_peak_")
    or any(
        column.startswith(f"imu{index}_{field}")
        for index in range(1, 5)
        for field in (
            "roll",
            "pitch",
            "yaw",
            "gx",
            "gy",
            "gz",
            "ax",
            "ay",
            "az",
            "lax",
            "lay",
            "laz",
        )
    )
)

FrameCallback = Callable[
    [dict[str, Any] | None, tuple[str, int] | None],
    Awaitable[None],
]


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CsvFrameParser:
    def __init__(self, stale_seconds: float = PC_STREAM_STALE_SECONDS) -> None:
        self._stale_seconds = stale_seconds
        self._seen_order: deque[tuple[str, int, str]] = deque(maxlen=4096)
        self._seen: set[tuple[str, int, str]] = set()
        self._source_progress: dict[str, tuple[int, float, float]] = {}

    def reset(self) -> None:
        self._seen_order.clear()
        self._seen.clear()
        self._source_progress.clear()

    def parse_row(
        self,
        values: list[str],
        source_id: str,
        source_port: int = 0,
    ) -> tuple[dict[str, Any] | None, str | None, bool]:
        if len(values) != len(CSV_COLUMNS):
            return None, f"unexpected_column_count:{len(values)}", False
        raw_values = tuple(values)
        row = dict(zip(CSV_COLUMNS, raw_values))
        try:
            sequence = int(row["log_seq"])
        except ValueError:
            return None, "invalid_log_seq", False
        try:
            timestamp = float(row["time_ms"])
        except ValueError:
            return None, "invalid_time_ms", False
        if not 0 <= sequence <= 9_999_999_999:
            return None, "invalid_log_seq", False
        if not math.isfinite(timestamp) or timestamp < 0:
            return None, "invalid_time_ms", False
        valid_sensor = _truthy(row["emg_valid"]) or any(
            _truthy(row[f"imu{index}_valid"]) for index in range(1, 5)
        )
        if not valid_sensor:
            return None, "no_valid_sensor", False
        signal_values: list[float] = []
        for column in SIGNAL_COLUMNS:
            value = row[column].strip()
            if not value:
                continue
            try:
                number = float(value)
            except ValueError:
                return None, f"invalid_number:{column}", False
            if not math.isfinite(number):
                return None, "non_finite_signal", False
            signal_values.append(number)
        if not signal_values or not any(value != 0 for value in signal_values):
            return None, "all_required_signals_zero", False

        key = (source_id, sequence, row["time_ms"])
        if key in self._seen:
            return None, None, True
        now = time.monotonic()
        previous = self._source_progress.get(source_id)
        source_stale = (
            previous is not None and now - previous[2] > self._stale_seconds
        )
        if previous is not None and not source_stale:
            if sequence < previous[0] or timestamp < previous[1]:
                return None, "out_of_order_frame", False

        received_at_ms = int(time.time() * 1000)
        if len(self._seen_order) == self._seen_order.maxlen:
            self._seen.discard(self._seen_order[0])
        self._seen_order.append(key)
        self._seen.add(key)
        self._source_progress[source_id] = (sequence, timestamp, now)
        row["_log_seq_int"] = sequence
        row["_time_ms_float"] = timestamp
        row["_received_at_ms"] = received_at_ms
        row["_source_ip"] = source_id
        row["_source_port"] = source_port
        row["_raw_values"] = raw_values
        return row, None, False


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, owner: "PcUdpIngestService") -> None:
        self._owner = owner

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._owner.accept_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        self._owner.record_transport_error(exc)


class PcUdpIngestService:
    def __init__(self, callback: FrameCallback) -> None:
        self._callback = callback
        self._transport: asyncio.DatagramTransport | None = None
        self._last_valid_monotonic: float | None = None
        self._parser = CsvFrameParser()
        self._recent_datagrams: deque[float] = deque()
        self._recent_valid: deque[float] = deque()
        self.udp_datagram_count = 0
        self.udp_bytes_total = 0
        self.udp_last_source_ip: str | None = None
        self.udp_last_source_port: int | None = None
        self.udp_last_received_at_ms: int | None = None
        self.udp_last_monotonic: float | None = None
        self.valid_frame_count = 0
        self.invalid_frame_count = 0
        self.duplicate_frame_count = 0
        self.last_source_ip: str | None = None
        self.last_source_port: int | None = None
        self.last_log_seq: int | None = None
        self.last_time_ms: float | None = None
        self.last_received_at: str | None = None
        self.last_valid_frame_at_ms: int | None = None
        self.last_valid_monotonic: float | None = None
        self.last_invalid_frame_at_ms: int | None = None
        self.last_duplicate_frame_at_ms: int | None = None
        self.last_column_count: int | None = None
        self.last_error: str | None = None
        self.last_parse_error: str | None = None

    async def start(self) -> None:
        if self._transport is not None:
            return
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _UdpProtocol(self),
            local_addr=(PC_STREAM_UDP_HOST, PC_STREAM_UDP_PORT),
        )
        self._transport = transport

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
            await asyncio.sleep(0)

    def accept_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        self._record_datagram(data, addr)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            self._invalid("decode_error")
            return
        saw_payload_row = False
        try:
            rows = csv.reader(io.StringIO(text, newline=""))
            for values in rows:
                if not values or all(not value.strip() for value in values):
                    continue
                if tuple(value.strip() for value in values) == CSV_COLUMNS:
                    continue
                saw_payload_row = True
                self._accept_row(values, addr)
        except csv.Error:
            self._invalid("csv_parse_error")
            return
        except ValueError as exc:
            self._invalid(str(exc) or "row_parse_error")
            return
        if not saw_payload_row:
            self._invalid("empty_datagram" if not text.strip() else "no_data_rows")

    def record_transport_error(self, exc: Exception) -> None:
        self.last_error = str(exc)
        self._notify(None, None)

    def stream_status(self) -> str:
        if self._last_valid_monotonic is None:
            return "waiting"
        if time.monotonic() - self._last_valid_monotonic > PC_STREAM_STALE_SECONDS:
            return "stale"
        return "receiving"

    def udp_stream_status(self) -> str:
        if self.udp_last_monotonic is None:
            return "waiting"
        if time.monotonic() - self.udp_last_monotonic > PC_STREAM_STALE_SECONDS:
            return "stale"
        return "receiving"

    def stats(self) -> dict[str, Any]:
        now = time.monotonic()
        while self._recent_datagrams and now - self._recent_datagrams[0] > 2.0:
            self._recent_datagrams.popleft()
        while self._recent_valid and now - self._recent_valid[0] > 2.0:
            self._recent_valid.popleft()
        datagram_rate = len(self._recent_datagrams) / 2.0
        valid_rate = len(self._recent_valid) / 2.0
        udp_age_ms = (
            int((now - self.udp_last_monotonic) * 1000)
            if self.udp_last_monotonic is not None
            else None
        )
        valid_age_ms = (
            int((now - self.last_valid_monotonic) * 1000)
            if self.last_valid_monotonic is not None
            else None
        )
        return {
            "data_stream_status": self.stream_status(),
            "udp_stream_status": self.udp_stream_status(),
            "parse_status": self.parse_status(udp_age_ms, valid_age_ms),
            "has_current_valid_frame": self.stream_status() == "receiving",
            "receive_rate_hz": round(valid_rate, 1),
            "valid_frame_rate_hz": round(valid_rate, 1),
            "udp_datagram_rate_hz": round(datagram_rate, 1),
            "udp_datagram_count": self.udp_datagram_count,
            "udp_bytes_total": self.udp_bytes_total,
            "udp_last_source_ip": self.udp_last_source_ip,
            "udp_last_source_port": self.udp_last_source_port,
            "udp_last_received_at_ms": self.udp_last_received_at_ms,
            "udp_last_age_ms": udp_age_ms,
            "valid_frame_count": self.valid_frame_count,
            "invalid_frame_count": self.invalid_frame_count,
            "duplicate_frame_count": self.duplicate_frame_count,
            "last_source_ip": self.udp_last_source_ip,
            "last_source_port": self.udp_last_source_port,
            "last_log_seq": self.last_log_seq,
            "last_time_ms": self.last_time_ms,
            "last_received_at": self.last_received_at,
            "last_valid_frame_at_ms": self.last_valid_frame_at_ms,
            "last_valid_frame_age_ms": valid_age_ms,
            "last_column_count": self.last_column_count,
            "last_error": self.last_error,
            "last_parse_error": self.last_parse_error,
            "last_invalid_frame_at_ms": self.last_invalid_frame_at_ms,
            "last_duplicate_frame_at_ms": self.last_duplicate_frame_at_ms,
            "listen_host": PC_STREAM_UDP_HOST,
            "listen_port": PC_STREAM_UDP_PORT,
        }

    def parse_status(
        self,
        udp_age_ms: int | None = None,
        valid_age_ms: int | None = None,
    ) -> str:
        now = time.monotonic()
        if udp_age_ms is None and self.udp_last_monotonic is not None:
            udp_age_ms = int((now - self.udp_last_monotonic) * 1000)
        if valid_age_ms is None and self.last_valid_monotonic is not None:
            valid_age_ms = int((now - self.last_valid_monotonic) * 1000)
        stale_ms = int(PC_STREAM_STALE_SECONDS * 1000)
        if self.udp_datagram_count == 0:
            return "waiting"
        if self.valid_frame_count == 0:
            return "invalid" if (udp_age_ms is None or udp_age_ms <= stale_ms) else "stale"
        if valid_age_ms is not None and valid_age_ms <= stale_ms:
            return "normal"
        if udp_age_ms is not None and udp_age_ms <= stale_ms:
            return "invalid_active"
        return "stale"

    def _accept_row(self, values: list[str], addr: tuple[str, int]) -> None:
        self.last_column_count = len(values)
        row, reason, duplicate = self._parser.parse_row(values, addr[0], addr[1])
        if duplicate:
            self.duplicate_frame_count += 1
            self.last_duplicate_frame_at_ms = int(time.time() * 1000)
            self._notify(None, addr)
            return
        if reason or row is None:
            self._invalid(reason or "row_parse_error")
            return
        now = time.monotonic()
        self._last_valid_monotonic = now
        self.last_valid_monotonic = now
        self._recent_valid.append(now)
        self.valid_frame_count += 1
        self.last_source_ip = addr[0]
        self.last_source_port = addr[1]
        self.last_log_seq = row["_log_seq_int"]
        self.last_time_ms = row["_time_ms_float"]
        self.last_received_at = _iso_now()
        self.last_valid_frame_at_ms = row["_received_at_ms"]
        self.last_parse_error = None
        self.last_error = None
        self._notify(row, addr)

    def _record_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        now = time.monotonic()
        self.udp_datagram_count += 1
        self.udp_bytes_total += len(data)
        self.udp_last_source_ip = addr[0]
        self.udp_last_source_port = addr[1]
        self.udp_last_received_at_ms = int(time.time() * 1000)
        self.udp_last_monotonic = now
        self._recent_datagrams.append(now)

    def _invalid(self, reason: str) -> None:
        self.invalid_frame_count += 1
        self.last_invalid_frame_at_ms = int(time.time() * 1000)
        self.last_parse_error = reason
        self.last_error = reason
        self._notify(None, None)

    def _notify(
        self, row: dict[str, Any] | None, addr: tuple[str, int] | None
    ) -> None:
        asyncio.get_running_loop().create_task(self._callback(row, addr))
