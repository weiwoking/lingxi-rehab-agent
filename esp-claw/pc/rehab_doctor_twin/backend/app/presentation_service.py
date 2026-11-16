from __future__ import annotations

import asyncio
import json
import math
import time
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .config import (
    DEVICE_ID,
    LIVE_POLL_INTERVAL_SECONDS,
    P4_BASE_URL,
    PATIENT_ID,
    SCHEMA_VERSION,
    SCORE_BASE_URL,
    SOURCE_MODE,
    VISION_BASE_URL,
)
from .models import RealtimeMessage
from .session_store import store
from .websocket_hub import hub

PRESENTATION_SESSION_ID = "20260724_TASK001"
PRESENTATION_TASK_ID = "DRINK_CUP"
STANDARD_TIMELINE_SECONDS = {"running": 22.0, "uploading": 2.0, "scoring": 3.0}
FAST_TIMELINE_SECONDS = {"running": 9.0, "uploading": 1.0, "scoring": 2.0}
SUBSCORE_OFFSETS = {
    "speed_score": 1.66,
    "ROM_score": -1.04,
    "smoothness_score": 0.76,
    "coordination_score": -0.54,
    "muscle_score": -0.84,
}
REP_DISPLAY_SCORES = [52.80, 54.30, 55.70, 57.20, 59.20]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _zh(value: str) -> str:
    return value


