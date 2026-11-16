from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Response

from .config import TRAINING_DATA_RESOLVER
from .patient_service import validate_patient
from .raw_log_service import LOG_FILES, raw_log_service

MAX_LIMIT = 500


def safe_session_dir(patient_id: str, session_id: str) -> Path:
    validate_patient(patient_id)
    if not session_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid session_id.")
    base = TRAINING_DATA_RESOLVER.doctor_sessions_root(patient_id)
    path = TRAINING_DATA_RESOLVER.resolve_doctor_session(patient_id, session_id).resolve()
    if not str(path).startswith(str(base.resolve())):
        raise HTTPException(status_code=400, detail="Invalid session path.")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Session not found.")
    return path


def list_sessions(patient_id: str) -> list[dict[str, Any]]:
    validate_patient(patient_id)
    base = TRAINING_DATA_RESOLVER.doctor_sessions_root(patient_id)
    if not base.exists():
        return []
    sessions: list[dict[str, Any]] = []
    for session_file in sorted(base.glob("*/session.json"), reverse=True):
        sessions.append(json.loads(session_file.read_text(encoding="utf-8")))
    return sessions


def get_report(patient_id: str, session_id: str) -> dict[str, Any]:
    path = safe_session_dir(patient_id, session_id) / "doctor" / "report.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found for this session.")
    return json.loads(path.read_text(encoding="utf-8"))


def get_session(patient_id: str, session_id: str) -> dict[str, Any]:
    path = safe_session_dir(patient_id, session_id) / "session.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Session metadata not found.")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["logging_status"] = raw_log_service.logging_status()
    return data


def read_logs(
    patient_id: str,
    session_id: str,
    log_type: str = "imu",
    sensor_id: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    if log_type not in LOG_FILES:
        raise HTTPException(status_code=400, detail="Invalid log type.")
    limit = min(max(limit, 1), MAX_LIMIT)
    offset = max(offset, 0)
    file_path = (
        safe_session_dir(patient_id, session_id)
        / "doctor"
        / "logs"
        / LOG_FILES[log_type]
    )
    if not file_path.exists():
        return {"items": [], "limit": limit, "offset": offset, "has_more": False, "total_seen": 0}
    items: list[dict[str, Any]] = []
    matched = 0
    with file_path.open("r", encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            if sensor_id:
                if log_type == "emg":
                    channels = record.get("channels", {})
                    if not any(data.get("sensor_id") == sensor_id for data in channels.values()):
                        continue
                elif record.get("sensor_id") != sensor_id:
                    continue
            ts = int(record.get("timestamp_ms", 0))
            if start_ms is not None and ts < start_ms:
                continue
            if end_ms is not None and ts > end_ms:
                continue
            if matched >= offset and len(items) < limit:
                items.append(record)
            matched += 1
            if matched >= offset + limit + 1:
                break
    return {"items": items, "limit": limit, "offset": offset, "has_more": matched > offset + len(items), "total_seen": matched}


def export_logs(patient_id: str, session_id: str, log_type: str, fmt: str) -> Response:
    if fmt not in {"csv", "jsonl"}:
        raise HTTPException(status_code=400, detail="format must be csv or jsonl.")
    result = read_logs(patient_id, session_id, log_type=log_type, limit=MAX_LIMIT, offset=0)
    filename = f"{patient_id}_{session_id}_{log_type}.{fmt}"
    if fmt == "jsonl":
        content = "\n".join(json.dumps(item, ensure_ascii=False) for item in result["items"]) + ("\n" if result["items"] else "")
        return Response(content, media_type="application/x-ndjson", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["timestamp_ms", "received_at_ms", "session_id", "seq", "type", "sensor_id", "key_values", "source_mode"])
    writer.writeheader()
    for item in result["items"]:
        writer.writerow(flatten_log_row(log_type, item))
    return Response(output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def flatten_log_row(log_type: str, item: dict[str, Any]) -> dict[str, Any]:
    if log_type == "imu":
        key_values = item.get("derived", {})
    elif log_type == "emg":
        key_values = {ch: data.get("normalized_activation") for ch, data in item.get("channels", {}).items()}
    elif log_type == "scores":
        key_values = {"final_score": item.get("score", {}).get("final_score"), "rep_index": item.get("rep_index")}
    elif log_type == "device_status":
        key_values = {"online": item.get("online"), "latency_ms": item.get("latency_ms"), "quality": item.get("quality")}
    else:
        key_values = {"text": item.get("text") or item.get("title")}
    return {
        "timestamp_ms": item.get("timestamp_ms"),
        "received_at_ms": item.get("received_at_ms"),
        "session_id": item.get("session_id"),
        "seq": item.get("seq"),
        "type": log_type,
        "sensor_id": item.get("sensor_id", item.get("key", "")),
        "key_values": json.dumps(key_values, ensure_ascii=False),
        "source_mode": item.get("source_mode"),
    }
