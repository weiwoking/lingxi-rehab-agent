from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from .config import (
    DEVICE_ID,
    PATIENT_ID,
    SCHEMA_VERSION,
    SOURCE_MODE,
    TRAINING_DATA_RESOLVER,
)
from .patient_service import validate_patient
from .raw_log_service import emg_record, euler_to_quaternion, imu_records, raw_log_service
from .models import RealtimeMessage
from .report_service import build_report
from .scenario_loader import load_scenario
from .sensor_topology import imu_nodes, semg_channels, system_services
from .session_store import store
from .websocket_hub import hub

QUATERNION_ORDER = "w,x,y,z"

DRINK_CUP_KEYFRAMES: dict[str, dict[str, float]] = {
    "PREPARE": {
        "shoulder_flex": 8.0,
        "shoulder_abd": 6.0,
        "shoulder_rot": -4.0,
        "elbow_flex": 14.0,
        "forearm_pronation": 8.0,
        "wrist_flex": -8.0,
    },
    "REACH": {
        "shoulder_flex": 44.0,
        "shoulder_abd": 12.0,
        "shoulder_rot": 2.0,
        "elbow_flex": 24.0,
        "forearm_pronation": 16.0,
        "wrist_flex": -2.0,
    },
    "GRASP": {
        "shoulder_flex": 54.0,
        "shoulder_abd": 14.0,
        "shoulder_rot": 4.0,
        "elbow_flex": 46.0,
        "forearm_pronation": 20.0,
        "wrist_flex": 0.0,
    },
    "LIFT": {
        "shoulder_flex": 66.0,
        "shoulder_abd": 10.0,
        "shoulder_rot": 8.0,
        "elbow_flex": 92.0,
        "forearm_pronation": 24.0,
        "wrist_flex": 10.0,
    },
    "APPROACH_MOUTH": {
        "shoulder_flex": 72.0,
        "shoulder_abd": 4.0,
        "shoulder_rot": 12.0,
        "elbow_flex": 118.0,
        "forearm_pronation": 26.0,
        "wrist_flex": 6.0,
    },
    "RETURN": {
        "shoulder_flex": 28.0,
        "shoulder_abd": 8.0,
        "shoulder_rot": 0.0,
        "elbow_flex": 28.0,
        "forearm_pronation": 12.0,
        "wrist_flex": -6.0,
    },
}

DRINK_CUP_PHASE_SEGMENTS: list[tuple[float, float, str, str]] = [
    (0.0, 1 / 6, "PREPARE", "REACH"),
    (1 / 6, 2 / 6, "REACH", "GRASP"),
    (2 / 6, 3 / 6, "GRASP", "LIFT"),
    (3 / 6, 4 / 6, "LIFT", "APPROACH_MOUTH"),
    (4 / 6, 5 / 6, "APPROACH_MOUTH", "RETURN"),
    (5 / 6, 1.0, "RETURN", "PREPARE"),
]


def _quat_normalize(quat: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in quat))
    if norm <= 0.000001:
        return [1.0, 0.0, 0.0, 0.0]
    return [round(value / norm, 6) for value in quat]


def _quat_multiply(left: list[float], right: list[float]) -> list[float]:
    lw, lx, ly, lz = left
    rw, rx, ry, rz = right
    return _quat_normalize([
        lw * rw - lx * rx - ly * ry - lz * rz,
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
    ])


