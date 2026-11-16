from __future__ import annotations

import csv
import io
import json
import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from fastapi import Body, FastAPI, HTTPException, Query, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from .config import APP_VERSION, DEVICE_ID, PATIENT_ID, SCHEMA_VERSION, SERVICE_NAME, SOURCE_MODE
from .config import PROJECT_ROOT
from .competition_demo_service import CompetitionDemoRequest, start_competition_demo
from .clinical_service import (
    get_prescription,
    get_review,
    safety_interventions,
    save_prescription,
    save_review,
    task_decision,
    training_trend,
)
from .cloud_sync_service import cloud_sync_service
from .demo_engine import engine
from .device_history_store import device_history_store
from .log_export_service import export_logs, get_report, get_session, list_sessions, read_logs
from .models import RealtimeMessage, StartDemoRequest
from .patient_service import get_patient, list_patients
from .presentation_service import PRESENTATION_SESSION_ID, presentation_service
from .raw_log_service import raw_log_service
from .scenario_loader import list_scenarios
from .score_prediction_service import SUPPORTED_TASKS, predict_next_score
from .session_v12_scoring_service import session_v12_scoring_service
from .session_store import store
from .task_library_service import task_library_service
from .websocket_hub import hub

pc_local_runtime_service = None
real_runtime_service = None
if SOURCE_MODE == "pc_local":
    from .pc_local_runtime_service import pc_local_runtime_service
elif SOURCE_MODE == "device":
    from .real_runtime_service import real_runtime_service

app = FastAPI(title="灵犀智伴医生端数字孪生网关", version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def active_runtime_service():
    if SOURCE_MODE == "pc_local":
        return pc_local_runtime_service
    if SOURCE_MODE == "device":
        return real_runtime_service
    return None


SESSION_V12_LIST_STATUS_VALUES = {"not_started", "pending", "running", "completed", "failed"}


def _with_session_v12_status(session: dict) -> dict:
    value = deepcopy(session)
    existing_status = value.get("session_v12_status")
    if existing_status in SESSION_V12_LIST_STATUS_VALUES:
        value["session_v12_status"] = existing_status
        value["session_v12_error"] = value.get("session_v12_error")
        value["session_v12_baseline_version"] = value.get("session_v12_baseline_version")
        return value
    session_id = str(value.get("session_id") or "")
    status = session_v12_scoring_service.status(session_id)
    value["session_v12_status"] = status.get("status")
    value["session_v12_error"] = status.get("error")
    value["session_v12_baseline_version"] = status.get("baseline_version")
    return value


async def snapshot_message() -> dict:
    if SOURCE_MODE == "presentation":
        return await presentation_service.state_message()
    runtime = active_runtime_service()
    if runtime is not None:
        return await runtime.state_message()
    snapshot = await store.get_snapshot()
    return RealtimeMessage(
        schema_version=SCHEMA_VERSION,
        type="rehab.state",
        device_id=DEVICE_ID,
        patient_id=PATIENT_ID,
        session_id=snapshot.get("session_id", "SESSION_IDLE"),
        seq=max(1, await store.next_seq()),
        timestamp_ms=0,
        source_mode=SOURCE_MODE,
        payload={"snapshot": snapshot},
    ).model_dump()


@app.get("/health")
async def health() -> dict:
    runtime = active_runtime_service()
    snapshot = (
        presentation_service.snapshot()
        if SOURCE_MODE == "presentation"
        else runtime.snapshot()
        if runtime is not None
        else await store.get_snapshot()
    )
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "version": APP_VERSION,
        "source_mode": SOURCE_MODE,
        "active_session": snapshot.get("session_id"),
        "websocket_clients": hub.client_count,
        "software_status": snapshot.get("software_status", "ready"),
        "data_stream_status": snapshot.get("data_stream_status", "not_applicable"),
    }


