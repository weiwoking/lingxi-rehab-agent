from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import DEVICE_ID, PATIENT_ID, SCHEMA_VERSION, SOURCE_MODE, TRAINING_DATA_RESOLVER
from .sensor_topology import imu_nodes, semg_channels

LOG_FILES = {
    "imu": "imu_raw.jsonl",
    "emg": "emg_raw.jsonl",
    "events": "events.jsonl",
    "scores": "scores.jsonl",
    "device_status": "device_status.jsonl",
}


@dataclass
class RawLogService:
    queue: asyncio.Queue[tuple[str, str, str, dict[str, Any]]] = field(default_factory=lambda: asyncio.Queue(maxsize=2048))
    writer_task: asyncio.Task | None = None
    drop_count: int = 0

    async def start(self) -> None:
        if self.writer_task is None or self.writer_task.done():
            self.writer_task = asyncio.create_task(self._writer())

    async def stop(self) -> None:
        if self.writer_task:
            await self.queue.join()
            self.writer_task.cancel()
            try:
                await self.writer_task
            except asyncio.CancelledError:
                pass

    def session_dir(self, patient_id: str, session_id: str) -> Path:
        return (
            TRAINING_DATA_RESOLVER.resolve_doctor_session(patient_id, session_id)
            / "doctor"
            / "logs"
        )

    async def start_session(self, patient_id: str, session_id: str, scenario: dict[str, Any]) -> None:
        path = self.session_dir(patient_id, session_id)
        path.mkdir(parents=True, exist_ok=True)
        session = {
            "schema_version": SCHEMA_VERSION,
            "patient_id": patient_id,
            "session_id": session_id,
            "device_id": DEVICE_ID,
            "source_mode": SOURCE_MODE,
            "scenario_id": scenario["scenario_id"],
            "task": "拿杯喝水",
            "target_reps": scenario["target_reps"],
            "status": "RUNNING",
            "started_at_ms": int(time.time() * 1000),
            "completed_reps": 0,
            "final_score": None,
            "report_ready": False,
            "log_drop_count": self.drop_count,
        }
        metadata_path = (
            TRAINING_DATA_RESOLVER.resolve_doctor_session(patient_id, session_id)
            / "session.json"
        )
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")

    async def finish_session(self, patient_id: str, session_id: str, final_score: int) -> None:
        path = (
            TRAINING_DATA_RESOLVER.resolve_doctor_session(patient_id, session_id)
            / "session.json"
        )
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(
            {
                "status": "REPORT_READY",
                "completed_reps": 5,
                "final_score": final_score,
                "report_ready": True,
                "ended_at_ms": int(time.time() * 1000),
                "log_drop_count": self.drop_count,
            }
        )
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def enqueue(self, patient_id: str, session_id: str, log_type: str, record: dict[str, Any]) -> None:
        if log_type not in LOG_FILES:
            return
        try:
            self.queue.put_nowait((patient_id, session_id, log_type, record))
        except asyncio.QueueFull:
            self.drop_count += 1

    def logging_status(self) -> dict[str, Any]:
        return {"queue_size": self.queue.qsize(), "drop_count": self.drop_count}

    async def _writer(self) -> None:
        while True:
            patient_id, session_id, log_type, record = await self.queue.get()
            try:
                path = self.session_dir(patient_id, session_id)
                path.mkdir(parents=True, exist_ok=True)
                file_path = path / LOG_FILES[log_type]
                with file_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            finally:
                self.queue.task_done()


raw_log_service = RawLogService()


def imu_records(session_id: str, seq: int, timestamp_ms: int, telemetry: dict[str, Any]) -> list[dict[str, Any]]:
    topology = {node["sensor_id"]: node for node in imu_nodes()}
    sensors = [
        ("lower_back_imu", telemetry["trunk_flexion_deg"], 0, 0),
        ("wrist_imu", telemetry["wrist_flexion_deg"], 0, telemetry["forearm_pronation_deg"] * 0.2),
        ("forearm_imu", telemetry["elbow_flexion_deg"], telemetry["forearm_pronation_deg"], 0),
        ("upper_arm_imu", telemetry["shoulder_flexion_deg"], telemetry["shoulder_abduction_deg"], telemetry["shoulder_rotation_deg"]),
    ]
    received = int(time.time() * 1000)
    return [
        {
            "schema_version": SCHEMA_VERSION,
            "patient_id": PATIENT_ID,
            "session_id": session_id,
            "seq": seq,
            "timestamp_ms": timestamp_ms,
            "received_at_ms": received,
            "source_mode": SOURCE_MODE,
            "frame_label": "DEMO RAW FRAME",
            "sensor_id": sensor_id,
            "raw_sampling_hz": topology[sensor_id]["raw_sampling_hz"],
            "visualization_hz": topology[sensor_id]["visualization_hz"],
            "sampling_hz": topology[sensor_id]["raw_sampling_hz"],
            "raw": {
                "acc_mps2": [round(math.sin(roll / 45) * 0.8, 4), round(math.sin(pitch / 45) * 0.6, 4), 9.8],
                "gyro_dps": [round(roll * 0.18, 4), round(pitch * 0.14, 4), round(yaw * 0.12, 4)],
                "quaternion": euler_to_quaternion(roll, pitch, yaw),
            },
            "derived": {"roll_deg": round(roll, 3), "pitch_deg": round(pitch, 3), "yaw_deg": round(yaw, 3)},
        }
        for sensor_id, roll, pitch, yaw in sensors
    ]


def emg_record(session_id: str, seq: int, timestamp_ms: int, emg: dict[str, Any]) -> dict[str, Any]:
    channels = {channel["channel_id"]: channel for channel in semg_channels()}
    return {
        "schema_version": SCHEMA_VERSION,
        "patient_id": PATIENT_ID,
        "session_id": session_id,
        "seq": seq,
        "timestamp_ms": timestamp_ms,
        "received_at_ms": int(time.time() * 1000),
        "source_mode": SOURCE_MODE,
        "frame_label": "DEMO RAW FRAME",
        "sensor_id": "semg_hub_01",
        "hub_id": "semg_hub_01",
        "topology_mode": "shared_hub_four_channels",
        "sampling_hz": 1000,
        "raw_sampling_hz": 1000,
        "visualization_hz": 10,
        "channels": {
            channel: {
                "sensor_id": channels[channel]["sensor_id"],
                "hub_id": channels[channel]["hub_id"],
                "channel_id": channel,
                "muscle": channels[channel]["business_muscle_id"],
                "raw_adc": int(1200 + payload["activation"] * 1800),
                "microvolts": round(payload["activation"] * 420, 3),
                "normalized_activation": payload["activation"],
                "signal_quality": payload.get("quality", 0),
                "raw_sampling_hz": channels[channel]["raw_sampling_hz"],
                "visualization_hz": channels[channel]["visualization_hz"],
            }
            for channel, payload in emg.items()
        },
    }


def euler_to_quaternion(roll: float, pitch: float, yaw: float) -> list[float]:
    cr = math.cos(math.radians(roll) * 0.5)
    sr = math.sin(math.radians(roll) * 0.5)
    cp = math.cos(math.radians(pitch) * 0.5)
    sp = math.sin(math.radians(pitch) * 0.5)
    cy = math.cos(math.radians(yaw) * 0.5)
    sy = math.sin(math.radians(yaw) * 0.5)
    return [
        round(cr * cp * cy + sr * sp * sy, 6),
        round(sr * cp * cy - cr * sp * sy, 6),
        round(cr * sp * cy + sr * cp * sy, 6),
        round(cr * cp * sy - sr * sp * cy, 6),
    ]