class DemoEngine:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, scenario_id: str, speed: float = 1.0, patient_id: str = PATIENT_ID) -> dict[str, Any]:
        validate_patient(patient_id)
        if self.running:
            raise HTTPException(status_code=409, detail="Demo is already running; reset before starting another session.")
        scenario = load_scenario(scenario_id)
        self._stop = asyncio.Event()
        await store.reset()
        session_id = f"SESSION_DEMO_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
        await raw_log_service.start_session(patient_id, session_id, scenario)
        await store.set_snapshot({"session_id": session_id, "status": "PREPARING", "report_ready": False, "scores": [], "events": []})
        self._task = asyncio.create_task(self._run_scenario(scenario, session_id, speed))
        return {"session_id": session_id, "scenario_id": scenario_id, "status": "started"}

    async def reset(self) -> dict[str, Any]:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        await store.reset()
        message = await self._message("rehab.state", "SESSION_IDLE", {"snapshot": await store.get_snapshot()})
        await hub.broadcast(message.model_dump())
        return {"status": "reset"}

    async def _message(self, msg_type: str, session_id: str, payload: dict[str, Any]) -> RealtimeMessage:
        return RealtimeMessage(
            schema_version=SCHEMA_VERSION,
            type=msg_type,  # type: ignore[arg-type]
            device_id=DEVICE_ID,
            patient_id=PATIENT_ID,
            session_id=session_id,
            seq=await store.next_seq(),
            timestamp_ms=int(time.time() * 1000),
            source_mode=SOURCE_MODE,
            payload=payload,
        )

    async def _publish(self, msg_type: str, session_id: str, payload: dict[str, Any]) -> None:
        message = await self._message(msg_type, session_id, payload)
        self._log_message(message)
        await hub.broadcast(message.model_dump())

    def _log_message(self, message: RealtimeMessage) -> None:
        payload = message.payload
        base = {
            "schema_version": SCHEMA_VERSION,
            "patient_id": PATIENT_ID,
            "session_id": message.session_id,
            "seq": message.seq,
            "timestamp_ms": message.timestamp_ms,
            "received_at_ms": int(time.time() * 1000),
            "source_mode": SOURCE_MODE,
            "frame_label": "DEMO RAW FRAME",
        }
        if message.type == "rehab.telemetry":
            for record in imu_records(message.session_id, message.seq, message.timestamp_ms, payload):
                raw_log_service.enqueue(PATIENT_ID, message.session_id, "imu", record)
        elif message.type == "rehab.emg":
            raw_log_service.enqueue(PATIENT_ID, message.session_id, "emg", emg_record(message.session_id, message.seq, message.timestamp_ms, payload))
        elif message.type == "rehab.event":
            raw_log_service.enqueue(PATIENT_ID, message.session_id, "events", {**base, **payload})
        elif message.type == "rehab.alert":
            raw_log_service.enqueue(PATIENT_ID, message.session_id, "events", {**base, "key": "alert", **payload})
        elif message.type == "rehab.score":
            raw_log_service.enqueue(PATIENT_ID, message.session_id, "scores", {**base, **payload})
        elif message.type == "device.status":
            for device in payload.get("devices", []):
                raw_log_service.enqueue(PATIENT_ID, message.session_id, "device_status", {**base, **device})

    async def _run_scenario(self, scenario: dict[str, Any], session_id: str, speed: float) -> None:
        start = time.perf_counter()
        last_device = -1.0
        emitted_events: set[str] = set()
        emitted_scores: set[int] = set()
        duration = float(scenario["duration_sec"])
        try:
            while not self._stop.is_set():
                t = min((time.perf_counter() - start) * speed, duration)
                frame = self._frame(scenario, t)
                snapshot = await store.set_snapshot(frame["snapshot_patch"])

                if frame["state_event"] and frame["state_event"] not in emitted_events:
                    emitted_events.add(frame["state_event"])
                    await self._publish("rehab.state", session_id, {"snapshot": snapshot})

                await self._publish("rehab.telemetry", session_id, frame["telemetry"])
                await self._publish("rehab.emg", session_id, frame["emg"])

                for event in frame["events"]:
                    key = event["key"]
                    if key not in emitted_events:
                        emitted_events.add(key)
                        events = [*snapshot.get("events", []), event]
                        snapshot = await store.set_snapshot({"events": events[-8:]})
                        await self._publish("rehab.event", session_id, event)

                if frame["alert"] and "alert_3" not in emitted_events:
                    emitted_events.add("alert_3")
                    snapshot = await store.set_snapshot({"alert": frame["alert"]})
                    await self._publish("rehab.alert", session_id, frame["alert"])

                for score in frame["scores"]:
                    rep = score["rep_index"]
                    if rep not in emitted_scores:
                        emitted_scores.add(rep)
                        scores = [*snapshot.get("scores", []), score]
                        task = dict(snapshot["task"])
                        task["completed_reps"] = rep
                        snapshot = await store.set_snapshot({"scores": scores, "task": task, "quality": score["quality"]})
                        await self._publish("rehab.score", session_id, score)

                if t - last_device >= 1.0:
                    last_device = t
                    devices = self._devices(t)
                    await store.set_snapshot({"devices": devices})
                    await self._publish("device.status", session_id, {"devices": devices})

                if t >= duration:
                    final_snapshot = await store.set_snapshot({"status": "REPORT_READY", "phase": "\u5b8c\u6210", "report_ready": True})
                    report = build_report(final_snapshot, scenario, session_id)
                    await store.set_report(report)
                    report_path = (
                        TRAINING_DATA_RESOLVER.resolve_doctor_session(PATIENT_ID, session_id)
                        / "doctor"
                        / "report.json"
                    )
                    report_path.parent.mkdir(parents=True, exist_ok=True)
                    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                    await raw_log_service.finish_session(PATIENT_ID, session_id, report["score"]["final_score"])
                    await self._publish("rehab.report_ready", session_id, {"session_id": session_id, "report": report})
                    await self._publish("rehab.state", session_id, {"snapshot": await store.get_snapshot()})
                    break
                await asyncio.sleep(max(0.01, 0.1 / speed))
        finally:
            self._task = None

    def _frame(self, scenario: dict[str, Any], t: float) -> dict[str, Any]:
        progress = min(max(t / scenario["duration_sec"], 0), 1)
        rep_index = min(5, max(0, math.floor(max(t - 7, 0) / 12) + (1 if t >= 7 else 0)))
        phase = self._phase(t)
        rep_phase = ((max(t - 7, 0) % 12) / 12) if t >= 7 else 0
        wave = math.sin(rep_phase * math.pi)
        improvement = min(rep_index, 5) * 0.045
        trunk = 9 * wave if 31 <= t <= 39 else max(0, 3 * wave if 43 <= t <= 51 else 0)
        motion = self._drink_cup_motion(rep_phase if t >= 7 else 0.0)
        shoulder_flex = round(motion["shoulder_flex"] + improvement * 4.0 * wave, 1)
        shoulder_abd = round(motion["shoulder_abd"], 1)
        shoulder_rot = round(motion["shoulder_rot"], 1)
        elbow_flex = round(motion["elbow_flex"], 1)
        forearm_pronation = round(motion["forearm_pronation"], 1)
        wrist_flex = round(motion["wrist_flex"], 1)
        timestamp_ms = int(time.time() * 1000)
        action_phase = self._action_phase(t)
        telemetry = {
            "shoulder_flexion_deg": shoulder_flex,
            "shoulder_abduction_deg": shoulder_abd,
            "shoulder_rotation_deg": shoulder_rot,
            "elbow_flexion_deg": elbow_flex,
            "forearm_pronation_deg": forearm_pronation,
            "wrist_flexion_deg": wrist_flex,
            "trunk_flexion_deg": round(trunk, 1),
            "trajectory_match": round(58 + progress * 33 - (5 if trunk > 6 else 0), 1),
            "phase_progress": round(rep_phase * 100 if t >= 7 else min(t / 7 * 100, 100), 1),
            "spatial_error_cm": round(max(1.6, 9.5 - progress * 7 + (1.8 if trunk > 6 else 0)), 1),
            "action_phase": action_phase,
            "imu_pose": self._imu_pose(timestamp_ms, shoulder_flex, shoulder_abd, shoulder_rot, elbow_flex, forearm_pronation, wrist_flex, trunk),
        }
        emg = self._emg_frame(action_phase, rep_phase)
        quality = self._quality(rep_index, trunk)
        return {
            "telemetry": telemetry,
            "emg": emg,
            "state_event": self._state_event(t),
            "events": self._events(t),
            "alert": {
                "level": "warning",
                "title": "\u68c0\u6d4b\u5230\u8f7b\u5ea6\u8eaf\u5e72\u524d\u503e",
                "message": "\u8bf7\u4fdd\u6301\u8eaf\u5e72\u7a33\u5b9a\uff0c\u51cf\u5c11\u524d\u503e\u4ee3\u507f\u3002",
                "rep_index": 3,
            } if trunk > 7 else None,
            "scores": self._scores_ready(scenario, t),
            "snapshot_patch": {
                "status": "PREPARING" if t < 7 else "RUNNING",
                "phase": phase,
                "next_phase": self._next_phase(phase),
                "telemetry": telemetry,
                "emg": emg,
                "quality": quality,
            },
        }

    def _emg_frame(self, action_phase: str, rep_phase: float) -> dict[str, Any]:
        reach_wave = max(0.0, math.sin(min(1.0, rep_phase / 0.24) * math.pi)) if action_phase == "REACH" else 0.0
        grasp_wave = 1.0 if action_phase == "GRASP" else 0.0
        lift_wave = 1.0 if action_phase in {"LIFT", "APPROACH_MOUTH"} else 0.0
        return_wave = 1.0 if action_phase == "RETURN" else 0.0
        complete_low = 0.04 if action_phase == "COMPLETE" else 0.0
        ch1 = 0.08 + 0.68 * reach_wave + 0.18 * grasp_wave + complete_low
        ch2 = 0.07 + 0.66 * lift_wave + 0.16 * grasp_wave
        ch3 = 0.06 + 0.62 * return_wave
        ch4 = 0.08 + 0.28 * reach_wave + 0.34 * grasp_wave + 0.32 * lift_wave + 0.26 * return_wave
        return {
            "CH1": {"label": "右侧指伸肌", "muscle_id": "finger_extensors_right", "activation": round(min(ch1, 0.88), 3), "quality": 96, "primary": action_phase == "REACH"},
            "CH2": {"label": "右侧肱二头肌", "muscle_id": "biceps_brachii_right", "activation": round(min(ch2, 0.86), 3), "quality": 94, "primary": action_phase in {"LIFT", "APPROACH_MOUTH"}},
            "CH3": {"label": "右侧肱三头肌", "muscle_id": "triceps_brachii_right", "activation": round(min(ch3, 0.84), 3), "quality": 93, "primary": action_phase == "RETURN"},
            "CH4": {"label": "右侧尺侧腕伸肌", "muscle_id": "extensor_carpi_ulnaris_right", "activation": round(min(ch4, 0.78), 3), "quality": 94, "primary": action_phase in {"GRASP", "LIFT", "APPROACH_MOUTH"}},
        }

    def _action_phase(self, t: float) -> str:
        if t < 7:
            return "PREPARE"
        phase_index = int(((t - 7) % 12) / 2)
        return ["REACH", "GRASP", "LIFT", "APPROACH_MOUTH", "RETURN", "COMPLETE"][min(5, phase_index)]

    def _drink_cup_motion(self, rep_phase: float) -> dict[str, float]:
        p = min(max(rep_phase, 0.0), 1.0)
        for start, end, from_phase, to_phase in DRINK_CUP_PHASE_SEGMENTS:
            if start <= p <= end:
                local = 0.0 if end <= start else (p - start) / (end - start)
                return self._interpolate_keyframes(DRINK_CUP_KEYFRAMES[from_phase], DRINK_CUP_KEYFRAMES[to_phase], self._smoothstep(local))
        return dict(DRINK_CUP_KEYFRAMES["PREPARE"])

    def _interpolate_keyframes(self, start: dict[str, float], end: dict[str, float], alpha: float) -> dict[str, float]:
        return {key: start[key] + (end[key] - start[key]) * alpha for key in start}

    def _smoothstep(self, value: float) -> float:
        t = min(max(value, 0.0), 1.0)
        return t * t * (3 - 2 * t)

    def _imu_pose(self, timestamp_ms: int, shoulder_flex: float, shoulder_abd: float, shoulder_rot: float, elbow_flex: float, forearm_pronation: float, wrist_flex: float, trunk: float) -> dict[str, Any]:
        def sensor(sensor_id: str, quat: list[float]) -> dict[str, Any]:
            normalized = _quat_normalize(quat)
            return {
                "sensor_id": sensor_id,
                "quaternion": {"w": normalized[0], "x": normalized[1], "y": normalized[2], "z": normalized[3]},
                "calibrated": True,
                "quality": 96,
                "timestamp_ms": timestamp_ms,
            }

        trunk_abs = euler_to_quaternion(0, 0, trunk)
        shoulder_relative = euler_to_quaternion(-shoulder_flex, shoulder_rot, shoulder_abd)
        upper_arm_abs = _quat_multiply(trunk_abs, shoulder_relative)
        elbow_relative = euler_to_quaternion(-elbow_flex * 0.72, forearm_pronation * 0.14, 0)
        forearm_abs = _quat_multiply(upper_arm_abs, elbow_relative)
        wrist_relative = euler_to_quaternion(wrist_flex, forearm_pronation * 0.25, 0)
        wrist_abs = _quat_multiply(forearm_abs, wrist_relative)

        return {
            "schema_version": "rehab.imu_pose.v1",
            "mapping_mode": "relative_quaternion_demo",
            "source": "DEMO DATA",
            "quaternion_order": QUATERNION_ORDER,
            "sensors": {
                "lower_back_imu": sensor("lower_back_imu", trunk_abs),
                "upper_arm_imu": sensor("upper_arm_imu", upper_arm_abs),
                "forearm_imu": sensor("forearm_imu", forearm_abs),
                "wrist_imu": sensor("wrist_imu", wrist_abs),
            },
        }

    def _phase(self, t: float) -> str:
        if t < 3:
            return "\u51c6\u5907"
        if t < 7:
            return "\u5904\u65b9\u8f7d\u5165"
        phases = ["\u4f38\u624b", "\u6293\u63e1", "\u62ac\u676f", "\u63a5\u8fd1\u53e3\u90e8", "\u56de\u6536", "\u5b8c\u6210"]
        return phases[min(len(phases) - 1, int(((t - 7) % 12) / 2))]

    def _next_phase(self, phase: str) -> str:
        phases = ["\u51c6\u5907", "\u4f38\u624b", "\u6293\u63e1", "\u62ac\u676f", "\u63a5\u8fd1\u53e3\u90e8", "\u56de\u6536", "\u5b8c\u6210"]
        return phases[min(len(phases) - 1, phases.index(phase) + 1)] if phase in phases else "\u4f38\u624b"

    def _state_event(self, t: float) -> str | None:
        if 0 <= t < 0.25:
            return "state_preparing"
        if 7 <= t < 7.25:
            return "state_running"
        return None

    def _events(self, t: float) -> list[dict[str, Any]]:
        timeline = [
            (0.5, "start_cmd", "\u6536\u5230\u60a3\u8005\u5f00\u59cb\u8bad\u7ec3\u6307\u4ee4"),
            (3.2, "task_selected", "Agent\u9009\u62e9\u62ff\u676f\u559d\u6c34\u4efb\u52a1"),
            (5.0, "device_checked", "\u8bad\u7ec3\u8bbe\u5907\u68c0\u67e5\u5b8c\u6210"),
            (7.2, "phase_reach", "\u8fdb\u5165\u4f38\u624b\u9636\u6bb5"),
            (19.1, "rep1_done", "\u7b2c1\u6b21\u52a8\u4f5c\u5b8c\u6210"),
            (19.4, "rep1_score", "\u6536\u5230\u52a8\u4f5c\u8bc4\u520676\u5206"),
            (32.8, "trunk_comp", "\u68c0\u6d4b\u5230\u8f7b\u5ea6\u8eaf\u5e72\u524d\u503e"),
            (33.1, "edge_alert", "\u5df2\u89e6\u53d1\u8fb9\u7f18\u63d0\u9192"),
            (43.3, "rep3_done", "\u7b2c3\u6b21\u52a8\u4f5c\u5b8c\u6210"),
            (67.5, "report_ready", "\u8bad\u7ec3\u62a5\u544a\u5df2\u51c6\u5907"),
        ]
        return [{"key": key, "time_sec": sec, "text": text} for sec, key, text in timeline if sec <= t]

    def _scores_ready(self, scenario: dict[str, Any], t: float) -> list[dict[str, Any]]:
        ready_times = [19, 31, 43, 55, 67]
        result = []
        for index, ready_t in enumerate(ready_times, start=1):
            if ready_t <= t:
                metric = scenario["rep_metrics"][index - 1]
                result.append({"rep_index": index, "score": {"score_version": "demo-score-1.0", "valid": True, "final_score": metric["final_score"], "subscores": {"speed_score": metric["speed_score"], "ROM_score": metric["ROM_score"], "smoothness_score": metric["smoothness_score"], "coordination_score": metric["coordination_score"], "muscle_score": metric["muscle_score"]}, "compensation": metric["compensation"], "fatigue": metric["fatigue"], "brs": None, "feedback": metric["feedback"]}, "quality": {"final_score": metric["final_score"], "subscores": {"speed_score": metric["speed_score"], "ROM_score": metric["ROM_score"], "smoothness_score": metric["smoothness_score"], "coordination_score": metric["coordination_score"], "muscle_score": metric["muscle_score"]}, "fatigue_level": metric["fatigue"]["level"], "compensation_risk": metric["compensation"]["risk"], "suggestion": metric["feedback"][0]}})
        return result

    def _quality(self, rep_index: int, trunk: float) -> dict[str, Any]:
        base = [0, 76, 79, 81, 83, 86][rep_index] if rep_index else 0
        return {"final_score": base, "subscores": {"speed_score": max(0, base - 2), "ROM_score": max(0, base + 2), "smoothness_score": max(0, base - 1), "coordination_score": max(0, base - (4 if trunk > 7 else 0)), "muscle_score": max(0, base - 3)}, "fatigue_level": "\u8f7b\u5ea6" if rep_index >= 5 else "\u65e0", "compensation_risk": "\u4e2d" if trunk > 7 else "\u4f4e", "suggestion": "\u8bf7\u4fdd\u6301\u8eaf\u5e72\u7a33\u5b9a\uff0c\u51cf\u5c11\u524d\u503e\u4ee3\u507f\u3002" if trunk > 7 else "\u52a8\u4f5c\u4fdd\u6301\u5e73\u7a33\uff0c\u7ee7\u7eed\u8d34\u8fd1\u6807\u51c6\u52a8\u4f5c\u8f68\u8ff9\u3002"}

    def _devices(self, t: float) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        devices: list[dict[str, Any]] = []
        index = 0
        for node in imu_nodes():
            devices.append({"device_id": node["sensor_id"], "name": node["chinese_name"], "category": "sensor", "sensor_type": "IMU", "role": node["role"], "mode": "DEMO", "online": True, "sample_rate": f'{node["raw_sampling_hz"]} Hz', "raw_sampling_hz": node["raw_sampling_hz"], "visualization_hz": node["visualization_hz"], "latency_ms": int(18 + index * 3 + (math.sin(t + index) + 1) * 4), "avg_latency_ms": int(22 + index * 3), "quality": int(94 + (index % 3)), "last_seq": int(t * node["visualization_hz"]), "packet_loss": 0, "out_of_order_dropped": 0, "error_count": 0, "updated_at": now})
            index += 1
        for channel in semg_channels():
            devices.append({"device_id": channel["sensor_id"], "name": channel["chinese_name"], "category": "sensor", "sensor_type": "sEMG", "topology_mode": channel["topology_mode"], "hub_id": channel["hub_id"], "channel_id": channel["channel_id"], "business_muscle_id": channel["business_muscle_id"], "mode": "DEMO", "online": True, "sample_rate": f'{channel["raw_sampling_hz"]} Hz raw / {channel["visualization_hz"]} Hz view', "raw_sampling_hz": channel["raw_sampling_hz"], "visualization_hz": channel["visualization_hz"], "latency_ms": int(20 + index * 3 + (math.sin(t + index) + 1) * 4), "avg_latency_ms": int(24 + index * 3), "quality": int(93 + (index % 4)), "last_seq": int(t * channel["visualization_hz"]), "packet_loss": 0, "out_of_order_dropped": 0, "error_count": 0, "updated_at": now})
            index += 1
        for service_index, service in enumerate(system_services()):
            devices.append({"device_id": service["service_id"], "name": service["name"], "category": "system_service", "mode": "DEMO", "online": True, "sample_rate": "event", "latency_ms": int(12 + service_index * 5), "avg_latency_ms": int(15 + service_index * 5), "quality": 96, "last_seq": int(t * 2), "packet_loss": 0, "out_of_order_dropped": 0, "error_count": 0, "updated_at": now})
        return devices


engine = DemoEngine()
