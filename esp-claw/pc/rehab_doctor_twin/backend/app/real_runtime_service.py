from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .config import (
    DEVICE_ID,
    P4_BASE_URL,
    P4_DEVICE_OFFLINE_TIMEOUT_SECONDS,
    P4_RUNTIME_POLL_INTERVAL_SECONDS,
    PATIENT_ID,
    SCHEMA_VERSION,
    SCORE_BASE_URL,
)
from .device_history_store import device_history_store
from .models import RealtimeMessage
from .session_store import store
from .websocket_hub import hub

MAX_LOG_ROWS = 500
IDLE_SESSION_ID = "SESSION_IDLE"
SCORE_POLL_INTERVAL_SECONDS = 1.0


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_from_age_ms(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return ""
    return datetime.fromtimestamp(
        max(0, _now_ms() - int(value)) / 1000,
        timezone.utc,
    ).isoformat()


def _request_json(url: str, timeout: float = 1.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            parsed = json.loads(body)
            return parsed if isinstance(parsed, dict) else {}
    except (OSError, UnicodeError, ValueError, urllib.error.URLError):
        return {}


def _score_body(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    return result if isinstance(result, dict) else payload


def _score_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not payload:
        return None
    body = _score_body(payload)
    final_score = body.get("final_score", body.get("average_score"))
    if isinstance(final_score, bool) or not isinstance(final_score, (int, float)):
        return None
    subscores = body.get("subscores")
    feedback = body.get("feedback")
    return {
        "session_id": str(body.get("session_id") or ""),
        "score_version": str(
            body.get("score_version")
            or body.get("schema_version")
            or "rehab_score_result_v1"
        ),
        "valid": bool(body.get("valid", True)),
        "final_score": float(final_score),
        "subscores": deepcopy(subscores) if isinstance(subscores, dict) else {},
        "compensation": deepcopy(body.get("compensation"))
        if isinstance(body.get("compensation"), dict)
        else {},
        "fatigue": deepcopy(body.get("fatigue"))
        if isinstance(body.get("fatigue"), dict)
        else {},
        "brs": body.get("brs"),
        "feedback": list(feedback) if isinstance(feedback, list) else [],
        "data_quality_level": body.get("data_quality_level"),
        "warnings": list(body.get("warnings"))
        if isinstance(body.get("warnings"), list)
        else [],
        "completed_at": body.get("completed_at"),
        "raw_source": "real",
    }


class RealRuntimeService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._snapshot = self._waiting_snapshot()
        self._last_p4_received_ms: int | None = None
        self._last_node_seq: dict[int, int] = {}
        self._last_node_received_ms: dict[int, int] = {}
        self._active_session_id = IDLE_SESSION_ID
        self._last_completed_session_id = ""
        self._score_result: dict[str, Any] | None = None
        self._last_score_poll_ms = 0
        self._sessions: dict[str, dict[str, Any]] = {}
        self._reports: dict[str, dict[str, Any]] = {}
        self._history_loaded = False
        self._logs: dict[str, dict[str, deque[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=MAX_LOG_ROWS))
        )
        self._runtime_events: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=100)
        )
        self._last_training_state = "idle"
        self._last_score_state: dict[str, str] = {}
        self._last_node_online: dict[int, bool] = {}
        self._sessions_with_samples: set[str] = set()
        self._event_index = 0

    async def start(self) -> None:
        if not self._history_loaded:
            self._load_history()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="real-runtime-service")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(P4_RUNTIME_POLL_INTERVAL_SECONDS)

    async def refresh(self) -> None:
        runtime = (
            await asyncio.to_thread(
                _request_json, f"{P4_BASE_URL}/api/rehab/runtime"
            )
            if P4_BASE_URL
            else {}
        )
        if runtime.get("ok") is True:
            self._last_p4_received_ms = _now_ms()
            await self._ingest_runtime(runtime)
        else:
            await self._mark_disconnected()

    async def _mark_disconnected(self) -> None:
        now = _now_ms()
        if (
            self._last_p4_received_ms is not None
            and now - self._last_p4_received_ms
            <= int(P4_DEVICE_OFFLINE_TIMEOUT_SECONDS * 1000)
        ):
            return
        snapshot = deepcopy(self._snapshot)
        snapshot["devices"] = self._offline_devices()
        for samples in snapshot.get("sensor_samples", {}).values():
            if isinstance(samples, list):
                for sample in samples:
                    if isinstance(sample, dict):
                        sample["online"] = False
        snapshot["updated_at"] = _iso_now()
        snapshot["connection_message"] = (
            "\u7b49\u5f85\u8bbe\u5907\u6570\u636e"
            if self._last_p4_received_ms is None
            else "\u8bbe\u5907\u8fde\u63a5\u4e2d\u65ad"
        )
        self._snapshot = snapshot
        await self._publish(snapshot)

    async def _ingest_runtime(self, runtime: dict[str, Any]) -> None:
        training = runtime.get("training")
        training = training if isinstance(training, dict) else {}
        current = training.get("current_session")
        current = current if isinstance(current, dict) else {}
        completed = training.get("last_completed_session")
        completed = completed if isinstance(completed, dict) else {}
        score_bridge = runtime.get("score_bridge")
        score_bridge = score_bridge if isinstance(score_bridge, dict) else {}
        nodes = runtime.get("nodes")
        nodes = nodes if isinstance(nodes, list) else []

        current_session_id = str(current.get("session_id") or "")
        completed_session_id = str(completed.get("session_id") or "")
        if current.get("active") and current_session_id:
            if self._active_session_id != current_session_id:
                self._score_result = None
            self._active_session_id = current_session_id
        elif completed.get("exists") and completed_session_id:
            if self._active_session_id != completed_session_id:
                self._score_result = None
            self._active_session_id = completed_session_id

        session_id = self._active_session_id
        state = str(training.get("state") or "idle")
        if state == "recording" and self._last_training_state != "recording":
            self._record_runtime_event(
                session_id,
                "training",
                "training_started",
                state,
                {
                    "training_state": state,
                    "generation": training.get("generation"),
                    "rows_written": current.get("rows_written"),
                },
            )
        elif state == "idle" and self._last_training_state in {"recording", "stopping"}:
            self._record_runtime_event(
                session_id,
                "training",
                "training_stopped",
                state,
                {
                    "training_state": state,
                    "generation": training.get("generation"),
                    "rows_written": completed.get("rows_written"),
                    "end_reason": completed.get("end_reason"),
                },
            )
        if state != self._last_training_state:
            self._last_training_state = state

        if completed.get("exists") and completed_session_id:
            self._last_completed_session_id = completed_session_id
            self._ensure_session(completed_session_id, completed, training)

        sensor_samples = self._capture_node_logs(session_id, nodes, runtime)
        score_state = str(score_bridge.get("state") or "not_available")
        self._capture_score_transition(session_id, score_state, score_bridge)
        await self._refresh_score(session_id, score_state, score_bridge)
        status = self._ui_status(state, score_state)
        devices = self._build_devices(nodes, runtime)
        score = self._score_result
        if status == "REPORT_READY" and score is None:
            status = "SCORING"

        snapshot = self._waiting_snapshot()
        snapshot.update(
            {
                "status": status,
                "phase": self._phase(status),
                "next_phase": self._next_phase(status),
                "session_id": session_id,
                "task": {
                    "task_id": self._task_id(
                        str(current.get("task_name") or completed.get("task_name") or "")
                    ),
                    "name": str(
                        current.get("task_name")
                        or completed.get("task_name")
                        or "\u5f85\u8bbe\u5907\u4e0a\u62a5"
                    ),
                    "difficulty": "",
                    "target_reps": 0,
                    "completed_reps": 0,
                    "prescription": "\u6309\u5f53\u524d\u5df2\u542f\u7528\u5904\u65b9\u6267\u884c",
                    "target_angles": {},
                },
                "quality": self._quality(score),
                "devices": devices,
                "sensor_samples": sensor_samples,
                "scores": self._score_rows(score),
                "events": [
                    {
                        "key": item["category"],
                        "time_sec": 0,
                        "text": item["business_event"],
                    }
                    for item in self._runtime_events.get(session_id, [])
                ][-8:],
                "report_ready": status == "REPORT_READY" and score is not None,
                "training_status": {
                    "session_id": session_id,
                    "node1_online": int(self._node(nodes, 1).get("online") is True),
                    "node2_online": int(self._node(nodes, 2).get("online") is True),
                    "required_ok": int(training.get("required_sensors_ok") is True),
                    "training_rows": current.get(
                        "rows_written", completed.get("rows_written")
                    ),
                    "score_state": score_state,
                    "record_queue_drop": training.get("record_queue_drop_count"),
                    "sd_write_error": training.get("sd_write_error_count"),
                },
                "raw_log_summary": {
                    "rows": sum(
                        len(rows)
                        for rows in self._logs.get(session_id, {}).values()
                    ),
                    "sampling_rate_hz": self._measured_rate(nodes),
                    "job_state": score_state,
                    "score_state": score_state,
                },
                "data_origin": "real",
                "control_mode": "p4",
                "field_sources": {
                    "runtime": "esp32_p4",
                    "score": "pc_score_service" if score else "unavailable",
                    "runtime_events": "generated_from_real_state_changes",
                },
                "connection_message": "\u8bbe\u5907\u6570\u636e\u63a5\u6536\u4e2d",
                "updated_at": _iso_now(),
            }
        )
        self._snapshot = snapshot
        if snapshot["report_ready"]:
            existing_report = self._reports.get(session_id)
            if (
                existing_report is None
                or existing_report.get("score") != self._score_result
            ):
                report = self._build_report(snapshot, completed)
                self._reports[session_id] = report
                device_history_store.save_report(session_id, report)
        self._update_session(snapshot, current, completed)
        await self._publish(snapshot)

    async def _refresh_score(
        self,
        session_id: str,
        state: str,
        score_bridge: dict[str, Any],
    ) -> None:
        if not session_id or session_id == IDLE_SESSION_ID:
            return
        now = _now_ms()
        if now - self._last_score_poll_ms < int(SCORE_POLL_INTERVAL_SECONDS * 1000):
            return
        self._last_score_poll_ms = now
        if state == "not_available":
            return
        job_id = str(score_bridge.get("pc_job_id") or session_id)
        result = await asyncio.to_thread(
            _request_json,
            f"{SCORE_BASE_URL}/api/v1/score/jobs/{job_id}/result",
        )
        parsed = _score_payload(result)
        if (
            parsed is not None
            and parsed["session_id"]
            and parsed["session_id"] != session_id
        ):
            parsed = None
        if parsed is not None:
            first_result = self._score_result is None
            self._score_result = parsed
            if first_result:
                self._logs[session_id]["scores"].append(
                    {
                        "received_at": _iso_now(),
                        "received_at_ms": now,
                        "timestamp_ms": now,
                        "session_id": session_id,
                        "source": "pc_score_service",
                        "sequence": 1,
                        "seq": 1,
                        "score": deepcopy(parsed),
                        "raw_source": "real",
                    }
                )
                self._record_runtime_event(
                    session_id,
                    "report",
                    "score_result_received",
                    "succeeded",
                    {
                        "score_version": parsed["score_version"],
                        "valid": parsed["valid"],
                        "final_score": parsed["final_score"],
                    },
                )

    def _capture_node_logs(
        self,
        session_id: str,
        nodes: list[Any],
        runtime: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        if not session_id or session_id == IDLE_SESSION_ID:
            received_at = _now_ms()
            for raw_node in nodes:
                if not isinstance(raw_node, dict) or not raw_node.get("available"):
                    continue
                node_id = int(raw_node.get("node_id") or 0)
                tx_seq = int(raw_node.get("tx_seq") or 0)
                if node_id > 0 and self._last_node_seq.get(node_id) != tx_seq:
                    self._last_node_seq[node_id] = tx_seq
                    self._last_node_received_ms[node_id] = received_at
            return self._sensor_samples(nodes)
        received_at = _now_ms()
        any_new_packet = False
        for raw_node in nodes:
            if not isinstance(raw_node, dict) or not raw_node.get("available"):
                continue
            node_id = int(raw_node.get("node_id") or 0)
            tx_seq = int(raw_node.get("tx_seq") or 0)
            online = raw_node.get("online") is True
            prior_online = self._last_node_online.get(node_id)
            if prior_online is None or prior_online != online:
                self._record_runtime_event(
                    session_id,
                    "device",
                    "device_online" if online else "device_offline",
                    "online" if online else "offline",
                    {
                        "node_id": node_id,
                        "last_rx_age_ms": raw_node.get("last_rx_age_ms"),
                    },
                )
            self._last_node_online[node_id] = online
            if node_id <= 0 or self._last_node_seq.get(node_id) == tx_seq:
                continue
            self._last_node_seq[node_id] = tx_seq
            self._last_node_received_ms[node_id] = received_at
            any_new_packet = True
            quality = {
                "online": raw_node.get("online"),
                "measured_pps": raw_node.get("measured_pps"),
                "valid_packet_count": raw_node.get("valid_packet_count"),
                "gap_count": raw_node.get("gap_count"),
                "udp_gap_ratio_pct": raw_node.get("udp_gap_ratio_pct"),
                "udp_quality": raw_node.get("udp_quality"),
                "checksum_error_count": raw_node.get("checksum_error_count"),
            }
            imu_record = {
                "received_at": _iso_now(),
                "received_at_ms": received_at,
                "timestamp_ms": received_at,
                "session_id": session_id,
                "source": f"node{node_id}",
                "sequence": tx_seq,
                "seq": tx_seq,
                "sensor_id": f"node{node_id}",
                "received_at_us": raw_node.get("received_at_us"),
                "packet_rate_hz": raw_node.get("packet_rate_hz"),
                "tx_ts_us": raw_node.get("tx_ts_us"),
                "flags": raw_node.get("flags"),
                "imu": {
                    "imu1": deepcopy(raw_node.get("imu1") or {}),
                    "imu2": deepcopy(raw_node.get("imu2") or {}),
                },
                "quality": quality,
                "raw_source": "real",
            }
            self._logs[session_id]["imu"].append(imu_record)
            if session_id not in self._sessions_with_samples:
                self._sessions_with_samples.add(session_id)
                self._record_runtime_event(
                    session_id,
                    "data",
                    "realtime_data_received",
                    "received",
                    {"node_id": node_id, "sequence": tx_seq},
                )
            emg = raw_node.get("emg")
            if node_id == 1 and isinstance(emg, dict) and emg.get("valid"):
                self._logs[session_id]["emg"].append(
                    {
                        "received_at": _iso_now(),
                        "received_at_ms": received_at,
                        "timestamp_ms": received_at,
                        "session_id": session_id,
                        "source": "node1",
                        "sequence": tx_seq,
                        "seq": tx_seq,
                        "sensor_id": "node1_semg",
                        "received_at_us": raw_node.get("received_at_us"),
                        "packet_rate_hz": raw_node.get("packet_rate_hz"),
                        "tx_ts_us": raw_node.get("tx_ts_us"),
                        "flags": raw_node.get("flags"),
                        "emg": deepcopy(emg),
                        "quality": quality,
                        "raw_source": "real",
                    }
                )
        if any_new_packet:
            self._logs[session_id]["device_status"].append(
                {
                    "received_at": _iso_now(),
                    "received_at_ms": received_at,
                    "timestamp_ms": received_at,
                    "session_id": session_id,
                    "source": "esp32_p4",
                    "sequence": runtime.get("captured_at_us"),
                    "seq": int(runtime.get("captured_at_us") or 0),
                    "quality": {
                        "p4_runtime_received": True,
                        "nodes": len(nodes),
                    },
                    "raw_source": "real",
                }
            )
        return self._sensor_samples(nodes)

    def _sensor_samples(
        self, nodes: list[Any]
    ) -> dict[str, list[dict[str, Any]]]:
        imu_samples: list[dict[str, Any]] = []
        emg_samples: list[dict[str, Any]] = []
        for node_id in (1, 2):
            node = self._node(nodes, node_id)
            if not node.get("available"):
                continue
            sequence = int(node.get("tx_seq") or 0)
            received_at_ms = self._last_node_received_ms.get(node_id)
            for imu_index in (1, 2):
                raw = node.get(f"imu{imu_index}")
                raw = raw if isinstance(raw, dict) else {}
                sensor_index = (node_id - 1) * 2 + imu_index
                imu_samples.append(
                    {
                        "sensor_id": f"imu{sensor_index}",
                        "label": f"IMU {sensor_index}",
                        "source": f"node{node_id}.imu{imu_index}",
                        "session_id": self._active_session_id,
                        "sequence": sequence,
                        "received_at_ms": received_at_ms,
                        "tx_ts_us": node.get("tx_ts_us"),
                        "online": node.get("online") is True,
                        "valid": raw.get("valid") is True,
                        "roll_deg": raw.get("angle_x_deg"),
                        "pitch_deg": raw.get("angle_y_deg"),
                        "yaw_deg": raw.get("angle_z_deg"),
                    }
                )
            if node_id != 1:
                continue
            emg = node.get("emg")
            emg = emg if isinstance(emg, dict) else {}
            for channel in range(4):
                emg_samples.append(
                    {
                        "sensor_id": f"emg{channel + 1}",
                        "label": f"EMG {channel + 1}",
                        "source": f"node1.emg{channel + 1}",
                        "session_id": self._active_session_id,
                        "sequence": sequence,
                        "received_at_ms": received_at_ms,
                        "tx_ts_us": node.get("tx_ts_us"),
                        "online": node.get("online") is True,
                        "valid": emg.get("valid") is True,
                        "rms": self._array_value(emg.get("rms"), channel),
                        "mav": self._array_value(emg.get("mav"), channel),
                        "peak": self._array_value(emg.get("peak_abs"), channel),
                        "latest": self._array_value(emg.get("latest"), channel),
                    }
                )
        return {"imu": imu_samples, "emg": emg_samples}

    @staticmethod
    def _array_value(value: Any, index: int) -> Any:
        return value[index] if isinstance(value, list) and len(value) > index else None

    def _capture_score_transition(
        self,
        session_id: str,
        state: str,
        score_bridge: dict[str, Any],
    ) -> None:
        if not session_id or session_id == IDLE_SESSION_ID:
            return
        prior = self._last_score_state.get(session_id)
        self._last_score_state[session_id] = state
        if prior == state or state == "not_available":
            return
        event = {
            "pending": "score_job_pending",
            "uploading": "csv_uploading",
            "accepted": "score_job_started",
            "scoring": "score_job_started",
            "succeeded": "score_job_succeeded",
            "failed": "score_job_failed",
        }.get(state)
        if event is None:
            return
        self._record_runtime_event(
            session_id,
            "score",
            event,
            state,
            {
                "score_state": state,
                "pc_job_id": score_bridge.get("pc_job_id"),
                "attempt": score_bridge.get("attempt"),
            },
        )

    def _record_runtime_event(
        self,
        session_id: str,
        category: str,
        event: str,
        result: str,
        details: dict[str, Any],
    ) -> None:
        if not session_id:
            return
        self._event_index += 1
        item = {
            "id": f"real-{self._event_index}",
            "time": datetime.now().strftime("%H:%M:%S"),
            "timestamp_ms": _now_ms(),
            "category": category,
            "business_event": event,
            "result": result,
            "reason": "\u7531\u8bbe\u5907\u6216\u8bc4\u5206\u670d\u52a1\u771f\u5b9e\u72b6\u6001\u53d8\u5316\u89e6\u53d1",
            "important": True,
            "expanded_by_default": True,
            "session_id": session_id,
            "source": "esp_claw",
            "technical_details": deepcopy(details),
        }
        self._runtime_events[session_id].append(item)
        self._logs[session_id]["events"].append(
            {
                **deepcopy(item),
                "received_at_ms": item["timestamp_ms"],
                "seq": self._event_index,
                "raw_source": "real",
            }
        )

    async def _publish(self, snapshot: dict[str, Any]) -> None:
        await store.set_snapshot(snapshot)
        await hub.broadcast(await self.state_message())
        await hub.broadcast(
            await self._message("device.status", {"devices": snapshot["devices"]})
        )

    async def _message(
        self, message_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return RealtimeMessage(
            schema_version=SCHEMA_VERSION,
            type=message_type,  # type: ignore[arg-type]
            device_id=DEVICE_ID,
            patient_id=PATIENT_ID,
            session_id=self._snapshot.get("session_id", IDLE_SESSION_ID),
            seq=max(1, await store.next_seq()),
            timestamp_ms=_now_ms(),
            source_mode="device",
            payload=payload,
        ).model_dump()

    async def state_message(self) -> dict[str, Any]:
        return await self._message(
            "rehab.state", {"snapshot": self.snapshot()}
        )

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self._snapshot)

    def report(self, session_id: str | None = None) -> dict[str, Any] | None:
        snapshot_session_id = str(self._snapshot.get("session_id") or "")
        requested_session_id = session_id or (
            snapshot_session_id
            if snapshot_session_id != IDLE_SESSION_ID
            else self._last_completed_session_id
        )
        report = self._reports.get(requested_session_id)
        return deepcopy(report) if report is not None else None

    def sessions(self) -> list[dict[str, Any]]:
        return [
            deepcopy(item)
            for item in sorted(
                self._sessions.values(),
                key=lambda value: value.get("started_at_ms", 0),
                reverse=True,
            )
        ]

    def session(self, session_id: str) -> dict[str, Any] | None:
        item = self._sessions.get(session_id)
        if item is None:
            return None
        result = deepcopy(item)
        result["logging_status"] = {
            "drop_count": 0,
            "queue_size": 0,
            "bounded_rows": MAX_LOG_ROWS,
        }
        return result

    def logs(
        self, session_id: str, log_type: str, limit: int, offset: int
    ) -> dict[str, Any]:
        rows = list(self._logs.get(session_id, {}).get(log_type, []))
        rows.reverse()
        chunk = rows[offset : offset + limit]
        return {
            "items": deepcopy(chunk),
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < len(rows),
            "total_seen": len(rows),
        }

    def patients(self) -> list[dict[str, Any]]:
        score = self._snapshot.get("quality", {}).get("final_score")
        return [
            {
                "patient_id": PATIENT_ID,
                "display_name": PATIENT_ID,
                "profile_label": "\u53f3\u4fa7\u4e0a\u80a2\u5eb7\u590d\u8bad\u7ec3",
                "stage": "\u8111\u5352\u4e2d\u6062\u590d\u671f",
                "affected_side": "\u53f3\u4fa7\u4e0a\u80a2",
                "brs": "IV",
                "age_range": "55-65",
                "today_training_count": len(self._sessions),
                "latest_score": score,
                "risk_level": "\u5f85\u8bc4\u4f30",
                "current_status": self._phase(self._snapshot["status"]),
                "current_task": self._snapshot["task"]["name"],
                "latest_training_time": self._snapshot["updated_at"],
                "fatigue_risk": "\u5f85\u8bc4\u5206\u7ed3\u679c",
                "device_status": self._snapshot.get(
                    "connection_message", "\u7b49\u5f85\u8bbe\u5907\u6570\u636e"
                ),
            }
        ]

    def patient(self, patient_id: str) -> dict[str, Any]:
        patient = self.patients()[0]
        return patient if patient_id == PATIENT_ID else {**patient, "patient_id": patient_id}

    def _update_session(
        self,
        snapshot: dict[str, Any],
        current: dict[str, Any],
        completed: dict[str, Any],
    ) -> None:
        session_id = snapshot["session_id"]
        if session_id == IDLE_SESSION_ID:
            return
        prior = self._sessions.get(session_id, {})
        status = snapshot["status"]
        final_score = snapshot["quality"].get("final_score")
        report_ready = snapshot["report_ready"] or session_id in self._reports
        completed_for_session = bool(
            completed.get("exists")
            and str(completed.get("session_id") or "") == session_id
        )
        rows_written = (
            completed.get("rows_written")
            if completed_for_session
            else current.get("rows_written")
        )
        summary = {
            "patient_id": PATIENT_ID,
            "session_id": session_id,
            "scenario_id": self._task_id(snapshot["task"]["name"]),
            "task": snapshot["task"]["name"],
            "target_reps": 0,
            "completed_reps": 0,
            "status": "REPORT_READY" if report_ready else status,
            "final_score": final_score
            if final_score is not None
            else prior.get("final_score"),
            "report_ready": report_ready,
            "source_mode": "device",
            "started_at_ms": prior.get("started_at_ms") or _now_ms(),
            "ended_at_ms": prior.get("ended_at_ms")
            or (_now_ms() if completed_for_session else None),
            "rows_written": rows_written
            if rows_written is not None
            else prior.get("rows_written"),
            "duration_ms": completed.get("duration_ms")
            if completed_for_session
            else prior.get("duration_ms"),
            "log_drop_count": prior.get("log_drop_count", 0),
        }
        self._sessions[session_id] = summary
        if summary != prior:
            device_history_store.save_session(summary)

    def _ensure_session(
        self,
        session_id: str,
        completed: dict[str, Any],
        training: dict[str, Any],
    ) -> None:
        if session_id in self._sessions:
            return
        summary = {
            "patient_id": PATIENT_ID,
            "session_id": session_id,
            "scenario_id": self._task_id(str(completed.get("task_name") or "")),
            "task": str(completed.get("task_name") or ""),
            "target_reps": 0,
            "completed_reps": 0,
            "status": "UPLOADING",
            "final_score": None,
            "report_ready": False,
            "source_mode": "device",
            "started_at_ms": _now_ms() - int(completed.get("duration_ms") or 0),
            "ended_at_ms": _now_ms(),
            "rows_written": completed.get("rows_written"),
            "duration_ms": completed.get("duration_ms"),
            "log_drop_count": training.get("record_queue_drop_count"),
        }
        self._sessions[session_id] = summary
        device_history_store.save_session(summary)

    def _load_history(self) -> None:
        sessions, reports = device_history_store.load()
        self._sessions.update(sessions)
        self._reports.update(reports)
        completed = [
            item
            for item in sessions.values()
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

    def _build_report(
        self, snapshot: dict[str, Any], completed: dict[str, Any]
    ) -> dict[str, Any]:
        score = deepcopy(self._score_result or {})
        return {
            "title": "\u4e0a\u80a2\u5eb7\u590d\u8bad\u7ec3\u8bc4\u4f30\u62a5\u544a",
            "patient": {
                "patient_id": PATIENT_ID,
                "affected_side": "\u53f3\u4fa7\u4e0a\u80a2",
                "brs": "IV",
                "task": snapshot["task"]["name"],
                "difficulty": snapshot["task"]["difficulty"],
                "training_time": snapshot["updated_at"],
                "session_id": snapshot["session_id"],
                "device_id": DEVICE_ID,
            },
            "completion": {
                "planned_reps": 0,
                "completed_reps": 0,
                "valid_reps": 0,
                "duration_sec": round(float(completed.get("duration_ms") or 0) / 1000, 3),
                "pause_count": 0,
                "fully_completed": completed.get("exists") is True,
                "end_reason": str(completed.get("end_reason") or ""),
            },
            "score": score,
            "rep_details": [],
            "kinematics": {},
            "emg_analysis": {},
            "compensation_analysis": deepcopy(score.get("compensation") or {}),
            "fatigue_analysis": deepcopy(score.get("fatigue") or {}),
            "summary": "\u672c\u62a5\u544a\u7ed1\u5b9a\u5f53\u524d\u8bbe\u5907\u4f1a\u8bdd\u548c\u8bc4\u5206\u670d\u52a1\u8fd4\u56de\u7ed3\u679c\u3002",
            "next_recommendations": list(score.get("feedback") or []),
            "versions": {
                "source_mode": "device",
                "score_version": score.get("score_version", ""),
                "schema_version": "rehab_score_result_v1",
                "device_id": DEVICE_ID,
                "generated_at": _iso_now(),
            },
            "disclaimer": "\u62a5\u544a\u4ec5\u4f9b\u5eb7\u590d\u4e13\u4e1a\u4eba\u5458\u53c2\u8003\u3002",
            "demo_notice": "",
        }

    @staticmethod
    def _node(nodes: list[Any], node_id: int) -> dict[str, Any]:
        for node in nodes:
            if isinstance(node, dict) and int(node.get("node_id") or 0) == node_id:
                return node
        return {}

    def _build_devices(
        self, nodes: list[Any], runtime: dict[str, Any]
    ) -> list[dict[str, Any]]:
        updated = _iso_now()
        devices = [
            self._device(
                DEVICE_ID,
                "ESP32-P4",
                "system_service",
                None,
                True,
                0,
                int(runtime.get("captured_at_us") or 0),
                updated,
            )
        ]
        for node_id in (1, 2):
            node = self._node(nodes, node_id)
            online = node.get("online") is True
            pps = float(node.get("measured_pps") or 0)
            gap = int(node.get("gap_count") or 0)
            count = int(node.get("valid_packet_count") or 0)
            node_updated = _iso_from_age_ms(node.get("last_rx_age_ms"))
            for imu_index in (1, 2):
                imu = node.get(f"imu{imu_index}")
                imu = imu if isinstance(imu, dict) else {}
                devices.append(
                    self._device(
                        f"node{node_id}_imu{imu_index}",
                        f"Node {node_id} IMU {imu_index}",
                        "sensor",
                        "IMU",
                        online and imu.get("valid") is True,
                        pps,
                        int(node.get("tx_seq") or 0),
                        node_updated,
                        gap,
                        count,
                        int(node.get("out_of_order_count") or 0),
                        int(node.get("checksum_error_count") or 0),
                    )
                )
            if node_id == 1:
                emg = node.get("emg")
                emg = emg if isinstance(emg, dict) else {}
                devices.append(
                    self._device(
                        "node1_semg",
                        "Node 1 sEMG",
                        "sensor",
                        "sEMG",
                        online and emg.get("valid") is True,
                        pps,
                        int(node.get("tx_seq") or 0),
                        node_updated,
                        gap,
                        count,
                        int(node.get("out_of_order_count") or 0),
                        int(node.get("checksum_error_count") or 0),
                    )
                )
        return devices

    @staticmethod
    def _device(
        device_id: str,
        name: str,
        category: str,
        sensor_type: str | None,
        online: bool,
        rate: float,
        sequence: int,
        updated_at: str,
        packet_loss: int | None = None,
        packet_count: int | None = None,
        out_of_order_dropped: int | None = None,
        error_count: int | None = None,
    ) -> dict[str, Any]:
        return {
            "device_id": device_id,
            "name": name,
            "category": category,
            "sensor_type": sensor_type,
            "role": "",
            "mode": "\u771f\u5b9e\u8bbe\u5907\u6570\u636e",
            "online": online,
            "sample_rate": f"{rate:.1f} Hz" if rate else "--",
            "raw_sampling_hz": rate or None,
            "visualization_hz": None,
            "latency_ms": None,
            "avg_latency_ms": None,
            "quality": None,
            "last_seq": sequence,
            "packet_loss": packet_loss,
            "packet_count": packet_count,
            "out_of_order_dropped": out_of_order_dropped,
            "error_count": error_count,
            "updated_at": updated_at,
        }

    def _offline_devices(self) -> list[dict[str, Any]]:
        updated = _iso_now()
        return [
            self._device(
                DEVICE_ID,
                "ESP32-P4",
                "system_service",
                None,
                False,
                0,
                0,
                updated,
            )
        ]

    @staticmethod
    def _quality(score: dict[str, Any] | None) -> dict[str, Any]:
        if score is None:
            return {
                "final_score": None,
                "subscores": {},
                "fatigue_level": "\u6682\u65e0\u6700\u65b0\u6570\u636e",
                "compensation_risk": "\u6682\u65e0\u6700\u65b0\u6570\u636e",
                "suggestion": "\u7b49\u5f85\u771f\u5b9e\u8bc4\u5206\u7ed3\u679c",
            }
        fatigue = score.get("fatigue")
        compensation = score.get("compensation")
        return {
            "final_score": score["final_score"],
            "subscores": deepcopy(score["subscores"]),
            "fatigue_level": json.dumps(fatigue, ensure_ascii=False)
            if fatigue
            else "\u672a\u8fd4\u56de",
            "compensation_risk": json.dumps(compensation, ensure_ascii=False)
            if compensation
            else "\u672a\u8fd4\u56de",
            "suggestion": "\uff1b".join(str(item) for item in score["feedback"]),
        }

    @staticmethod
    def _score_rows(score: dict[str, Any] | None) -> list[dict[str, Any]]:
        if score is None:
            return []
        return [
            {
                "rep_index": 0,
                "score": deepcopy(score),
                "quality": RealRuntimeService._quality(score),
            }
        ]

    @staticmethod
    def _ui_status(training_state: str, score_state: str) -> str:
        if training_state in {"starting", "recording", "stopping"}:
            return "RUNNING"
        if score_state in {"pending", "uploading"}:
            return "UPLOADING"
        if score_state in {"accepted", "scoring"}:
            return "SCORING"
        if score_state == "succeeded":
            return "REPORT_READY"
        return "READY"

    @staticmethod
    def _phase(status: str) -> str:
        return {
            "RUNNING": "\u8bad\u7ec3\u8fdb\u884c\u4e2d",
            "UPLOADING": "\u6570\u636e\u4e0a\u4f20\u4e2d",
            "SCORING": "\u8bc4\u5206\u4e2d",
            "REPORT_READY": "\u62a5\u544a\u5df2\u5b8c\u6210",
        }.get(status, "\u7b49\u5f85\u8bbe\u5907\u5f00\u59cb\u8bad\u7ec3")

    @staticmethod
    def _next_phase(status: str) -> str:
        return {
            "RUNNING": "\u7b49\u5f85 P4 \u505c\u6b62\u4e8b\u4ef6",
            "UPLOADING": "\u7b49\u5f85\u8bc4\u5206\u4efb\u52a1",
            "SCORING": "\u7b49\u5f85\u771f\u5b9e\u8bc4\u5206\u7ed3\u679c",
            "REPORT_READY": "\u67e5\u770b\u62a5\u544a",
        }.get(status, "\u6682\u65e0\u6700\u65b0\u6570\u636e")

    @staticmethod
    def _task_id(task_name: str) -> str:
        if task_name in {"drink_water_right", "pick_book_right"}:
            return task_name
        if "\u4e66" in task_name:
            return "pick_book_right"
        if "\u6c34" in task_name or "\u676f" in task_name:
            return "drink_water_right"
        return ""

    @staticmethod
    def _measured_rate(nodes: list[Any]) -> float | None:
        rates = [
            float(node.get("measured_pps"))
            for node in nodes
            if isinstance(node, dict)
            and isinstance(node.get("measured_pps"), (int, float))
        ]
        return round(sum(rates), 2) if rates else None

    @staticmethod
    def _waiting_snapshot() -> dict[str, Any]:
        return {
            "status": "READY",
            "phase": "\u7b49\u5f85\u8bbe\u5907\u6570\u636e",
            "next_phase": "\u6682\u65e0\u6700\u65b0\u6570\u636e",
            "session_id": IDLE_SESSION_ID,
            "patient": {
                "patient_id": PATIENT_ID,
                "profile_label": "\u53f3\u4fa7\u4e0a\u80a2\u5eb7\u590d\u8bad\u7ec3",
                "diagnosis_stage": "\u8111\u5352\u4e2d\u6062\u590d\u671f",
                "affected_side": "\u53f3\u4fa7\u4e0a\u80a2",
                "brs": "IV",
                "age_band": "55-65",
                "today_sessions": 0,
                "today_average_score": None,
                "risk_level": "\u5f85\u8bc4\u4f30",
            },
            "task": {
                "task_id": "",
                "name": "\u5f85\u8bbe\u5907\u4e0a\u62a5",
                "difficulty": "",
                "target_reps": 0,
                "completed_reps": 0,
                "prescription": "\u6309\u5f53\u524d\u5df2\u542f\u7528\u5904\u65b9\u6267\u884c",
                "target_angles": {},
            },
            "telemetry": {},
            "emg": {},
            "quality": RealRuntimeService._quality(None),
            "devices": [],
            "sensor_samples": {"imu": [], "emg": []},
            "scores": [],
            "events": [],
            "alert": None,
            "report_ready": False,
            "connection_message": "\u7b49\u5f85\u8bbe\u5907\u6570\u636e",
            "data_origin": "real",
            "control_mode": "p4",
            "field_sources": {
                "runtime": "unavailable",
                "score": "unavailable",
                "runtime_events": "generated_from_real_state_changes",
            },
            "updated_at": _iso_now(),
        }


real_runtime_service = RealRuntimeService()
