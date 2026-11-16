from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import PATIENT_ID


def idle_snapshot() -> dict[str, Any]:
    return {
        "status": "IDLE",
        "phase": "准备",
        "next_phase": "伸手",
        "session_id": "SESSION_IDLE",
        "patient": {
            "patient_id": PATIENT_ID,
            "profile_label": "演示档案",
            "diagnosis_stage": "脑卒中恢复期",
            "affected_side": "右侧上肢",
            "brs": "IV期",
            "age_band": "55-65岁",
            "today_sessions": 2,
            "today_average_score": 82,
            "risk_level": "低",
        },
        "task": {
            "task_id": "DRINK_CUP",
            "name": "拿杯喝水",
            "difficulty": "中低",
            "target_reps": 5,
            "completed_reps": 0,
            "prescription": "坐位，右侧上肢主动辅助训练，注意躯干稳定。",
            "target_angles": {
                "shoulder_flexion_deg": 70,
                "elbow_flexion_deg": 105,
                "wrist_flexion_deg": 10,
            },
        },
        "telemetry": {
            "shoulder_flexion_deg": 0,
            "shoulder_abduction_deg": 0,
            "shoulder_rotation_deg": 0,
            "elbow_flexion_deg": 8,
            "forearm_pronation_deg": 0,
            "wrist_flexion_deg": 0,
            "trunk_flexion_deg": 0,
            "trajectory_match": 0,
            "phase_progress": 0,
            "spatial_error_cm": 0,
        },
        "emg": {
            "CH1": {"label": "右侧指伸肌", "muscle_id": "finger_extensors_right", "activation": 0.04, "quality": 96, "primary": False},
            "CH2": {"label": "右侧肱二头肌", "muscle_id": "biceps_brachii_right", "activation": 0.03, "quality": 95, "primary": False},
            "CH3": {"label": "右侧肱三头肌", "muscle_id": "triceps_brachii_right", "activation": 0.02, "quality": 93, "primary": False},
            "CH4": {"label": "右侧尺侧腕伸肌", "muscle_id": "extensor_carpi_ulnaris_right", "activation": 0.02, "quality": 94, "primary": False},
        },
        "quality": {
            "final_score": 0,
            "subscores": {
                "speed_score": 0,
                "ROM_score": 0,
                "smoothness_score": 0,
                "coordination_score": 0,
                "muscle_score": 0,
            },
            "fatigue_level": "无",
            "compensation_risk": "低",
            "suggestion": "等待患者端开始训练。",
        },
        "devices": [],
        "scores": [],
        "events": [],
        "alert": None,
        "report_ready": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@dataclass
class SessionStore:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    snapshot: dict[str, Any] = field(default_factory=idle_snapshot)
    seq: int = 0
    active_task: asyncio.Task | None = None
    report: dict[str, Any] | None = None

    async def reset(self) -> None:
        async with self.lock:
            self.snapshot = idle_snapshot()
            self.seq = 0
            self.report = None

    async def next_seq(self) -> int:
        async with self.lock:
            self.seq += 1
            return self.seq

    async def set_snapshot(self, patch: dict[str, Any]) -> dict[str, Any]:
        async with self.lock:
            self.snapshot.update(patch)
            self.snapshot["updated_at"] = datetime.now(timezone.utc).isoformat()
            return deepcopy(self.snapshot)

    async def get_snapshot(self) -> dict[str, Any]:
        async with self.lock:
            return deepcopy(self.snapshot)

    async def set_report(self, report: dict[str, Any]) -> None:
        async with self.lock:
            self.report = deepcopy(report)

    async def get_report(self) -> dict[str, Any] | None:
        async with self.lock:
            return deepcopy(self.report)


store = SessionStore()