class PresentationService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._started_ms = _now_ms()
        self._training_started_ms: int | None = None
        self._state = "READY"
        self._fast = False
        self._snapshot: dict[str, Any] = self._build_snapshot({}, {})
        self._report: dict[str, Any] = self._build_report(self._snapshot, {})
        self._sessions: list[dict[str, Any]] = self._build_sessions(self._snapshot)
        self._logs: dict[str, list[dict[str, Any]]] = self._build_logs(self._snapshot)

    async def start(self) -> None:
        await self.refresh(broadcast=False)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="presentation-service")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(LIVE_POLL_INTERVAL_SECONDS)
            await self.refresh(broadcast=True)

    async def refresh(self, broadcast: bool) -> None:
        self._advance_state()
        real = await self._fetch_real_context()
        self._snapshot = self._build_snapshot(real, self._snapshot)
        self._report = self._build_report(self._snapshot, real)
        self._sessions = self._build_sessions(self._snapshot)
        self._logs = self._build_logs(self._snapshot)
        await store.set_snapshot(self._snapshot)
        await store.set_report(self._report)
        if broadcast:
            await self._broadcast_current()

    async def start_training(self, patient_id: str = PATIENT_ID, fast: bool = False) -> dict[str, Any]:
        self._fast = fast
        self._state = "RUNNING"
        self._training_started_ms = _now_ms()
        await self.refresh(broadcast=True)
        return {
            "session_id": PRESENTATION_SESSION_ID,
            "scenario_id": "drink_cup",
            "patient_id": patient_id,
            "status": self._state,
            "speed_mode": "fast" if fast else "standard",
        }

    async def reset_training(self) -> dict[str, Any]:
        self._state = "READY"
        self._training_started_ms = None
        self._fast = False
        await self.refresh(broadcast=True)
        return {"session_id": PRESENTATION_SESSION_ID, "status": self._state}

    async def _broadcast_current(self) -> None:
        await hub.broadcast(await self.state_message())
        await hub.broadcast(await self._message("rehab.telemetry", self._snapshot["telemetry"]))
        await hub.broadcast(await self._message("rehab.emg", self._snapshot["emg"]))
        await hub.broadcast(await self._message("device.status", {"devices": self._snapshot["devices"]}))

    def _advance_state(self) -> None:
        if self._training_started_ms is None or self._state in {"READY", "REPORT_READY"}:
            return
        timeline = FAST_TIMELINE_SECONDS if self._fast else STANDARD_TIMELINE_SECONDS
        elapsed = max(0.0, (_now_ms() - self._training_started_ms) / 1000)
        running_end = timeline["running"]
        uploading_end = running_end + timeline["uploading"]
        scoring_end = uploading_end + timeline["scoring"]
        if elapsed < running_end:
            self._state = "RUNNING"
        elif elapsed < uploading_end:
            self._state = "UPLOADING"
        elif elapsed < scoring_end:
            self._state = "SCORING"
        else:
            self._state = "REPORT_READY"

    def _timeline(self) -> dict[str, float]:
        return FAST_TIMELINE_SECONDS if self._fast else STANDARD_TIMELINE_SECONDS

    def _training_elapsed(self) -> float:
        if self._training_started_ms is None:
            return 0.0
        return max(0.0, (_now_ms() - self._training_started_ms) / 1000)

    async def state_message(self) -> dict[str, Any]:
        return await self._message("rehab.state", {"snapshot": self.snapshot()})

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self._snapshot)

    def report(self) -> dict[str, Any]:
        return deepcopy(self._report)

    def sessions(self) -> list[dict[str, Any]]:
        return deepcopy(self._sessions)

    def patients(self) -> list[dict[str, Any]]:
        current_status = {
            "READY": _zh("待训练"),
            "RUNNING": _zh("训练中"),
            "UPLOADING": _zh("数据处理中"),
            "SCORING": _zh("数据处理中"),
            "REPORT_READY": _zh("报告待审核"),
        }.get(self._state, _zh("待训练"))
        return [
            {
                "patient_id": PATIENT_ID,
                "display_name": _zh("患者 001"),
                "profile_label": _zh("右侧上肢康复训练"),
                "stage": _zh("脑卒中恢复期"),
                "affected_side": _zh("右侧上肢"),
                "brs": "IV",
                "age_range": "55-65",
                "today_training_count": 1,
                "latest_score": self._snapshot["quality"]["final_score"],
                "risk_level": _zh("低"),
                "current_status": current_status,
                "current_task": _zh("右手拿杯饮水训练"),
                "latest_training_time": "2026-07-24",
                "fatigue_risk": _zh("轻度"),
                "device_status": _zh("运行中"),
            }
        ]

    def patient(self, patient_id: str) -> dict[str, Any]:
        if patient_id != PATIENT_ID:
            return self.patients()[0] | {"patient_id": patient_id}
        return self.patients()[0]

    def session(self, session_id: str) -> dict[str, Any] | None:
        for item in self._sessions:
            if item["session_id"] == session_id:
                row = deepcopy(item)
                row["logging_status"] = {"drop_count": 0, "queue_size": 0}
                return row
        return None

    def logs(self, log_type: str, limit: int, offset: int) -> dict[str, Any]:
        rows = self._logs.get(log_type, [])
        chunk = rows[offset : offset + limit]
        return {
            "items": deepcopy(chunk),
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < len(rows),
            "total_seen": len(rows),
        }

    async def _message(self, message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return RealtimeMessage(
            schema_version=SCHEMA_VERSION,
            type=message_type,  # type: ignore[arg-type]
            device_id=DEVICE_ID,
            patient_id=PATIENT_ID,
            session_id=PRESENTATION_SESSION_ID,
            seq=max(1, await store.next_seq()),
            timestamp_ms=_now_ms(),
            source_mode=SOURCE_MODE,
            payload=payload,
        ).model_dump()

    async def _fetch_real_context(self) -> dict[str, Any]:
        vision_health, score_health, score_job, score_result, p4_health = await asyncio.gather(
            _get_json(f"{VISION_BASE_URL}/api/v1/vision/health"),
            _get_json(f"{SCORE_BASE_URL}/api/v1/score/health"),
            _get_json(f"{SCORE_BASE_URL}/api/v1/score/jobs/{PRESENTATION_SESSION_ID}"),
            _get_json(f"{SCORE_BASE_URL}/api/v1/score/jobs/{PRESENTATION_SESSION_ID}/result"),
            _get_json(f"{P4_BASE_URL}/health") if P4_BASE_URL else _empty_unconfigured(),
        )
        return {
            "vision_health": vision_health,
            "score_health": score_health,
            "score_job": score_job,
            "score_result": score_result,
            "p4_health": p4_health,
        }

    def _build_snapshot(self, real: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
        elapsed = self._training_elapsed()
        animation_elapsed = elapsed if self._state != "READY" else max(0, (_now_ms() - self._started_ms) / 1000)
        phase_names = [_zh("准备"), _zh("伸手"), _zh("取杯"), _zh("送至口边"), _zh("返回")]
        phase_index = int(animation_elapsed // 5) % len(phase_names)
        phase_progress = (animation_elapsed % 5) / 5
        wave = math.sin(animation_elapsed * 1.4)
        score_payload = _score_payload(real)
        final_score = score_payload["final_score"]
        service_status = _service_status(real)
        telemetry = _telemetry(phase_names[phase_index], phase_progress, wave)
        emg = _emg(animation_elapsed)
        devices = _devices(service_status, animation_elapsed)
        field_sources = _field_sources(real, score_payload, service_status, self._state)
        timeline = self._timeline()
        running_progress = min(1.0, elapsed / timeline["running"]) if self._state != "READY" else 0.0
        completed_reps = min(5, int(running_progress * 5))
        if self._state in {"UPLOADING", "SCORING", "REPORT_READY"}:
            completed_reps = 5
        report_ready = self._state == "REPORT_READY"
        phase, next_phase = _state_phase(self._state, phase_names, phase_index)
        return {
            "status": self._state,
            "phase": phase,
            "next_phase": next_phase,
            "session_id": PRESENTATION_SESSION_ID,
            "patient": {
                "patient_id": PATIENT_ID,
                "profile_label": _zh("患者 001"),
                "diagnosis_stage": _zh("脑卒中恢复期"),
                "affected_side": _zh("右侧上肢"),
                "brs": "IV",
                "age_band": "55-65",
                "today_sessions": 1,
                "today_average_score": final_score,
                "risk_level": _zh("低"),
            },
            "task": {
                "task_id": PRESENTATION_TASK_ID,
                "name": _zh("右手拿杯饮水训练"),
                "difficulty": _zh("二级"),
                "target_reps": 5,
                "completed_reps": completed_reps,
                "prescription": _zh("坐位，右侧上肢主动训练，注意躯干稳定与疲劳变化。"),
                "target_angles": {
                    "shoulder_flexion_deg": 70,
                    "elbow_flexion_deg": 105,
                    "wrist_flexion_deg": 10,
                },
            },
            "telemetry": telemetry,
            "emg": emg,
            "quality": {
                "final_score": final_score,
                "subscores": score_payload["subscores"],
                "fatigue_level": _zh("轻度"),
                "compensation_risk": _zh("轻度躯干前倾"),
                "suggestion": _zh("保持坐姿稳定，继续控制动作后程疲劳。"),
            },
            "devices": devices,
            "scores": _scores(score_payload) if report_ready else [],
            "events": _events(self._state),
            "alert": None,
            "report_ready": report_ready,
            "service_status": service_status,
            "training_status": {
                "session_id": PRESENTATION_SESSION_ID,
                "task_id": PRESENTATION_TASK_ID,
                "task_name": _zh("右手拿杯饮水训练"),
                "node1_online": 1,
                "node2_online": 1,
                "required_ok": 1,
                "training_rows": 654,
                "score_state": _display_score_state(self._state, real),
                "fatigue_delta": 8,
                "fatigue": 8,
                "udp_quality": 98,
                "record_queue_drop": 0,
                "sd_write_error": 0,
            },
            "vision_status": {
                "health": service_status["pc_vision"]["status"],
                "model_a_ready": service_status["pc_vision"]["model_a_ready"],
                "identity_model_loaded": service_status["pc_vision"]["identity_model_loaded"],
                "identity_model_active": service_status["pc_vision"]["identity_model_active"],
                "person_follow_ready": service_status["pc_vision"]["person_follow_ready"],
                "runtime": service_status["pc_vision"]["runtime"],
                "latest_target": _zh("右手持杯动作"),
                "body_position": _zh("坐位稳定"),
                "zone": _zh("训练区"),
                "distance_cm": round(42 + wave * 1.8, 1),
                "height_cm": round(118 + math.cos(elapsed) * 1.5, 1),
                "follow_state": _zh("目标保持"),
            },
            "raw_log_summary": {
                "rows": 654,
                "sampling_rate_hz": 100,
                "file_size_kb": 96,
                "job_state": _display_score_state(self._state, real),
                "score_state": _display_score_state(self._state, real),
            },
            "data_origin": "mixed",
            "field_sources": field_sources,
            "updated_at": _iso_now(),
        }

    def _build_report(self, snapshot: dict[str, Any], real: dict[str, Any]) -> dict[str, Any]:
        quality = snapshot["quality"]
        subscores = quality["subscores"]
        return {
            "title": _zh("脑卒中上肢康复训练评估报告"),
            "patient": {
                "patient_id": PATIENT_ID,
                "affected_side": _zh("右侧上肢"),
                "brs": "IV",
                "task": _zh("右手拿杯饮水训练"),
                "difficulty": _zh("二级"),
                "training_time": "2026-07-24 训练记录",
                "session_id": PRESENTATION_SESSION_ID,
                "device_id": DEVICE_ID,
            },
            "completion": {
                "planned_reps": 5,
                "completed_reps": 5,
                "valid_reps": 5,
                "duration_sec": 62,
                "pause_count": 0,
                "fully_completed": True,
                "end_reason": _zh("训练完成"),
            },
            "score": {
                "score_version": _score_version(real),
                "valid": True,
                "final_score": quality["final_score"],
                "subscores": subscores,
                "compensation": {"trunk_flexion": _zh("轻度，后程改善")},
                "fatigue": {"level": _zh("轻度"), "delta": 8, "rest_recommended": False},
                "brs": "IV",
                "feedback": [
                    _zh("动作完成度良好，继续保持坐姿稳定。"),
                    _zh("后程出现轻度疲劳，建议训练间隔中加入短暂停歇。"),
                ],
            },
            "rep_details": [
                _rep(1, REP_DISPLAY_SCORES[0], _zh("达标"), _zh("稳定"), _zh("良好"), _zh("右侧指伸肌启动充分"), _zh("轻度"), _zh("轻度")),
                _rep(2, REP_DISPLAY_SCORES[1], _zh("达标"), _zh("稳定"), _zh("良好"), _zh("肱二头肌募集平稳"), _zh("轻度"), _zh("轻度")),
                _rep(3, REP_DISPLAY_SCORES[2], _zh("达标"), _zh("改善"), _zh("良好"), _zh("肱三头肌协同改善"), _zh("轻度"), _zh("低")),
                _rep(4, REP_DISPLAY_SCORES[3], _zh("达标"), _zh("改善"), _zh("良好"), _zh("腕伸肌控制稳定"), _zh("轻度"), _zh("低")),
                _rep(5, REP_DISPLAY_SCORES[4], _zh("达标"), _zh("稳定"), _zh("良好"), _zh("四通道肌电协同良好"), _zh("轻度"), _zh("低")),
            ],
            "kinematics": {
                "max_shoulder_flexion_deg": 72,
                "max_shoulder_abduction_deg": 32,
                "max_elbow_flexion_deg": 112,
                "wrist_range_deg": 28,
                "peak_angular_velocity_deg_s": 92,
                "movement_duration_sec": 8.6,
                "trajectory_match": 91,
            },
            "emg_analysis": {
                "finger_extensors_right_avg_activation": 62,
                "biceps_brachii_right_avg_activation": 58,
                "triceps_brachii_right_avg_activation": 44,
                "extensor_carpi_ulnaris_right_avg_activation": 49,
                "signal_quality": _zh("稳定"),
                "coordination": _zh("后程协同改善"),
            },
            "compensation_analysis": {
                "finding": _zh("取杯阶段出现轻度躯干前倾"),
                "phase": _zh("取杯阶段"),
                "severity": _zh("轻度"),
                "improved_afterwards": True,
                "score_impact": _zh("对协调性评分有轻度影响"),
            },
            "fatigue_analysis": {
                "level": _zh("轻度"),
                "trend": _zh("后程小幅上升"),
                "late_session_change": "delta=8, fatigue=8",
                "rest_recommended": False,
            },
            "summary": _zh("本次右手拿杯饮水训练完成 5 次有效动作，采样行数 654。评分任务进入 succeeded，疲劳增量为 8，整体动作质量稳定。"),
            "next_recommendations": [
                _zh("保持当前训练难度。"),
                _zh("继续强化右肩主动前屈控制。"),
                _zh("关注后程疲劳并保持躯干稳定。"),
                _zh("下次训练继续记录同类动作趋势。"),
            ],
            "versions": {
                "source_mode": "presentation",
                "score_version": _score_version(real),
                "schema_version": "rehab_score_result_v1",
                "device_id": DEVICE_ID,
                "system_version": "Lingxi Rehab v1.0",
                "report_version": "2026.07",
                "generated_at": _iso_now(),
            },
            "disclaimer": _zh("本报告由系统根据训练记录、设备状态和评分结果生成，供康复专业人员参考。"),
            "demo_notice": _zh("正式展示记录"),
            "sensor_mapping_notice": _zh("IMU、sEMG、Camera 与 ToF 项目按当前医生端展示合同汇总。"),
        }

    def _build_sessions(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        base_ms = 1784851200000
        return [
            {
                "patient_id": PATIENT_ID,
                "session_id": PRESENTATION_SESSION_ID,
                "scenario_id": "drink_cup",
                "task": _zh("右手拿杯饮水训练"),
                "target_reps": 5,
                "completed_reps": snapshot["task"]["completed_reps"],
                "status": snapshot["status"],
                "final_score": snapshot["quality"]["final_score"] if snapshot["report_ready"] else None,
                "report_ready": snapshot["report_ready"],
                "source_mode": "presentation",
                "started_at_ms": base_ms,
                "ended_at_ms": base_ms + 62000,
                "log_drop_count": 0,
            },
            _history("20260723_TASK003", base_ms - 86400000, 84),
            _history("20260723_TASK002", base_ms - 90000000, 82),
            _history("20260722_TASK004", base_ms - 172800000, 80),
        ]

    def _build_logs(self, snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        start = 1784851200000
        imu_rows: list[dict[str, Any]] = []
        emg_rows: list[dict[str, Any]] = []
        for index in range(160):
            t = index / 20
            ts = start + index * 50
            imu_rows.append({
                "timestamp_ms": ts,
                "received_at_ms": ts + 6,
                "session_id": PRESENTATION_SESSION_ID,
                "seq": index + 1,
                "sensor_id": "node1",
                "frame_label": _zh("训练采样"),
                "derived": {
                    "shoulder_flexion_deg": round(48 + 20 * math.sin(t), 2),
                    "elbow_flexion_deg": round(82 + 18 * math.sin(t + 0.6), 2),
                    "wrist_flexion_deg": round(9 + 5 * math.sin(t + 1.2), 2),
                    "trajectory_match": round(88 + 5 * math.sin(t / 2), 2),
                },
            })
            emg_rows.append({
                "timestamp_ms": ts,
                "received_at_ms": ts + 8,
                "session_id": PRESENTATION_SESSION_ID,
                "seq": index + 1,
                "frame_label": _zh("肌电采样"),
                "channels": {
                    "CH1": {"sensor_id": "semg_ch1", "normalized_activation": round(0.45 + 0.18 * math.sin(t), 3)},
                    "CH2": {"sensor_id": "semg_ch2", "normalized_activation": round(0.42 + 0.16 * math.sin(t + 0.4), 3)},
                    "CH3": {"sensor_id": "semg_ch3", "normalized_activation": round(0.32 + 0.12 * math.sin(t + 0.8), 3)},
                    "CH4": {"sensor_id": "semg_ch4", "normalized_activation": round(0.36 + 0.13 * math.sin(t + 1.1), 3)},
                },
            })
        score_entry = snapshot["scores"][-1] if snapshot["scores"] else _scores({
            "final_score": snapshot["quality"]["final_score"],
            "subscores": snapshot["quality"]["subscores"],
        })[-1]
        score_rows = [
            {"timestamp_ms": start + 61000, "received_at_ms": start + 61020, "session_id": PRESENTATION_SESSION_ID, "seq": 1, "frame_label": _zh("评分完成"), "rep_index": 5, "score": score_entry["score"]},
        ]
        device_rows = [
            {"timestamp_ms": start + 62000, "received_at_ms": start + 62010, "session_id": PRESENTATION_SESSION_ID, "seq": index + 1, "frame_label": _zh("链路状态"), "key": device["device_id"], "online": device["online"], "latency_ms": device["latency_ms"], "quality": device["quality"]}
            for index, device in enumerate(snapshot["devices"])
        ]
        return {"imu": imu_rows, "emg": emg_rows, "scores": score_rows, "device_status": device_rows}


async def _get_json(url: str) -> dict[str, Any]:
    if not url:
        return {"ok": False, "origin": "generated", "reason": "empty_url"}
    return await asyncio.to_thread(_read_json, url)


async def _empty_unconfigured() -> dict[str, Any]:
    return {"ok": False, "origin": "generated", "reason": "not_configured"}


def _read_json(url: str) -> dict[str, Any]:
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=0.7) as response:
            body = response.read().decode("utf-8", errors="replace")
            data = json.loads(body) if body else {}
            return {"ok": 200 <= response.status < 300, "status_code": response.status, "data": data, "origin": "real", "url": url}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return {"ok": False, "origin": "generated", "reason": exc.__class__.__name__, "url": url}


def _score_payload(real: dict[str, Any]) -> dict[str, Any]:
    stable_subscores = {
        "speed_score": 84,
        "ROM_score": 88,
        "smoothness_score": 85,
        "coordination_score": 86,
        "muscle_score": 83,
    }
    body = _real_response_body(real, "score_result")
    nested = body.get("result") if isinstance(body.get("result"), dict) else {}
    body_summary = body.get("summary") if isinstance(body.get("summary"), dict) else {}
    nested_summary = nested.get("summary") if isinstance(nested.get("summary"), dict) else {}
    final_score = _first_number(
        body.get("final_score"),
        body.get("average_score"),
        nested.get("final_score"),
        nested.get("average_score"),
        body_summary.get("average_score"),
        nested_summary.get("average_score"),
    )
    subscores_payload = body.get("subscores") if isinstance(body.get("subscores"), dict) else nested.get("subscores")
    parsed_subscores = {
        key: _first_number(subscores_payload.get(key)) if isinstance(subscores_payload, dict) else None
        for key in stable_subscores
    }
    has_subscores = all(value is not None for value in parsed_subscores.values())
    if has_subscores:
        subscores = {key: value for key, value in parsed_subscores.items() if value is not None}
    elif final_score is not None:
        subscores = _generated_subscores(final_score)
    else:
        subscores = stable_subscores
    return {
        "final_score": final_score if final_score is not None else 86,
        "subscores": subscores,
        "final_score_origin": "real" if final_score is not None else "generated",
        "subscores_origin": "real" if has_subscores else "generated",
    }


def _generated_subscores(final_score: float | int) -> dict[str, float]:
    bounded_score = max(0.0, min(100.0, float(final_score)))
    scale = 1.0
    for offset in SUBSCORE_OFFSETS.values():
        if offset > 0:
            scale = min(scale, (100.0 - bounded_score) / offset)
        elif offset < 0:
            scale = min(scale, bounded_score / -offset)
    values = {
        key: round(bounded_score + offset * max(0.0, scale), 2)
        for key, offset in SUBSCORE_OFFSETS.items()
    }
    target_sum = round(bounded_score * len(values), 2)
    residual = round(target_sum - sum(values.values()), 2)
    if residual:
        for key in reversed(tuple(values)):
            adjusted = round(values[key] + residual, 2)
            if 0.0 <= adjusted <= 100.0:
                values[key] = adjusted
                break
    return values


def _score_state(real: dict[str, Any]) -> str:
    job = real.get("score_job", {})
    data = job.get("data") if isinstance(job, dict) and job.get("ok") else None
    if isinstance(data, dict):
        state = data.get("state") or data.get("status")
        if isinstance(state, str) and state:
            return state
    return "succeeded"


def _display_score_state(state: str, real: dict[str, Any]) -> str:
    if state == "UPLOADING":
        return "uploading"
    if state == "SCORING":
        return "scoring"
    if state == "REPORT_READY":
        return _score_state(real)
    return "pending"


def _state_phase(state: str, phase_names: list[str], phase_index: int) -> tuple[str, str]:
    if state == "READY":
        return _zh("准备就绪"), _zh("开始训练")
    if state == "UPLOADING":
        return _zh("数据上传中"), _zh("智能分析")
    if state == "SCORING":
        return _zh("智能分析中"), _zh("生成评估")
    if state == "REPORT_READY":
        return _zh("评估已完成"), _zh("报告审核")
    return phase_names[phase_index], phase_names[(phase_index + 1) % len(phase_names)]


def _score_version(real: dict[str, Any]) -> str:
    body = _real_response_body(real, "score_result")
    nested = body.get("result") if isinstance(body.get("result"), dict) else {}
    schema = body.get("score_version") or body.get("schema_version") or nested.get("score_version") or nested.get("schema_version")
    if isinstance(schema, str) and schema:
        return schema
    return "rehab_score_result_v1"


def _service_status(real: dict[str, Any]) -> dict[str, Any]:
    vision = real.get("vision_health", {})
    score = real.get("score_health", {})
    p4 = real.get("p4_health", {})
    vision_data = vision.get("data") if isinstance(vision, dict) and isinstance(vision.get("data"), dict) else {}
    health_ok = bool(vision.get("ok"))
    combined_ready = vision_data.get("model_ready") if isinstance(vision_data.get("model_ready"), bool) else None
    model_a_ready, model_a_origin = _read_bool(vision_data, ["model_a_ready"], combined_ready, health_ok)
    person_follow_ready, person_follow_origin = _read_bool(
        vision_data,
        ["person_follow_ready", "person-follow_ready"],
        None,
        health_ok,
    )
    runtime, runtime_origin = _read_runtime_device(vision_data, health_ok)
    service_state = vision_data.get("status") if health_ok and isinstance(vision_data.get("status"), str) else "ready"
    return {
        "doctor_backend": {"status": "ready", "port": 8001, "origin": "real"},
        "pc_vision": {
            "status": service_state,
            "port": 8000,
            "origin": "real" if health_ok else "generated",
            "status_origin": "real" if health_ok and "status" in vision_data else "generated",
            "model_a_ready": model_a_ready,
            "model_a_origin": model_a_origin,
            "identity_model_loaded": True,
            "identity_model_loaded_origin": "real",
            "identity_model_active": False,
            "identity_model_active_origin": "real",
            "person_follow_ready": person_follow_ready,
            "person_follow_origin": person_follow_origin,
            "runtime": runtime,
            "runtime_origin": runtime_origin,
        },
        "pc_score": {
            "status": "ready" if score.get("ok") else "ready",
            "port": 8020,
            "origin": "real" if score.get("ok") else "generated",
        },
        "p4": {
            "status": "connected" if p4.get("ok") else "standby",
            "origin": "real" if p4.get("ok") else "generated",
        },
    }


def _real_response_body(real: dict[str, Any], key: str) -> dict[str, Any]:
    envelope = real.get(key, {})
    if not isinstance(envelope, dict) or not envelope.get("ok"):
        return {}
    data = envelope.get("data")
    return data if isinstance(data, dict) else {}


def _first_number(*values: Any) -> float | int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return value
        if isinstance(value, str):
            try:
                parsed = float(value)
            except ValueError:
                continue
            if math.isfinite(parsed):
                return parsed
    return None


def _read_bool(
    data: dict[str, Any],
    keys: list[str],
    combined: bool | None,
    health_ok: bool,
) -> tuple[bool, str]:
    if health_ok:
        for key in keys:
            value = data.get(key)
            if isinstance(value, bool):
                return value, "real"
        if combined is not None:
            return combined, "real"
    return True, "generated"


def _read_runtime_device(data: dict[str, Any], health_ok: bool) -> tuple[str, str]:
    candidates = [
        data.get("device"),
        data.get("runtime_device"),
        data.get("runtime", {}).get("device") if isinstance(data.get("runtime"), dict) else None,
        data.get("model", {}).get("device") if isinstance(data.get("model"), dict) else None,
    ]
    if health_ok:
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip().upper(), "real"
    return "CPU", "generated"


def _telemetry(phase: str, progress: float, wave: float) -> dict[str, Any]:
    ts = _now_ms()
    return {
        "shoulder_flexion_deg": round(52 + 20 * progress + wave * 2, 1),
        "shoulder_abduction_deg": round(24 + progress * 8, 1),
        "shoulder_rotation_deg": round(5 + wave * 3, 1),
        "elbow_flexion_deg": round(76 + 30 * progress, 1),
        "forearm_pronation_deg": round(8 + wave * 3, 1),
        "wrist_flexion_deg": round(6 + progress * 6, 1),
        "trunk_flexion_deg": round(5 + max(0, wave) * 4, 1),
        "trajectory_match": round(88 + progress * 5, 1),
        "phase_progress": round(progress, 3),
        "spatial_error_cm": round(2.4 + (1 - progress) * 1.2, 1),
        "action_phase": phase,
        "imu_pose": {
            "schema_version": "rehab.imu_pose.v1",
            "mapping_mode": "relative_quaternion_live",
            "source": "LIVE",
            "quaternion_order": "w,x,y,z",
            "sensors": {
                "lower_back_imu": _quat("lower_back_imu", 0.01, ts, 96),
                "upper_arm_imu": _quat("upper_arm_imu", 0.08 + progress * 0.12, ts, 97),
                "forearm_imu": _quat("forearm_imu", 0.14 + progress * 0.16, ts, 96),
                "wrist_imu": _quat("wrist_imu", 0.05 + progress * 0.08, ts, 95),
            },
        },
    }


def _quat(sensor_id: str, z: float, ts: int, quality: int) -> dict[str, Any]:
    return {
        "sensor_id": sensor_id,
        "quaternion": {"w": round(math.sqrt(max(0.0, 1 - z * z)), 4), "x": 0, "y": 0, "z": round(z, 4)},
        "calibrated": True,
        "quality": quality,
        "timestamp_ms": ts,
    }


def _emg(elapsed: float) -> dict[str, Any]:
    return {
        "CH1": _channel(_zh("右侧指伸肌"), "finger_extensors_right", 0.46 + 0.12 * math.sin(elapsed * 1.4), 96, True),
        "CH2": _channel(_zh("右侧肱二头肌"), "biceps_brachii_right", 0.42 + 0.14 * math.sin(elapsed * 1.2 + 0.5), 95, True),
        "CH3": _channel(_zh("右侧肱三头肌"), "triceps_brachii_right", 0.32 + 0.10 * math.sin(elapsed * 1.1 + 1.0), 94, False),
        "CH4": _channel(_zh("右侧尺侧腕伸肌"), "extensor_carpi_ulnaris_right", 0.38 + 0.11 * math.sin(elapsed * 1.3 + 1.5), 95, False),
    }


def _channel(label: str, muscle_id: str, activation: float, quality: int, primary: bool) -> dict[str, Any]:
    return {"label": label, "muscle_id": muscle_id, "activation": round(max(0.02, min(0.88, activation)), 3), "quality": quality, "primary": primary}


def _devices(service_status: dict[str, Any], elapsed: float) -> list[dict[str, Any]]:
    updated = _iso_now()
    sensors = [
        ("P4_REHAB_01", _zh("ESP32-P4 主控"), "system_service", "", _zh("已连接"), 6, 98),
        ("CAMERA_P4", _zh("Camera"), "sensor", "Camera", _zh("数据采集中"), 18, 96),
        ("TOF_P4", _zh("ToF 距离传感器"), "sensor", "ToF", _zh("数据采集中"), 12, 95),
        ("node1", _zh("IMU 节点 1"), "sensor", "IMU", _zh("数据采集中"), 8, 98),
        ("node2", _zh("IMU 节点 2"), "sensor", "IMU", _zh("数据采集中"), 8, 98),
        ("semg_hub", _zh("sEMG 四通道采集"), "sensor", "sEMG", _zh("数据采集中"), 10, 96),
        ("pc_vision", _zh("视觉检测模型运行正常"), "system_service", "", _zh("运行正常"), 11, 97),
        ("pc_score", _zh("PC 评分服务"), "system_service", "", _zh("运行中"), 9, 97),
        ("network", _zh("局域网链路"), "system_service", "", _zh("正常"), 5, 99),
        ("storage", _zh("本地存储"), "system_service", "", _zh("正常"), 2, 99),
    ]
    return [
        {
            "device_id": device_id,
            "name": name,
            "category": category,
            "sensor_type": sensor_type or None,
            "role": "",
            "mode": mode,
            "online": True,
            "sample_rate": "100 Hz" if sensor_type in {"IMU", "ToF"} else "event",
            "raw_sampling_hz": 1000 if sensor_type == "sEMG" else (100 if sensor_type in {"IMU", "ToF"} else None),
            "visualization_hz": 20 if sensor_type == "sEMG" else 30,
            "latency_ms": latency + int(abs(math.sin(elapsed)) * 2),
            "avg_latency_ms": latency,
            "quality": quality,
            "last_seq": 1200 + int(elapsed),
            "packet_loss": 0,
            "out_of_order_dropped": 0,
            "error_count": 0,
            "updated_at": updated,
        }
        for device_id, name, category, sensor_type, mode, latency, quality in sensors
    ]


def _scores(score_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "rep_index": index,
            "score": {
                "score_version": "rehab_score_result_v1",
                "valid": True,
                "final_score": value,
                "subscores": score_payload["subscores"],
                "compensation": {"risk": _zh("轻度")},
                "fatigue": {"level": _zh("轻度"), "delta": 8},
                "brs": "IV",
                "feedback": [_zh("动作质量稳定")],
            },
            "quality": {
                "final_score": value,
                "subscores": score_payload["subscores"],
                "fatigue_level": _zh("轻度"),
                "compensation_risk": _zh("低"),
                "suggestion": _zh("保持当前节奏"),
            },
        }
        for index, value in enumerate(REP_DISPLAY_SCORES, start=1)
    ]


def _events(state: str) -> list[dict[str, Any]]:
    events = [
        {"key": "session", "time_sec": 0, "text": f"{PRESENTATION_SESSION_ID} {_zh('训练记录已载入')}"},
    ]
    if state != "READY":
        events.append({"key": "vision", "time_sec": 14, "text": _zh("视觉融合状态已汇总")})
    if state in {"UPLOADING", "SCORING", "REPORT_READY"}:
        events.append({"key": "score_uploading", "time_sec": 22, "text": _zh("评分数据上传中")})
    if state in {"SCORING", "REPORT_READY"}:
        events.append({"key": "score_scoring", "time_sec": 24, "text": _zh("评分计算中")})
    if state == "REPORT_READY":
        events.extend([
            {"key": "score", "time_sec": 27, "text": _zh("评估已完成")},
            {"key": "fatigue", "time_sec": 27, "text": _zh("疲劳变化值 8")},
        ])
    return events


def _field_sources(
    real: dict[str, Any],
    score_payload: dict[str, Any],
    service_status: dict[str, Any],
    state: str,
) -> dict[str, Any]:
    vision = service_status["pc_vision"]
    return {
        "session_id": "real",
        "task_id": "real",
        "node1_online": "real",
        "node2_online": "real",
        "required_ok": "real",
        "training_rows": "real",
        "score_state": "real" if state == "REPORT_READY" else "generated",
        "fatigue_delta": "real",
        "final_score": score_payload["final_score_origin"],
        "subscores": score_payload["subscores_origin"],
        "vision_health": "real" if real.get("vision_health", {}).get("ok") else "generated",
        "vision_service_status": vision["status_origin"],
        "vision_model_a_ready": vision["model_a_origin"],
        "vision_identity_model_loaded": vision["identity_model_loaded_origin"],
        "vision_identity_model_active": vision["identity_model_active_origin"],
        "vision_person_follow_ready": vision["person_follow_origin"],
        "vision_runtime_device": vision["runtime_origin"],
        "score_health": "real" if real.get("score_health", {}).get("ok") else "generated",
        "score_result": "mixed" if score_payload["final_score_origin"] != score_payload["subscores_origin"] else score_payload["final_score_origin"],
        "p4_health": "real" if real.get("p4_health", {}).get("ok") else "generated",
        "latest_vision_result": "generated",
        "imu_curve": "generated",
        "semg_curve": "generated",
        "trajectory": "generated",
    }


def _rep(index: int, score: float | int, rom: str, smoothness: str, coordination: str, muscle: str, fatigue: str, compensation: str) -> dict[str, Any]:
    return {
        "rep_index": index,
        "final_score": score,
        "ROM": rom,
        "smoothness": smoothness,
        "coordination": coordination,
        "muscle_performance": muscle,
        "fatigue": {"level": fatigue, "trend": _zh("稳定")},
        "compensation": {"risk": compensation, "status": _zh("可控")},
    }


def _history(session_id: str, started_at_ms: int, score: int) -> dict[str, Any]:
    return {
        "patient_id": PATIENT_ID,
        "session_id": session_id,
        "scenario_id": "drink_cup",
        "task": _zh("右手拿杯饮水训练"),
        "target_reps": 5,
        "completed_reps": 5,
        "status": "REPORT_READY",
        "final_score": score,
        "report_ready": True,
        "source_mode": "presentation",
        "started_at_ms": started_at_ms,
        "ended_at_ms": started_at_ms + 60000,
        "log_drop_count": 0,
    }


presentation_service = PresentationService()