@app.on_event("startup")
async def startup() -> None:
    if SOURCE_MODE == "presentation":
        await presentation_service.start()
    elif active_runtime_service() is not None:
        await active_runtime_service().start()
    else:
        await raw_log_service.start()
    await cloud_sync_service.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    await cloud_sync_service.stop()
    await presentation_service.stop()
    if real_runtime_service is not None:
        await real_runtime_service.stop()
    if pc_local_runtime_service is not None:
        await pc_local_runtime_service.stop()
    await raw_log_service.stop()


@app.get("/api/demo/scenarios")
async def demo_scenarios() -> dict:
    return {"scenarios": [item.model_dump() for item in list_scenarios()]}


@app.post("/api/competition-demo/start")
async def competition_demo_start(request: CompetitionDemoRequest) -> dict:
    if SOURCE_MODE == "presentation":
        return await presentation_service.start_training(request.patient_id, fast=request.speed_mode == "fast")
    if SOURCE_MODE in {"device", "pc_local"}:
        raise HTTPException(status_code=409, detail="Training is controlled by the active real-data source.")
    return await start_competition_demo(request)


@app.post("/api/demo/start")
async def start_demo(request: StartDemoRequest) -> dict:
    if SOURCE_MODE == "presentation":
        return await presentation_service.start_training(request.patient_id, fast=False)
    if SOURCE_MODE in {"device", "pc_local"}:
        raise HTTPException(status_code=409, detail="Training is controlled by the active real-data source.")
    return await engine.start(request.scenario_id, request.speed, request.patient_id)


@app.post("/api/demo/reset")
async def reset_demo() -> dict:
    if SOURCE_MODE == "presentation":
        return await presentation_service.reset_training()
    if SOURCE_MODE in {"device", "pc_local"}:
        raise HTTPException(status_code=409, detail="Training is controlled by the active real-data source.")
    return await engine.reset()


@app.get("/api/state/current")
async def current_state() -> dict:
    if SOURCE_MODE == "presentation":
        return {"snapshot": presentation_service.snapshot()}
    runtime = active_runtime_service()
    if runtime is not None:
        return {"snapshot": runtime.snapshot()}
    return {"snapshot": await store.get_snapshot()}


@app.get("/api/model/status")
async def model_status() -> dict:
    model_path = PROJECT_ROOT / "frontend" / "public" / "models" / "body.glb"
    return {
        "body_glb_present": model_path.exists(),
        "rigged_glb_present": (PROJECT_ROOT / "frontend" / "public" / "models" / "rehab_upper_limb_rigged.glb").exists(),
        "body_glb_path": "frontend/public/models/body.glb",
        "body_glb_size": model_path.stat().st_size if model_path.exists() else 0,
        "status": "ready" if model_path.exists() else "pending",
    }


@app.get("/api/patients")
async def patients() -> dict:
    if SOURCE_MODE == "presentation":
        return {"patients": presentation_service.patients()}
    runtime = active_runtime_service()
    if runtime is not None:
        return {"patients": runtime.patients()}
    return {"patients": list_patients()}


@app.get("/api/patients/{patient_id}")
async def patient_detail(patient_id: str) -> dict:
    if SOURCE_MODE == "presentation":
        return {"patient": presentation_service.patient(patient_id)}
    runtime = active_runtime_service()
    if runtime is not None:
        return {"patient": runtime.patient(patient_id)}
    return {"patient": get_patient(patient_id)}


@app.get("/api/patients/{patient_id}/sessions")
async def patient_sessions(patient_id: str) -> dict:
    if SOURCE_MODE == "presentation":
        return {"sessions": cloud_sync_service.enrich_sessions(presentation_service.sessions())}
    runtime = active_runtime_service()
    if runtime is not None:
        if patient_id != PATIENT_ID:
            raise HTTPException(status_code=404, detail="Patient not found.")
        return {
            "sessions": cloud_sync_service.enrich_sessions(
                [_with_session_v12_status(item) for item in runtime.sessions()],
                refresh_discovery=False,
            )
        }
    return {"sessions": cloud_sync_service.enrich_sessions([_with_session_v12_status(item) for item in list_sessions(patient_id)])}


