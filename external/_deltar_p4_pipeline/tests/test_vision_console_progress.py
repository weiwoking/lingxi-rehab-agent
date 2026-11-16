from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import deltar_p4_pipeline.scan_service as service_module
import deltar_p4_pipeline.scan_service_worker as worker_module
from deltar_p4_pipeline.scan_progress import ProgressStore, ScanTrafficProgressMiddleware
from deltar_p4_pipeline.scan_service import ServiceConfig, create_app
from deltar_p4_pipeline.scan_service_storage import ServiceStorage
from deltar_p4_pipeline.scan_service_worker import WorkerConfig


def run_upload_harness(
    tmp_path: Path, messages: list[dict], content_length: int | None
) -> tuple[list[dict], bytes]:
    store = ProgressStore(tmp_path / "progress")
    received = bytearray()

    async def downstream(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            received.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    headers = [(b"content-type", b"multipart/form-data; boundary=x")]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    scope = {
        "type": "http", "method": "POST", "path": "/api/v1/scan/jobs",
        "headers": headers, "state": {},
    }
    pending = iter(messages)

    async def receive():
        return next(pending)

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    asyncio.run(ScanTrafficProgressMiddleware(downstream, store)(scope, receive, send))
    return store.events_since(None), bytes(received)


def test_upload_middleware_reports_real_multi_chunk_bytes_without_changing_body(tmp_path: Path) -> None:
    body = b"a" * (300 * 1024) + b"tail"
    events, received = run_upload_harness(
        tmp_path,
        [
            {"type": "http.request", "body": body[:200_000], "more_body": True},
            {"type": "http.request", "body": body[200_000:], "more_body": False},
        ],
        len(body),
    )
    assert received == body
    assert [event["stage"] for event in events][0] == "upload_started"
    assert events[-1]["stage"] == "upload_completed"
    assert events[-1]["bytes_current"] == len(body)
    assert events[-1]["bytes_total"] == len(body)


def test_upload_middleware_supports_indeterminate_single_chunk_and_disconnect(tmp_path: Path) -> None:
    events, received = run_upload_harness(
        tmp_path / "single",
        [{"type": "http.request", "body": b"multipart", "more_body": False}],
        None,
    )
    assert received == b"multipart"
    assert events[-1]["bytes_total"] is None
    disconnected, _ = run_upload_harness(
        tmp_path / "disconnect",
        [
            {"type": "http.request", "body": b"partial", "more_body": True},
            {"type": "http.disconnect"},
        ],
        99,
    )
    assert disconnected[-1]["error_code"] == "upload_client_disconnected"


def test_store_is_bounded_nonblocking_bindable_and_recovers_history(tmp_path: Path) -> None:
    root = tmp_path / "progress"
    store = ProgressStore(root, max_events=5, client_queue_size=2)
    subscriber = store.subscribe()
    store.set_request_identity("req_a", "user", "user", active=True)
    store.emit("upload_started", request_id="req_a", message="token=secret")
    assert store.snapshot()["current_scan"]["request_id"] == "req_a"
    assert store.snapshot()["current_scan"]["scan_id"] is None
    store.bind_request("req_a", "scan_a")
    assert store.snapshot()["current_scan"]["scan_id"] == "scan_a"
    for index in range(8):
        store.emit(
            "geometry_progress", request_id="req_a", current=index, total=8,
            message=f"object {index}",
        )
    store.emit(
        "selection_completed", request_id="req_a",
        details={"gate1_count": 4, "final_selected_count": 2, "class_counts": {"cup": 2}},
    )
    assert len(store.events_since(None)) == 5
    assert subscriber.qsize() == 2
    assert store.scan_detail("scan_a")["summary"]["metrics"]["final_selected_count"] == 2
    store._write_queue.join()
    recovered = ProgressStore(root, max_events=20)
    detail = recovered.scan_detail("scan_a")
    assert detail is not None
    assert detail["summary"]["restart_incomplete"] is True
    assert "secret" not in json.dumps(store.events_since(None))


def test_business_reducer_isolates_history_telemetry_and_terminal_state(tmp_path: Path) -> None:
    store = ProgressStore(tmp_path / "progress")
    store.emit("upload_started", scan_id="scan_failed")
    store.emit(
        "failed", scan_id="scan_failed", status="error",
        error_code="E_PIPELINE_FAILED", message="old failure",
    )
    store.emit("upload_started", scan_id="scan_current")
    store.emit("validation_completed", scan_id="scan_current", status="success")
    before = store.snapshot(active_user_scan_id="scan_current")["current_scan"]
    store.emit(
        "p4_status_polled", scan_id="scan_failed",
        details={"last_status_returned": "failed"}, persist=False,
    )
    after_poll = store.snapshot(active_user_scan_id="scan_current")
    assert after_poll["current_scan"]["scan_id"] == "scan_current"
    assert after_poll["current_scan"]["business_stage"] == before["business_stage"] == "validation"
    assert {event["scan_id"] for event in after_poll["recent_events"]} == {"scan_current"}

    for stage in (
        "detection_completed", "geometry_completed", "selection_completed",
        "package_ready", "package_send_completed",
    ):
        store.emit(stage, scan_id="scan_current", status="success")
    completed_state = store.snapshot(active_user_scan_id="scan_current")
    terminal = completed_state["last_completed_user_scan"]
    assert completed_state["current_user_scan"] is None
    assert completed_state["ui_state"] == "completed"
    assert terminal["scan_id"] == "scan_current"
    assert terminal["final_status"] == "sent"
    assert terminal["business_status"] == "success"
    assert terminal["active_stage"] is None
    assert list(terminal["step_statuses"].values()) == ["success"] * 7
    failed = store.scan_detail("scan_failed")["summary"]
    assert failed["final_status"] == "failed"
    assert failed["error"]["error_code"] == "E_PIPELINE_FAILED"
    assert failed["stage_states"]["upload"] == "FAILED"
    assert terminal["error"] is None


def test_request_binding_and_restart_keep_unique_terminal_events(tmp_path: Path) -> None:
    root = tmp_path / "progress"
    store = ProgressStore(root)
    first = store.emit("upload_started", request_id="req_a")
    store.emit("upload_progress", request_id="req_a", bytes_current=10)
    store.bind_request("req_a", "scan_bound")
    store.emit("upload_completed", request_id="req_a")
    store.emit("package_send_completed", scan_id="scan_bound", status="success")
    detail = store.scan_detail("scan_bound")
    ids = [event["event_id"] for event in detail["events"]]
    assert first["event_id"] in ids
    assert len(ids) == len(set(ids))
    assert store.snapshot()["recent_events"] == detail["events"]
    store._write_queue.join()

    recovered = ProgressStore(root)
    terminal = recovered.scan_detail("scan_bound")
    assert terminal is not None
    assert terminal["summary"]["final_status"] == "sent"
    assert terminal["summary"]["active_stage"] is None
    recovered_ids = [event["event_id"] for event in terminal["events"]]
    assert len(recovered_ids) == len(set(recovered_ids))


def test_fine_progress_eta_and_console_three_views(tmp_path: Path) -> None:
    store = ProgressStore(tmp_path / "progress")
    for event_id, frame_index, duration in ((1, 1, 900), (2, 2, 1100)):
        store.emit(
            "pipeline_progress", scan_id="scan_fine", status="success",
            elapsed_ms=duration, message="帧完成",
            details={
                "pipeline_event_id": event_id,
                "pipeline_schema_version": "visionpc.pipeline.progress.v1",
                "pipeline_timestamp": f"2026-08-03T10:20:0{event_id}.000000Z",
                "pipeline_status": "completed", "phase": "frame_processing", "subphase": "frame",
                "frame_id": f"frame_{frame_index - 1:06d}", "frame_index": frame_index,
                "frame_total": 11, "elapsed_ms": duration, "observer_mode": "child_stream",
                "metrics": {"completed_frames": frame_index, "successful_frames": frame_index,
                            "rejected_frames": 0, "failed_frames": 0},
                "completed_frames": frame_index, "successful_frames": frame_index,
                "rejected_frames": 0, "failed_frames": 0,
            },
        )
    current = store.snapshot(active_user_scan_id="scan_fine")["current_scan"]
    assert current["eta_seconds"] == 9.0
    assert current["pipeline_subphase"] == "frame"
    assert current["stage_states"]["frame_scene"] == "RUNNING"
    assert "业务进度" in service_module.VISION_CONSOLE_HTML
    assert "通信" in service_module.VISION_CONSOLE_HTML
    assert "原始事件" in service_module.VISION_CONSOLE_HTML
    assert "预计剩余" in service_module.VISION_CONSOLE_HTML


def test_console_rest_ui_links_and_unknown_scan(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        service_module, "vision_health",
        lambda: {"online": True, "status_code": 200, "health": {"status": "ok"}},
    )
    config = ServiceConfig(data_root=tmp_path / "data", pipeline_root=tmp_path)
    with TestClient(create_app(config)) as client:
        page = client.get("/vision-console")
        assert page.status_code == 200
        assert "P4 11帧上传" in page.text
        assert "performance-state" in page.text
        assert "healthy:'正常'" in page.text
        assert "paused_busy:'P4忙，暂停'" in page.text
        assert "failed:'恢复失败'" in page.text
        assert "8 阶段实时流程" in page.text
        assert "PC已完整发送package响应" not in page.text
        assert "viewMode='current'" in page.text
        assert "selectedScanId=null" in page.text
        assert "logMode='summary'" in page.text
        assert "c?.error" in page.text
        assert "state?.scans||[]).find(x=>x.error" not in page.text
        assert "state.current_user_scan" in page.text
        assert "state?.current_scan" not in page.text
        assert "slow_warning" not in page.text
        assert "当前处理速度较慢" not in page.text
        state = client.get("/api/v1/vision-console/state").json()
        assert state["services"]["vision"]["online"] is True
        assert state["performance_guard"]["status"] == "unknown"
        assert state["links"]["scan_docs"] == "/docs"
        assert "E_SCAN_VALIDATE" in state["error_guidance"]
        assert client.get("/api/v1/vision-console/scans/missing").status_code == 404


def test_console_visual_polish_semantics_and_time_formatter() -> None:
    html = service_module.VISION_CONSOLE_HTML
    assert "grid-template-columns:minmax(0,1.62fr) minmax(320px,1fr)" in html
    assert "@media(max-width:980px){.main-grid{grid-template-columns:1fr}" in html
    assert "@media(max-width:1180px)" not in html
    assert "@media(max-width:1500px)" not in html
    assert "grid-template-columns:repeat(8,minmax(0,1fr))" in html
    assert ".history-wrap{overflow-x:hidden" in html
    assert "table{width:100%;table-layout:fixed" in html
    assert '<details id="logs-card" class="card full"><summary>' in html
    assert '<details id="logs-card" class="card full" open>' not in html
    assert "真实 user scan" not in html
    assert "内部 replay / diagnostic / test 已隔离" not in html
    assert "扫描完成 · 部分成功" in html
    assert ".hero-title.partial{color:var(--amber)}" in html
    assert "rejected&&i>=completed-rejected?'bad':'done'" in html
    assert "nextCheckText(g)" in html
    assert "return'即将检查'" in html
    assert "P4忙，已暂停" in html

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable for the JavaScript formatter check")
    formatter = re.search(
        r"const parseTime=.*?;const ago=", html, flags=re.DOTALL
    )
    assert formatter is not None
    definitions = formatter.group(0).removesuffix("const ago=")
    script = definitions + """
const values = [
  formatTime('2026-08-10T09:39:23.700889Z'),
  formatTime('2026-08-10T17:39:23.700+08:00'),
  formatTime(null), formatTime('not-a-time'),
  formatTime('2026-08-10T09:39:23.700889Z', true)
];
console.log(JSON.stringify(values));
"""
    env = {**os.environ, "TZ": "Asia/Shanghai"}
    completed = subprocess.run(
        [node, "-e", script], check=True, capture_output=True, text=True,
        encoding="utf-8", env=env,
    )
    values = json.loads(completed.stdout)
    assert values == [
        "2026-08-10 17:39:23",
        "2026-08-10 17:39:23",
        "—", "—", "08-10 17:39:23",
    ]


def test_package_send_wrapper_preserves_bytes_and_emits_completion(tmp_path: Path) -> None:
    store = ProgressStore(tmp_path / "progress")
    package = b'{"unchanged":true}'

    async def downstream(scope, receive, send):
        await send({
            "type": "http.response.start", "status": 200,
            "headers": [(b"content-length", str(len(package)).encode("ascii"))],
        })
        await send({"type": "http.response.body", "body": package, "more_body": False})

    scope = {
        "type": "http", "method": "GET",
        "path": "/api/v1/scans/scan_a/decision-package", "headers": [],
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(ScanTrafficProgressMiddleware(downstream, store)(scope, receive, send))
    assert sent[-1]["body"] == package
    assert [event["stage"] for event in store.events_since(None)] == [
        "package_send_started", "package_send_progress", "package_send_completed"
    ]
    assert store.events_since(None)[-1]["bytes_current"] == len(package)


def test_worker_observer_does_not_reread_intermediate_authority_files(
    monkeypatch, tmp_path: Path
) -> None:
    storage = ServiceStorage(tmp_path / "data")
    scan_id = "scan_observer"
    result_dir = storage.result_dir(scan_id)
    (result_dir / "frame_results").mkdir(parents=True)
    (result_dir / "frame_results" / "f0.json").write_text(
        json.dumps({"detections": {"detect_count": 3}}), encoding="utf-8"
    )
    (result_dir / "stage8b_all_object_geometry.v1.json").write_text(
        json.dumps({"summary": {"object_count": 7}}), encoding="utf-8"
    )
    (result_dir / "stage8b_dual_gate_selection.v1.json").write_text(
        json.dumps({"objects": [{"gate1_passed": True}, {"gate1_passed": False}]}),
        encoding="utf-8",
    )
    (result_dir / "stage8b4a_selected_objects.v2.json").write_text(
        json.dumps({"items": [{"class_name": "cup"}, {"class_name": "cup"}]}),
        encoding="utf-8",
    )
    package = b'{"scan_id":"scan_observer","objects":[]}'
    monkeypatch.setattr(
        worker_module, "_run_process",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, b"", b""),
    )
    monkeypatch.setattr(
        worker_module, "validate_result_files",
        lambda *args: ({"job_id": "job_a", "status": "success"}, b"result"),
    )
    monkeypatch.setattr(
        worker_module, "validate_decision_package",
        lambda *args, **kwargs: ({"scan_id": scan_id}, package, hashlib.sha256(package).hexdigest()),
    )
    monkeypatch.setattr(worker_module, "_append_worker_phase", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker_module, "_finish_full_scene", lambda *args, **kwargs: None)
    record = {
        "scan_id": scan_id, "service_job_id": "svc_a", "frame_count": 11,
        "status": "running",
    }
    transitions: list[dict] = []
    events: list[dict] = []
    config = WorkerConfig(
        pipeline_root=tmp_path, worker_python=tmp_path / "python.exe",
        profile_bundle=tmp_path / "profiles",
    )
    worker_module.run_pipeline(
        config, storage, record, lambda value: transitions.append(dict(value)),
        lambda stage, **values: events.append({"stage": stage, **values}),
    )
    by_stage = {event["stage"]: event for event in events}
    assert "detection_count" not in by_stage["detection_completed"]["details"]
    assert "geometry_progress" not in by_stage
    assert by_stage["geometry_completed"]["details"]["object_count"] == 7
    assert by_stage["selection_completed"]["details"]["final_selected_count"] == 0
    assert by_stage["package_ready"]["details"]["package_size_bytes"] == len(package)
    assert record["package_sha256"] == hashlib.sha256(package).hexdigest()


def test_no_active_authority_and_latest_completed_never_becomes_current(tmp_path: Path) -> None:
    store = ProgressStore(tmp_path / "progress")
    store.emit("upload_started", scan_id="scan_done")
    store.emit("processing_started", scan_id="scan_done")
    store.emit("package_ready", scan_id="scan_done", status="success")
    state = store.snapshot()
    assert state["current_user_scan"] is None
    assert state["current_scan"] is None
    assert state["last_completed_user_scan"]["scan_id"] == "scan_done"
    assert [item["scan_id"] for item in state["scans"]] == ["scan_done"]


def test_internal_roles_are_filtered_from_user_current_history_and_events(tmp_path: Path) -> None:
    store = ProgressStore(tmp_path / "progress")
    for role in ("replay", "diagnostic", "test", "canary"):
        scan_id = f"scan_{role}"
        store.emit(
            "upload_started", scan_id=scan_id, job_role=role,
            visibility="diagnostics",
        )
        store.emit(
            "package_ready", scan_id=scan_id, status="success", job_role=role,
            visibility="diagnostics",
        )
    state = store.snapshot(active_user_scan_id="scan_replay")
    assert state["current_user_scan"] is None
    assert state["last_completed_user_scan"] is None
    assert state["scans"] == []
    assert store.events_since(None, user_only=True) == []
    assert store.scan_detail("scan_replay")["summary"]["job_role"] == "replay"


def test_manager_authority_selects_exactly_one_active_user_scan(tmp_path: Path) -> None:
    store = ProgressStore(tmp_path / "progress")
    store.emit("upload_started", scan_id="scan_old")
    store.emit("package_send_completed", scan_id="scan_old", status="success")
    store.emit("upload_started", scan_id="scan_live")
    state = store.snapshot(active_user_scan_id="scan_live")
    assert state["ui_state"] == "active"
    assert state["current_user_scan"]["scan_id"] == "scan_live"
    assert all(item["scan_id"] != "scan_live" for item in state["scans"])


def test_pc_completion_freezes_timer_and_is_completed_while_waiting_p4(tmp_path: Path) -> None:
    store = ProgressStore(tmp_path / "progress")
    store.emit("upload_started", scan_id="scan_wait")
    store.emit("upload_completed", scan_id="scan_wait", elapsed_ms=100)
    store.emit("processing_started", scan_id="scan_wait")
    store.emit("package_ready", scan_id="scan_wait", status="success")
    first = store.snapshot()["last_completed_user_scan"]
    time.sleep(0.02)
    second_state = store.snapshot()
    second = second_state["last_completed_user_scan"]
    assert second_state["ui_state"] == "completed"
    assert second["delivery_state"] == "waiting_p4"
    assert second["stage_states"]["p4_communication"] == "WAITING_P4"
    assert first["total_elapsed_ms"] == second["total_elapsed_ms"]
    assert first["processing_elapsed_ms"] == second["processing_elapsed_ms"]
    assert first["seconds_since_last_progress"] == second["seconds_since_last_progress"]
    assert second["eta_seconds"] is None


def test_completed_focus_at_five_minutes_and_two_hour_idle_have_no_stale_current(
    tmp_path: Path, monkeypatch,
) -> None:
    store = ProgressStore(tmp_path / "progress")
    store.emit("upload_started", scan_id="scan_focus")
    store.emit("processing_started", scan_id="scan_focus")
    store.emit("package_ready", scan_id="scan_focus", status="success")
    frozen = store.snapshot()["last_completed_user_scan"]["total_elapsed_ms"]

    monkeypatch.setattr(store, "_seconds_since", lambda _value: 5 * 60)
    five_minutes = store.snapshot()
    assert five_minutes["ui_state"] == "completed"
    assert five_minutes["current_user_scan"] is None
    assert five_minutes["last_completed_user_scan"]["total_elapsed_ms"] == frozen

    monkeypatch.setattr(store, "_seconds_since", lambda _value: 2 * 60 * 60)
    two_hours = store.snapshot()
    assert two_hours["ui_state"] == "idle"
    assert two_hours["current_user_scan"] is None
    assert two_hours["last_completed_user_scan"]["scan_id"] == "scan_focus"
    assert two_hours["last_completed_user_scan"]["total_elapsed_ms"] == frozen


def test_package_done_is_monotonic_and_p4_delivery_has_three_states(tmp_path: Path) -> None:
    store = ProgressStore(tmp_path / "progress")
    store.emit(
        "pipeline_progress", scan_id="scan_stage", status="success",
        details={
            "pipeline_event_id": 1,
            "pipeline_schema_version": "visionpc.pipeline.progress.v1",
            "pipeline_status": "completed", "phase": "package",
            "subphase": "package_write", "metrics": {"package_bytes": 1200},
        },
    )
    store.emit("package_build_started", scan_id="scan_stage")
    assert store.scan_detail("scan_stage")["summary"]["pipeline_step_statuses"]["package"] == "success"
    store.emit("package_ready", scan_id="scan_stage", status="success")
    waiting = store.snapshot()["last_completed_user_scan"]
    assert list(waiting["stage_states"].values())[:7] == ["DONE"] * 7
    assert waiting["stage_states"]["p4_communication"] == "WAITING_P4"
    store.emit("p4_package_requested", scan_id="scan_stage")
    sending = store.snapshot()["last_completed_user_scan"]
    assert sending["stage_states"]["p4_communication"] == "SENDING"
    store.emit("package_send_started", scan_id="scan_stage")
    store.emit("package_send_completed", scan_id="scan_stage", status="success")
    done = store.snapshot()["last_completed_user_scan"]
    assert done["stage_states"]["p4_communication"] == "DONE"
    assert done["p4_send_started_at"] is not None
    assert done["p4_send_completed_at"] is not None


def test_nested_child_metrics_map_geometry_selected_and_zero_explicitly(tmp_path: Path) -> None:
    store = ProgressStore(tmp_path / "progress")
    store.emit(
        "pipeline_progress", scan_id="scan_metrics", status="success",
        details={
            "pipeline_event_id": 1,
            "pipeline_schema_version": "visionpc.pipeline.progress.v1",
            "pipeline_status": "completed", "phase": "all_object_geometry",
            "subphase": "geometry_document_write",
            "metrics": {"object_count": 73, "geometry_success_count": 73},
        },
    )
    store.emit(
        "pipeline_progress", scan_id="scan_metrics", status="success",
        details={
            "pipeline_event_id": 2,
            "pipeline_schema_version": "visionpc.pipeline.progress.v1",
            "pipeline_status": "completed", "phase": "selection",
            "subphase": "selected_write", "metrics": {"selected_count": 0},
        },
    )
    summary = store.scan_detail("scan_metrics")["summary"]
    assert summary["metrics"]["geometry_object_count"] == 73
    assert summary["metrics"]["geometry_success_count"] == 73
    assert summary["metrics"]["final_selected_count"] == 0


def test_recovery_does_not_resurrect_unclassified_legacy_or_internal_scan(tmp_path: Path) -> None:
    root = tmp_path / "progress"
    root.mkdir()
    old = {
        "event_id": 1, "request_id": None, "scan_id": "scan_internal_old",
        "timestamp": "2026-08-10T05:38:04Z", "stage": "upload_started",
        "status": "info", "current": None, "total": None,
        "bytes_current": 0, "bytes_total": 10, "elapsed_ms": None,
        "message": "legacy replay", "error_code": None, "error_detail": None,
        "details": {},
    }
    (root / "scan_internal_old.progress_events.jsonl").write_text(
        json.dumps(old) + "\n", encoding="utf-8"
    )
    recovered = ProgressStore(root)
    state = recovered.snapshot(active_user_scan_id="scan_internal_old")
    assert state["ui_state"] == "idle"
    assert state["current_user_scan"] is None
    assert state["scans"] == []
    assert recovered.scan_detail("scan_internal_old")["summary"]["visibility"] == "diagnostics"


def test_reconnect_and_refresh_restore_same_final_state(tmp_path: Path) -> None:
    store = ProgressStore(tmp_path / "progress")
    store.emit("upload_started", scan_id="scan_final")
    store.emit("processing_started", scan_id="scan_final")
    store.emit("package_ready", scan_id="scan_final", status="success")
    store.emit("package_send_started", scan_id="scan_final")
    store.emit("package_send_completed", scan_id="scan_final", status="success")
    before = store.snapshot()
    store._write_queue.join()
    after = ProgressStore(tmp_path / "progress").snapshot()
    assert before["current_user_scan"] is after["current_user_scan"] is None
    assert before["last_completed_user_scan"]["final_status"] == "sent"
    assert after["last_completed_user_scan"]["final_status"] == "sent"
    assert before["last_completed_user_scan"]["total_elapsed_ms"] == after["last_completed_user_scan"]["total_elapsed_ms"]


def test_healthy_performance_has_no_second_slow_system_warning() -> None:
    html = service_module.VISION_CONSOLE_HTML
    assert "performanceText()" in html
    assert "slow_warning" not in html
    assert "请确认电脑已接电" not in html


def test_progress_summary_has_no_slow_warning_field(tmp_path: Path) -> None:
    store = ProgressStore(tmp_path / "progress")
    store.emit(
        "upload_started",
        request_id="req_no_slow",
        scan_id="scan_no_slow",
        message="upload",
    )
    summary = store.snapshot(active_user_scan_id="scan_no_slow")[
        "current_user_scan"
    ]
    assert "slow_warning" not in summary
