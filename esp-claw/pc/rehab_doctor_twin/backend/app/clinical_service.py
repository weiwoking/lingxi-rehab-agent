from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DATA_DIR, PATIENT_ID
from .real_runtime_service import real_runtime_service

CLINICAL_DIR = DATA_DIR / "clinical"
PRESCRIPTION_FILE = CLINICAL_DIR / f"{PATIENT_ID}_prescription.json"
ALLOWED_TASKS = {
    "drink_water_right": "\u53f3\u624b\u62ff\u676f\u996e\u6c34",
    "pick_book_right": "\u53f3\u624b\u62ff\u4e66",
}


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _default_prescription() -> dict[str, Any]:
    return {
        "patient_id": PATIENT_ID,
        "affected_side": "right",
        "affected_side_label": "\u53f3\u4fa7\u4e0a\u80a2",
        "brs": "BRS IV",
        "training_goal": "\u6062\u590d\u53f3\u4fa7\u4e0a\u80a2\u65e5\u5e38\u4efb\u52a1\u80fd\u529b",
        "allowed_difficulty": [1, 2],
        "max_reach_height_cm": 90,
        "prohibited_task_types": [],
        "monitoring_focus": [
            "\u8bbe\u5907\u8fde\u63a5",
            "\u4f20\u611f\u5668\u6570\u636e\u8d28\u91cf",
            "\u8bc4\u5206\u7ed3\u679c",
        ],
        "enabled_tasks": list(ALLOWED_TASKS),
        "valid_from": "",
        "valid_until": "",
        "training_frequency": "",
        "target_reps": 0,
        "enabled": True,
        "version": "RX-P0217-1",
        "updated_at": _now_text(),
        "source": "live",
        "requires_per_session_approval": False,
    }


def get_prescription() -> dict[str, Any]:
    if not PRESCRIPTION_FILE.exists():
        return _default_prescription()
    try:
        value = json.loads(PRESCRIPTION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _default_prescription()
    return value if isinstance(value, dict) else _default_prescription()


def save_prescription(value: dict[str, Any]) -> dict[str, Any]:
    current = get_prescription()
    allowed = {
        "affected_side",
        "affected_side_label",
        "brs",
        "training_goal",
        "allowed_difficulty",
        "max_reach_height_cm",
        "prohibited_task_types",
        "monitoring_focus",
        "enabled_tasks",
        "valid_from",
        "valid_until",
        "training_frequency",
        "target_reps",
        "enabled",
        "version",
    }
    current.update({key: deepcopy(value[key]) for key in allowed if key in value})
    enabled = current.get("enabled_tasks")
    if not isinstance(enabled, list) or any(item not in ALLOWED_TASKS for item in enabled):
        raise ValueError("enabled_tasks contains an unsupported task")
    current.update(
        {
            "patient_id": PATIENT_ID,
            "source": "live",
            "updated_at": _now_text(),
            "requires_per_session_approval": False,
        }
    )
    CLINICAL_DIR.mkdir(parents=True, exist_ok=True)
    PRESCRIPTION_FILE.write_text(
        json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return deepcopy(current)


def task_decision(session_id: str) -> dict[str, Any]:
    prescription = get_prescription()
    snapshot = real_runtime_service.snapshot()
    task = snapshot.get("task") if snapshot.get("session_id") == session_id else {}
    task = task if isinstance(task, dict) else {}
    task_id = str(task.get("task_id") or "")
    task_name = str(task.get("name") or "")
    enabled = task_id in prescription.get("enabled_tasks", [])
    return {
        "patient_id": PATIENT_ID,
        "session_id": session_id,
        "task_id": task_id,
        "task_name": task_name,
        "selected_difficulty": 0,
        "training_objective": prescription["training_goal"],
        "monitoring_focus": "\u3001".join(prescription["monitoring_focus"]),
        "selection_basis": (
            "\u5f53\u524d P4 \u4e0a\u62a5\u4efb\u52a1\u5df2\u5728\u533b\u5631\u4e2d\u542f\u7528"
            if enabled
            else "\u5f53\u524d P4 \u4efb\u52a1\u672a\u80fd\u4e0e\u5df2\u542f\u7528\u533b\u5631\u5339\u914d"
        ),
        "prescription_version": prescription["version"],
        "candidates": [],
        "source": "live",
        "requires_per_session_approval": False,
    }


def safety_interventions(session_id: str) -> list[dict[str, Any]]:
    del session_id
    return []


def training_trend(limit: int) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    for session in reversed(real_runtime_service.sessions()):
        score = session.get("final_score")
        if not isinstance(score, (int, float)):
            continue
        points.append(
            {
                "session_id": session["session_id"],
                "date_label": datetime.fromtimestamp(
                    session["started_at_ms"] / 1000
                ).strftime("%m/%d"),
                "overall_score": score,
                "ROM_score": 0,
                "smoothness_score": 0,
                "compensation_count": 0,
                "duration_sec": round(float(session.get("duration_ms") or 0) / 1000, 3),
            }
        )
    return {"patient_id": PATIENT_ID, "points": points[-limit:], "source": "live"}


def _review_file(session_id: str) -> Path:
    return CLINICAL_DIR / f"{PATIENT_ID}_{session_id}_review.json"


def get_review(session_id: str) -> dict[str, Any]:
    path = _review_file(session_id)
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, ValueError):
            pass
    return {
        "patient_id": PATIENT_ID,
        "session_id": session_id,
        "agent_suggestion": "\u7b49\u5f85\u771f\u5b9e\u8bc4\u5206\u7ed3\u679c",
        "suggestion_notice": "\u7cfb\u7edf\u4e0d\u4f1a\u81ea\u52a8\u4fee\u6539\u533b\u751f\u5904\u65b9\u3002",
        "decision": "pending",
        "doctor_note": "",
        "confirmed_plan": "",
        "updated_at": _now_text(),
        "source": "live",
    }


def save_review(session_id: str, value: dict[str, Any]) -> dict[str, Any]:
    current = get_review(session_id)
    for key in ("decision", "doctor_note", "confirmed_plan"):
        if key in value:
            current[key] = value[key]
    current.update(
        {
            "patient_id": PATIENT_ID,
            "session_id": session_id,
            "source": "live",
            "updated_at": _now_text(),
        }
    )
    CLINICAL_DIR.mkdir(parents=True, exist_ok=True)
    _review_file(session_id).write_text(
        json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return deepcopy(current)