@app.get("/api/sessions")
async def doctor_session_results() -> dict:
    runtime = active_runtime_service()
    if runtime is not None:
        sessions = runtime.sessions()
    else:
        sessions = list_sessions(PATIENT_ID)
    enriched = []
    for item in sessions:
        enriched.append(_with_session_v12_status(item))
    return {
        "baseline_precheck": session_v12_scoring_service.formal_baseline_precheck(),
        "sessions": enriched,
    }


@app.get("/api/sessions/{session_id}")
async def doctor_session_result(session_id: str) -> dict:
    detail = session_v12_scoring_service.session_detail(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Session scoring result not found.")
    return {"session": detail}


@app.post("/api/sessions/{session_id}/rescore")
async def doctor_session_rescore(session_id: str) -> dict:
    result = session_v12_scoring_service.rescore(session_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result.get("reason") or "Session not found.")
    if result.get("accepted") is not True:
        raise HTTPException(status_code=409, detail=result)
    return {"rescore": result}


@app.get("/api/sessions/{session_id}/repetitions/{rep_index}")
async def doctor_session_repetition(session_id: str, rep_index: int) -> dict:
    repetition = session_v12_scoring_service.repetition_detail(session_id, rep_index)
    if repetition is None:
        raise HTTPException(status_code=404, detail="Repetition result not found.")
    return {"repetition": repetition}


@app.get("/api/sessions/{session_id}/repetitions/{rep_index}/trajectory")
async def doctor_session_repetition_trajectory(session_id: str, rep_index: int) -> dict:
    trajectory = session_v12_scoring_service.trajectory(session_id, rep_index)
    if trajectory is None:
        raise HTTPException(status_code=404, detail="Repetition trajectory not found.")
    return {"trajectory": trajectory}


@app.get("/api/cloud-sync/summary")
async def cloud_sync_summary() -> dict:
    return cloud_sync_service.summary()


@app.get("/api/cloud-sync/sessions/{session_id}")
async def cloud_sync_session(session_id: str) -> dict:
    return cloud_sync_service.session_status(session_id)


@app.post("/api/cloud-sync/sessions/{session_id}/upload")
async def cloud_sync_upload_session(session_id: str) -> dict:
    runtime = active_runtime_service()
    session_record = runtime.session(session_id) if runtime is not None else None
    return cloud_sync_service.request_upload(session_id, session_record)


@app.post("/api/cloud-sync/upload-pending")
async def cloud_sync_upload_pending() -> dict:
    runtime = active_runtime_service()
    if runtime is not None:
        cloud_sync_service.refresh_session_records(runtime.sessions())
    return cloud_sync_service.upload_pending()


@app.post("/api/cloud-sync/auto-upload")
async def cloud_sync_auto_upload(payload: dict = Body(...)) -> dict:
    return cloud_sync_service.set_auto_upload(bool(payload.get("enabled")))


@app.post("/api/cloud-sync/check")
async def cloud_sync_check() -> dict:
    return cloud_sync_service.check_cloud(force=True)


@app.get("/api/predictions/next-score")
async def next_score_prediction(task_id: str = Query(...)) -> dict:
    if task_id not in SUPPORTED_TASKS:
        raise HTTPException(status_code=422, detail="Unsupported task_id.")
    return predict_next_score(task_id)


@app.get("/api/patients/{patient_id}/sessions/{session_id}")
async def patient_session(patient_id: str, session_id: str) -> dict:
    if SOURCE_MODE == "presentation":
        session = presentation_service.session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found.")
        return {"session": session}
    runtime = active_runtime_service()
    if runtime is not None:
        if patient_id != PATIENT_ID:
            raise HTTPException(status_code=404, detail="Patient not found.")
        session = runtime.session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found.")
        session = _with_session_v12_status(session)
        session["cloud_sync"] = cloud_sync_service.session_status(session_id)
        return {"session": session}
    session = get_session(patient_id, session_id)
    session = _with_session_v12_status(session)
    session["cloud_sync"] = cloud_sync_service.session_status(session_id)
    return {"session": session}


@app.get("/api/patients/{patient_id}/sessions/{session_id}/report")
async def patient_session_report(patient_id: str, session_id: str) -> dict:
    if SOURCE_MODE == "presentation":
        if session_id == PRESENTATION_SESSION_ID and not presentation_service.snapshot().get("report_ready"):
            raise HTTPException(status_code=409, detail="Report is not ready for the current session.")
        report = deepcopy(presentation_service.report())
        report["patient"]["session_id"] = session_id or PRESENTATION_SESSION_ID
        return {"report": report}
    runtime = active_runtime_service()
    if runtime is not None:
        if patient_id != PATIENT_ID:
            raise HTTPException(status_code=404, detail="Patient not found.")
        report = runtime.report(session_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found for this session.")
        return {"report": report}
    return {"report": get_report(patient_id, session_id)}


@app.get("/api/patients/{patient_id}/sessions/{session_id}/logs")
async def patient_session_logs(
    patient_id: str,
    session_id: str,
    type: str = Query(default="imu"),
    sensor_id: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    if SOURCE_MODE == "presentation":
        return presentation_service.logs(type, limit, offset)
    runtime = active_runtime_service()
    if runtime is not None:
        return runtime.logs(session_id, type, limit, offset)
    return read_logs(patient_id, session_id, type, sensor_id, start_ms, end_ms, limit, offset)


@app.get("/api/patients/{patient_id}/sessions/{session_id}/logs/export")
async def patient_session_logs_export(
    patient_id: str,
    session_id: str,
    type: str = Query(default="imu"),
    format: str = Query(default="csv"),
):
    if SOURCE_MODE == "presentation":
        rows = presentation_service.logs(type, 500, 0)["items"]
        filename = f"{patient_id}_{session_id}_{type}.{format}"
        if format == "jsonl":
            content = "\n".join(json.dumps(item, ensure_ascii=False) for item in rows)
            return Response(content + ("\n" if content else ""), media_type="application/x-ndjson", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["timestamp_ms", "received_at_ms", "session_id", "seq", "type", "sensor_id", "summary"])
        writer.writeheader()
        for item in rows:
            writer.writerow({
                "timestamp_ms": item.get("timestamp_ms"),
                "received_at_ms": item.get("received_at_ms"),
                "session_id": item.get("session_id"),
                "seq": item.get("seq"),
                "type": type,
                "sensor_id": item.get("sensor_id", item.get("key", "")),
                "summary": json.dumps(item.get("derived", item.get("channels", item.get("score", {}))), ensure_ascii=False),
            })
        return Response(output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    if SOURCE_MODE in {"device", "pc_local"}:
        raise HTTPException(status_code=409, detail="Real-data log export is not enabled.")
    return export_logs(patient_id, session_id, type, format)


@app.get("/api/report/current")
async def current_report() -> dict:
    if SOURCE_MODE == "presentation":
        if not presentation_service.snapshot().get("report_ready"):
            raise HTTPException(status_code=409, detail="Report is not ready for the current session.")
        return {"report": presentation_service.report()}
    runtime = active_runtime_service()
    if runtime is not None:
        report = runtime.report()
        if report is None:
            raise HTTPException(status_code=409, detail="Report is not ready for the current session.")
        return {"report": report}
    snapshot = await store.get_snapshot()
    report = await store.get_report()
    if not snapshot.get("report_ready") or report is None:
        raise HTTPException(status_code=409, detail="Report is not ready for the current session.")
    if report["patient"]["session_id"] != snapshot["session_id"]:
        raise HTTPException(status_code=409, detail="Report does not match the current session.")
    return {"report": report}


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    await hub.connect(websocket, await snapshot_message())
    await hub.pump(websocket)


def require_pc_local() -> None:
    if SOURCE_MODE != "pc_local" or pc_local_runtime_service is None:
        raise HTTPException(
            status_code=409,
            detail="This operation is available only in pc_local mode.",
        )


@app.get("/api/patient/runtime")
async def patient_runtime() -> dict:
    require_pc_local()
    return {"snapshot": pc_local_runtime_service.snapshot()}


@app.get("/api/patient/reports")
async def patient_reports() -> dict:
    require_pc_local()
    sessions = []
    for item in pc_local_runtime_service.sessions():
        session_id = str(item.get("session_id") or "")
        if _patient_report_visible_session(
            item,
            report_available=_patient_report_source_available(session_id),
        ):
            sessions.append(item)
    return {"sessions": sessions}


@app.get("/api/patient/reports/{session_id}")
async def patient_report(session_id: str) -> dict:
    require_pc_local()
    report = pc_local_runtime_service.report(session_id)
    session = pc_local_runtime_service.session(session_id)
    legacy_report = _legacy_patient_report(session_id)
    resolved_report = (
        report
        if _report_has_current_score(report) or legacy_report is None
        else legacy_report
    )
    if (
        resolved_report is None
        or session is None
        or not _patient_report_visible_session(session, resolved_report)
    ):
        raise HTTPException(status_code=404, detail="Report not found.")
    return {"report": resolved_report}


def _patient_report_visible_session(
    session: dict,
    report: dict | None = None,
    report_available: bool | None = None,
) -> bool:
    if session.get("status") != "COMPLETED":
        return False
    if session.get("source_origin") != "pc_udp_6006_ingest":
        return False
    if session.get("record_kind") == "showcase" or session.get("formal") is False:
        return False
    if session.get("report_ready") is True:
        return True
    return report is not None or report_available is True


def _patient_report_source_available(session_id: str) -> bool:
    if not session_id:
        return False
    try:
        session_dir = device_history_store.session_directory(session_id)
    except ValueError:
        return False
    if (session_dir / "doctor" / "report.json").is_file():
        return True
    per_rep = (
        session_dir
        / "scoring"
        / "intermediate"
        / "dedicated_scoring"
        / session_id
        / "per_rep_scores"
    )
    return per_rep.is_dir() and any(per_rep.glob("*_score.json"))


def _legacy_patient_report(session_id: str) -> dict | None:
    try:
        path = (
            device_history_store.session_directory(session_id)
            / "doctor"
            / "report.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _report_has_current_score(report: dict | None) -> bool:
    if not isinstance(report, dict):
        return False
    score = report.get("score")
    if not isinstance(score, dict):
        return False
    value = score.get("final_score")
    if not isinstance(value, (int, float)):
        return False
    compensation = score.get("compensation")
    source = (
        compensation.get("source")
        if isinstance(compensation, dict)
        else None
    )
    return (
        score.get("valid") is True
        and math.isfinite(value)
        and source == "authoritative_session_v12_per_rep_scores"
    )


@app.post("/api/training/start")
async def training_start() -> dict:
    require_pc_local()
    try:
        return {"training": await pc_local_runtime_service.start_training()}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/training/stop")
async def training_stop() -> dict:
    require_pc_local()
    try:
        return {"training": await pc_local_runtime_service.stop_training()}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/training/current")
async def training_current() -> dict:
    require_pc_local()
    return {"training": pc_local_runtime_service.current_training()}


@app.post("/api/patient/runtime/csv-replay")
async def csv_replay_control(payload: dict = Body(...)) -> dict:
    require_pc_local()
    action = str(payload.get("action") or "")
    speed = payload.get("speed")
    parsed_speed = speed if isinstance(speed, (int, float)) else None
    return await pc_local_runtime_service.csv_replay_control(
        action,
        float(parsed_speed) if parsed_speed is not None else None,
    )


@app.get("/api/doctor/tasks")
async def doctor_tasks() -> dict:
    return {
        "tasks": task_library_service.list_tasks(),
        "supported_registry": task_library_service.supported_registry(),
    }


@app.post("/api/doctor/tasks")
async def doctor_task_add(payload: dict = Body(...)) -> dict:
    try:
        return {"task": task_library_service.add(str(payload.get("task_id") or ""))}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.patch("/api/doctor/tasks/{task_id}")
async def doctor_task_update(task_id: str, payload: dict = Body(...)) -> dict:
    try:
        return {"task": task_library_service.update(task_id, payload)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found.") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/doctor/tasks/{task_id}", status_code=204)
async def doctor_task_remove(task_id: str) -> Response:
    try:
        task_library_service.remove(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found.") from exc
    return Response(status_code=204)


@app.get("/api/doctor/overview")
async def doctor_overview() -> dict:
    runtime = active_runtime_service()
    sessions = runtime.sessions() if runtime is not None else []
    formal_sessions = [
        item
        for item in sessions
        if item.get("formal") is not False
        and item.get("record_kind") != "showcase"
        and item.get("status") == "COMPLETED"
        and item.get("source_mode") == "device"
        and item.get("source_origin") == "pc_udp_6006_ingest"
        and (item.get("valid_row_count") or 0) >= 20
        and (item.get("duration_seconds") or 0) >= 2
        and item.get("csv_file")
    ]
    score_eligible = [
        item
        for item in formal_sessions
        if item.get("report_ready") is True
        and item.get("score_valid") is True
        and isinstance(item.get("final_score"), (int, float))
    ]
    cutoff_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=7)).timestamp() * 1000
    )
    recent = sorted(
        formal_sessions,
        key=lambda item: item.get("started_at_ms") or 0,
        reverse=True,
    )
    recent_scores = sorted(
        score_eligible,
        key=lambda item: item.get("started_at_ms") or 0,
        reverse=True,
    )
    return {
        "patient": (
            runtime.patient(PATIENT_ID)
            if runtime is not None
            else get_patient(PATIENT_ID)
        ),
        "latest_session": recent[0] if recent else None,
        "weekly_valid_session_count": sum(
            1 for item in formal_sessions if (item.get("started_at_ms") or 0) >= cutoff_ms
        ),
        "formal_report_count": len(score_eligible),
        "score_trend": [
            {
                "session_id": item["session_id"],
                "started_at_ms": item.get("started_at_ms"),
                "final_score": item.get("final_score"),
            }
            for item in reversed(recent_scores[:7])
        ],
        "task_library_count": len(task_library_service.list_tasks()),
        "recent_sessions": recent[:5],
        "attention_items": [
            {
                "session_id": item["session_id"],
                "status": item.get("status"),
                "message": item.get("error") or item.get("scoring_status"),
            }
            for item in sessions
            if item.get("status") in {"INVALID", "FAILED"}
        ][:5],
    }


@app.get("/api/clinical/patients/{patient_id}/prescription")
async def clinical_prescription(patient_id: str) -> dict:
    if patient_id != PATIENT_ID:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return {"prescription": get_prescription()}


@app.put("/api/clinical/patients/{patient_id}/prescription")
async def clinical_prescription_save(
    patient_id: str, payload: dict = Body(...)
) -> dict:
    if patient_id != PATIENT_ID:
        raise HTTPException(status_code=404, detail="Patient not found.")
    try:
        return {"prescription": save_prescription(payload)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/clinical/patients/{patient_id}/sessions/{session_id}/decision")
async def clinical_decision(patient_id: str, session_id: str) -> dict:
    if patient_id != PATIENT_ID:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return {"decision": task_decision(session_id)}


@app.get("/api/clinical/patients/{patient_id}/sessions/{session_id}/safety")
async def clinical_safety(patient_id: str, session_id: str) -> dict:
    if patient_id != PATIENT_ID:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return {"items": safety_interventions(session_id)}


@app.get("/api/clinical/patients/{patient_id}/trend")
async def clinical_trend(
    patient_id: str, limit: int = Query(default=7, ge=1, le=50)
) -> dict:
    if patient_id != PATIENT_ID:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return {"trend": training_trend(limit)}


@app.get("/api/clinical/patients/{patient_id}/sessions/{session_id}/review")
async def clinical_review(patient_id: str, session_id: str) -> dict:
    if patient_id != PATIENT_ID:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return {"review": get_review(session_id)}


@app.put("/api/clinical/patients/{patient_id}/sessions/{session_id}/review")
async def clinical_review_save(
    patient_id: str, session_id: str, payload: dict = Body(...)
) -> dict:
    if patient_id != PATIENT_ID:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return {"review": save_review(session_id, payload)}
