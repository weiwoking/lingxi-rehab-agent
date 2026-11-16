from __future__ import annotations

import math
import statistics
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .device_history_store import device_history_store
from .task_library_service import SUPPORTED_TASKS as TASK_REGISTRY

SUPPORTED_TASKS = {
    task_id: str(task["display_name"])
    for task_id, task in TASK_REGISTRY.items()
    if task.get("ui_enabled") is True
}
EXCLUDED_UNCONFIRMED_SCORE = 55.84


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _finite_score(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    return score if math.isfinite(score) and 0 <= score <= 100 else None


def _task_id(session: dict[str, Any], report: dict[str, Any]) -> str:
    for value in (
        session.get("task_id"),
        session.get("scenario_id"),
        session.get("task"),
        session.get("task_name"),
        session.get("internal_score_task_name"),
    ):
        if isinstance(value, str) and value in SUPPORTED_TASKS:
            return value

    patient = report.get("patient")
    task_name = patient.get("task") if isinstance(patient, dict) else None
    if not isinstance(task_name, str):
        task_name = session.get("task_name") or session.get("task")
    if not isinstance(task_name, str):
        return ""

    # Compatibility only: new sessions should already persist registry task_id.
    if "横向" in task_name and "球" in task_name:
        return "球类横向取放训练（一级）"
    if "横向" in task_name and ("水" in task_name or "杯" in task_name):
        return "水杯横向取放训练（一级）"
    if "饮水" in task_name or "喝水" in task_name or "水杯" in task_name:
        return "水杯拿取与饮水训练（二级）"
    return ""


def _completed_at(
    session: dict[str, Any], report: dict[str, Any], score: dict[str, Any]
) -> tuple[str, float]:
    for value in (
        score.get("completed_at"),
        (report.get("versions") or {}).get("generated_at"),
    ):
        if isinstance(value, str) and value:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return value, parsed.timestamp()
            except ValueError:
                pass

    timestamp_ms = session.get("ended_at_ms") or session.get("started_at_ms")
    if isinstance(timestamp_ms, (int, float)) and not isinstance(timestamp_ms, bool):
        timestamp = float(timestamp_ms) / 1000
        completed = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        return completed, timestamp
    return "", 0


def _formal_scores() -> list[dict[str, Any]]:
    sessions, reports = device_history_store.load()
    records: list[dict[str, Any]] = []
    for session_id, session in sessions.items():
        if session_id.startswith("SESSION_DEMO_"):
            continue
        report = reports.get(session_id)
        if not isinstance(report, dict):
            continue
        session_dir = device_history_store.session_directory(session_id)
        if not (session_dir / "scoring" / "full_result.json").is_file():
            continue
        if not (session_dir / "doctor" / "report.json").is_file():
            continue
        score = report.get("score")
        if not isinstance(score, dict):
            continue
        final_score = _finite_score(score.get("final_score"))
        task_id = _task_id(session, report)
        registry_task = TASK_REGISTRY.get(task_id, {})
        if (
            task_id not in SUPPORTED_TASKS
            or registry_task.get("formal_score_supported") is not True
            or registry_task.get("showcase_only") is True
            or session.get("formal") is False
            or session.get("prediction_eligible") is False
            or session.get("record_kind") == "showcase"
            or session.get("source_mode") != "device"
            or session.get("report_ready") is not True
            or score.get("valid") is not True
            or session.get("source_origin") != "pc_udp_6006_ingest"
            or session.get("status") != "COMPLETED"
            or not score.get("score_version")
            or final_score is None
            or abs(final_score - EXCLUDED_UNCONFIRMED_SCORE) < 0.005
        ):
            continue
        completed_at, sort_time = _completed_at(session, report, score)
        records.append(
            {
                "session_id": session_id,
                "task_id": task_id,
                "task_name": str(
                    (report.get("patient") or {}).get("task")
                    or session.get("task_name")
                    or session.get("task")
                    or ""
                ),
                "final_score": final_score,
                "completed_at": completed_at,
                "source_mode": "device",
                "valid": True,
                "_sort_time": sort_time,
            }
        )
    records.sort(key=lambda item: (item["_sort_time"], item["session_id"]), reverse=True)
    return records


def _base_and_trend(records: list[dict[str, Any]]) -> tuple[float, float]:
    scores = [record["final_score"] for record in records[:3]]
    if len(scores) >= 3:
        base = 0.50 * scores[0] + 0.30 * scores[1] + 0.20 * scores[2]
        trend = _clamp(
            0.35 * (scores[0] - scores[1])
            + 0.15 * (scores[1] - scores[2]),
            -3.0,
            3.0,
        )
    elif len(scores) == 2:
        base = 0.65 * scores[0] + 0.35 * scores[1]
        trend = _clamp(0.35 * (scores[0] - scores[1]), -3.0, 3.0)
    else:
        base = scores[0]
        trend = 0.0
    return base, trend


def _half_width(records: list[dict[str, Any]]) -> float:
    scores = [record["final_score"] for record in records[:3]]
    if len(scores) == 1:
        return 8.0
    if len(scores) == 2:
        return 6.0
    return _clamp(1.5 * statistics.stdev(scores), 3.5, 8.0)


def _future_scores(base: float, trend: float) -> list[float]:
    cumulative = 0.0
    results = []
    for index in range(5):
        cumulative += 0.65**index
        results.append(round(_clamp(base + trend * cumulative, 0, 100), 2))
    return results


def predict_next_score(task_id: str) -> dict[str, Any]:
    if task_id not in SUPPORTED_TASKS:
        raise ValueError("Unsupported task_id.")

    all_records = _formal_scores()
    same_task = [record for record in all_records if record["task_id"] == task_id]
    selected = same_task[:3]
    if not selected:
        return {
            "available": False,
            "task_id": task_id,
            "task_name": SUPPORTED_TASKS[task_id],
            "reason": "no_valid_formal_scores",
            "same_task_sample_count": 0,
            "total_real_sample_count": len(all_records),
            "source_sessions": [],
        }

    base, trend = _base_and_trend(selected)
    predicted = _clamp(base + trend, 0, 100)
    width = _half_width(selected)
    confidence = (
        "low"
        if len(same_task) == 1
        else "medium"
        if len(same_task) == 2
        else "higher"
    )
    source_sessions = []
    for record in selected:
        source = deepcopy(record)
        source.pop("_sort_time", None)
        source_sessions.append(source)

    return {
        "available": True,
        "task_id": task_id,
        "task_name": SUPPORTED_TASKS[task_id],
        "predicted_score": round(predicted, 2),
        "interval": {
            "lower": round(_clamp(predicted - width, 0, 100), 2),
            "upper": round(_clamp(predicted + width, 0, 100), 2),
        },
        "confidence": confidence,
        "basis_type": "same_task",
        "same_task_sample_count": len(same_task),
        "total_real_sample_count": len(all_records),
        "base_score": round(base, 2),
        "trend_adjustment": round(trend, 2),
        "future_scores": _future_scores(base, trend),
        "source_sessions": source_sessions,
    }
