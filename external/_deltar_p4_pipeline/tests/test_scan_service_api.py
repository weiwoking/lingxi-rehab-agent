from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import deltar_p4_pipeline.scan_service as service_module
from deltar_p4_pipeline.scan_service import ServiceConfig, create_app
from deltar_p4_pipeline.scan_service_storage import ServiceStorage
from tests.scan_service_helpers import copy_scan, multipart_bytes
from deltar_p4_pipeline.validation_receipt import capture_input_manifest
from deltar_p4_pipeline.vision_console import VISION_CONSOLE_HTML

REAL_SCAN = Path(r"C:\ec\scan_test\scan_20260718_081523_000")


def fake_validate(_config, scan_dir: Path, known_file_identities=None):
    scan = json.loads((scan_dir / "scan.json").read_text(encoding="utf-8"))
    return {
        "elapsed_seconds": 0.001,
        "compute_seconds": 0.0005,
        "identity_seconds": 0.0005,
        "subprocess_startup": False,
        "returncode": 0,
        "scan_id": scan["scan_id"],
        "frame_count": scan["frame_count"],
        "authoritative_manifest": capture_input_manifest(scan_dir, known_file_identities),
    }


def complete_runner(_config, storage: ServiceStorage, record, transition) -> None:
    folder = storage.result_dir(record["scan_id"])
    folder.mkdir()
    result = {"schema_version": "deltar_scan_result.v1.1", "status": "rejected",
              "scan_id": record["scan_id"], "job_id": "job_fake", "objects": [],
              "warnings": [], "errors": []}
    report = {"schema_version": "deltar_pc_job_report.v1.1", "status": "rejected",
              "result_status": "rejected", "scan_id": record["scan_id"], "job_id": "job_fake"}
    storage.atomic_json(folder / "result.json", result, replace=False)
    storage.atomic_json(folder / "job_report.json", report, replace=False)
    record.update({"status": "completed", "pipeline_job_id": "job_fake", "pipeline_result_status": "rejected",
                   "finished_at": "2026-07-18T00:00:00Z", "error": None})
    transition(record)


def app_client(monkeypatch, tmp_path: Path, runner=complete_runner) -> TestClient:
    monkeypatch.setattr(service_module, "validate_scan", fake_validate)
    monkeypatch.setattr(service_module, "run_pipeline", runner)
    app = create_app(ServiceConfig(data_root=tmp_path / "data", pipeline_root=Path(r"C:\ec\_deltar_p4_pipeline")))
    return TestClient(app)


def wait_status(client: TestClient, url: str) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        value = client.get(url).json()
        if value["status"] in {"completed", "failed"}:
            return value
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_health_contract(monkeypatch, tmp_path: Path) -> None:
    with app_client(monkeypatch, tmp_path) as client:
        response = client.get("/api/v1/scan/health")
        assert response.status_code == 200
        body = response.json()
        assert body["schema_version"] == "deltar_scan_service_health.v1"
        assert body["limits"]["max_upload_bytes"] == 6291456
        assert body["limits"]["max_frames"] == 11
        assert body["performance_guard"]["status"] == "unknown"
        assert body["performance_guard"]["threshold_ms"] == 20000
        assert "C:\\" not in response.text


def test_console_raw_event_download_and_poll_is_persisted(monkeypatch, tmp_path: Path) -> None:
    with app_client(monkeypatch, tmp_path) as client:
        progress = client.app.state.scan_progress
        progress.emit("upload_started", scan_id="scan_raw")
        response = client.get("/api/v1/vision-console/scans/scan_raw/events.jsonl")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        assert b'"stage":"upload_started"' in response.content
        client.get("/api/v1/scans/unknown/decision-status")
        progress._write_queue.join()
        persisted = progress._event_path("unknown").read_text(encoding="utf-8")
        assert '"stage":"p4_status_polled"' in persisted


def test_internal_api_job_role_is_persisted_but_hidden_from_user_console(
    monkeypatch, tmp_path: Path
) -> None:
    scan = copy_scan(
        REAL_SCAN, tmp_path / "internal_scan", frame_count=1,
        scan_id="scan_internal_replay",
    )
    with app_client(monkeypatch, tmp_path / "case") as client:
        response = client.post(
            "/api/v1/scan/jobs",
            files=multipart_bytes(scan),
            headers={"X-VisionPC-Job-Role": "replay"},
        )
        assert response.status_code == 202
        final = wait_status(client, response.json()["status_url"])
        record = client.app.state.scan_manager.get(final["service_job_id"])
        assert record["job_role"] == "replay"
        assert record["visibility"] == "diagnostics"
        state = client.get("/api/v1/vision-console/state").json()
        assert state["current_user_scan"] is None
        assert state["last_completed_user_scan"] is None
        assert state["scans"] == []
        detail = client.get(
            "/api/v1/vision-console/scans/scan_internal_replay"
        ).json()
        assert detail["summary"]["job_role"] == "replay"


def test_required_local_preflight_gates_health(monkeypatch, tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    progress = data_root / "progress"
    progress.mkdir(parents=True)
    status = {
        "schema_version": "deltar_local_runtime_preflight.v1",
        "status": "ready",
        "ready": True,
        "processing_mode": "local_only",
        "runtime_network_required": False,
        "checked_at": "2026-08-03T00:00:00.000Z",
        "error": None,
    }
    (progress / "offline_preflight.json").write_text(json.dumps(status), encoding="utf-8")
    app = create_app(ServiceConfig(
        data_root=data_root,
        pipeline_root=Path(r"C:\ec\_deltar_p4_pipeline"),
        require_local_preflight=True,
    ))
    with TestClient(app) as client:
        health = client.get("/api/v1/scan/health").json()
        assert health["status"] == "ok"
        assert health["local_runtime"]["ready"] is True

    status.update({"status": "failed", "ready": False, "error": {"message": "bad checkpoint"}})
    (progress / "offline_preflight.json").write_text(json.dumps(status), encoding="utf-8")
    with TestClient(app) as client:
        health = client.get("/api/v1/scan/health").json()
        assert health["status"] == "preflight_failed"


@pytest.mark.parametrize("frames", [1, 5, 11])
def test_valid_upload_poll_and_original_result(monkeypatch, tmp_path: Path, frames: int) -> None:
    scan = copy_scan(REAL_SCAN, tmp_path / f"scan{frames}", frame_count=frames, scan_id=f"scan_{frames}")
    with app_client(monkeypatch, tmp_path / f"case{frames}") as client:
        response = client.post("/api/v1/scan/jobs", files=multipart_bytes(scan))
        assert response.status_code == 202
        accepted = response.json()
        assert accepted["status"] == "accepted"
        assert accepted["pipeline_job_id"] is None
        status = wait_status(client, accepted["status_url"])
        assert status["status"] == "completed"
        assert status["pipeline_result_status"] == "rejected"
        result = client.get(accepted["result_url"])
        stored = (tmp_path / f"case{frames}" / "data" / "results" / f"scan_{frames}" / "result.json").read_bytes()
        assert result.content == stored
        assert result.json()["job_id"] == status["pipeline_job_id"]


def test_upload_only_validates_saves_and_returns_receipt_without_pipeline(
    monkeypatch, tmp_path: Path
) -> None:
    scan = copy_scan(
        REAL_SCAN, tmp_path / "scan11", frame_count=11,
        scan_id="scan_upload_only_11",
    )
    pipeline_called = False

    def forbidden_pipeline(*_args, **_kwargs):
        nonlocal pipeline_called
        pipeline_called = True
        raise AssertionError("upload-only must not start the inference pipeline")

    with app_client(monkeypatch, tmp_path / "case", forbidden_pipeline) as client:
        response = client.post(
            "/api/v1/scan/jobs?mode=upload_only",
            files=multipart_bytes(scan),
        )
        assert response.status_code == 200
        receipt = response.json()
        assert receipt["ok"] is True
        assert receipt["mode"] == "upload_only"
        assert receipt["scan_id"] == "scan_upload_only_11"
        assert receipt["frame_count"] == 11
        assert receipt["status"] == "completed"
        assert receipt["pipeline_job_id"] is None
        assert receipt["saved_path"] == "validated/scan_upload_only_11"
        assert pipeline_called is False

        saved = (
            tmp_path / "case" / "data" / "validated" /
            "scan_upload_only_11"
        )
        assert (saved / "scan.json").is_file()
        for sequence in range(11):
            frame = saved / "frames" / f"frame_{sequence:06d}"
            assert (frame / "frame.json").is_file()
            assert (frame / "tof.json").is_file()
            assert (frame / "image.rgb565").is_file()
        assert not (
            tmp_path / "case" / "data" / "results" /
            "scan_upload_only_11"
        ).exists()


def test_upload_only_same_id_retry_is_idempotent_and_preserves_saved_scan(
    monkeypatch, tmp_path: Path
) -> None:
    scan = copy_scan(
        REAL_SCAN, tmp_path / "scan11", frame_count=11,
        scan_id="scan_retry_same_id",
    )
    with app_client(monkeypatch, tmp_path / "case") as client:
        first = client.post(
            "/api/v1/scan/jobs?mode=upload_only",
            files=multipart_bytes(scan),
        )
        assert first.status_code == 200
        saved = (
            tmp_path / "case" / "data" / "validated" /
            "scan_retry_same_id"
        )
        before = {
            path.relative_to(saved).as_posix(): path.read_bytes()
            for path in saved.rglob("*") if path.is_file()
        }

        second = client.post(
            "/api/v1/scan/jobs?mode=upload_only",
            files=multipart_bytes(scan),
        )
        assert second.status_code == 200
        receipt = second.json()
        assert receipt["ok"] is True
        assert receipt["idempotent"] is True
        assert receipt["service_job_id"] == first.json()["service_job_id"]
        after = {
            path.relative_to(saved).as_posix(): path.read_bytes()
            for path in saved.rglob("*") if path.is_file()
        }
        assert after == before


def test_upload_only_survives_service_restart(
    monkeypatch, tmp_path: Path
) -> None:
    data_root = tmp_path / "case" / "data"
    scan = copy_scan(
        REAL_SCAN, tmp_path / "scan11", frame_count=11,
        scan_id="scan_restart_upload_only",
    )
    with app_client(monkeypatch, tmp_path / "case") as client:
        first = client.post(
            "/api/v1/scan/jobs?mode=upload_only",
            files=multipart_bytes(scan),
        )
        assert first.status_code == 200
        service_job_id = first.json()["service_job_id"]

    monkeypatch.setattr(service_module, "validate_scan", fake_validate)
    monkeypatch.setattr(service_module, "run_pipeline", complete_runner)
    app = create_app(ServiceConfig(
        data_root=data_root,
        pipeline_root=Path(r"C:\ec\_deltar_p4_pipeline"),
    ))
    with TestClient(app) as client:
        status = client.get(f"/api/v1/scan/jobs/{service_job_id}")
        assert status.status_code == 200
        body = status.json()
        assert body["status"] == "completed"
        assert body["mode"] == "upload_only"
        assert body["pipeline_job_id"] is None


def test_unknown_upload_mode_rejected_before_storage(
    monkeypatch, tmp_path: Path
) -> None:
    scan = copy_scan(REAL_SCAN, tmp_path / "scan", scan_id="bad_mode")
    with app_client(monkeypatch, tmp_path / "case") as client:
        response = client.post(
            "/api/v1/scan/jobs?mode=other",
            files=multipart_bytes(scan),
        )
        assert response.status_code == 422
        assert response.json()["error_code"] == "E_UPLOAD_MODE"
        assert not (
            tmp_path / "case" / "data" / "validated" / "bad_mode"
        ).exists()


def test_header_rejections(monkeypatch, tmp_path: Path) -> None:
    with app_client(monkeypatch, tmp_path) as client:
        nonmultipart = client.post("/api/v1/scan/jobs", content=b"x", headers={"content-type": "application/json"})
        assert nonmultipart.status_code == 415
        assert nonmultipart.json()["error_code"] == "E_REQUEST_CONTENT_TYPE"
        too_large = client.post("/api/v1/scan/jobs", content=b"x", headers={
            "content-type": "multipart/form-data; boundary=x", "content-length": "6291457"})
        assert too_large.status_code == 413
        assert too_large.json()["error_code"] == "E_UPLOAD_TOO_LARGE"


def test_twelve_frames_rejected_before_storage(monkeypatch, tmp_path: Path) -> None:
    scan = copy_scan(REAL_SCAN, tmp_path / "scan12", frame_count=11,
                     scan_id="scan_12")
    payload = json.loads((scan / "scan.json").read_text(encoding="utf-8"))
    payload["frame_count"] = 12
    files = [("scan_json", ("scan.json", json.dumps(payload).encode(),
                            "application/json"))]
    with app_client(monkeypatch, tmp_path / "case12") as client:
        response = client.post("/api/v1/scan/jobs", files=files)
        assert response.status_code == 422
        assert response.json()["error_code"] == "E_SCAN_JSON"


def test_upload_limit_boundary_is_exact(monkeypatch, tmp_path: Path) -> None:
    headers = {"content-type": "multipart/form-data; boundary=x"}
    with app_client(monkeypatch, tmp_path / "boundary") as client:
        at_limit = client.post("/api/v1/scan/jobs", content=b"--x--\r\n",
                               headers={**headers,
                                        "content-length": "6291456"})
        assert at_limit.status_code != 413
        above_limit = client.post("/api/v1/scan/jobs", content=b"--x--\r\n",
                                  headers={**headers,
                                           "content-length": "6291457"})
        assert above_limit.status_code == 413
        assert above_limit.json()["error_code"] == "E_UPLOAD_TOO_LARGE"


def test_unknown_duplicate_missing_and_path_filename(monkeypatch, tmp_path: Path) -> None:
    scan = copy_scan(REAL_SCAN, tmp_path / "scan", scan_id="negative")
    valid = multipart_bytes(scan)
    cases = [
        (valid + [("unknown", ("x", b"x", "application/octet-stream"))], "E_MULTIPART_FIELD"),
        (valid + [valid[0]], "E_MULTIPART_DUPLICATE"),
        (valid[:-1], "E_MULTIPART_MISSING"),
        ([(name, ("../scan.json", data, ctype)) if name == "scan_json" else (name, (filename, data, ctype))
          for name, (filename, data, ctype) in valid], "E_PART_FILENAME"),
    ]
    for index, (files, code) in enumerate(cases):
        with app_client(monkeypatch, tmp_path / f"case{index}") as client:
            response = client.post("/api/v1/scan/jobs", files=files)
            assert response.status_code == 422
            assert response.json()["error_code"] == code
            assert "Traceback" not in response.text


def test_wrong_image_and_large_json_rejected(monkeypatch, tmp_path: Path) -> None:
    scan = copy_scan(REAL_SCAN, tmp_path / "scan", scan_id="sizes")
    valid = multipart_bytes(scan)
    wrong_image = [(name, (filename, data[:-1], ctype)) if name.endswith("image_rgb565") else (name, (filename, data, ctype))
                   for name, (filename, data, ctype) in valid]
    with app_client(monkeypatch, tmp_path / "image") as client:
        response = client.post("/api/v1/scan/jobs", files=wrong_image)
        assert response.status_code == 422
        assert response.json()["error_code"] == "E_IMAGE_SIZE"
    large = list(valid)
    large[1] = (large[1][0], ("frame.json", b"{" + b" " * 65536 + b"}", "application/json"))
    with app_client(monkeypatch, tmp_path / "json") as client:
        response = client.post("/api/v1/scan/jobs", files=large)
        assert response.status_code == 413
        assert response.json()["error_code"] == "E_PART_TOO_LARGE"


def test_busy_and_active_idempotency(monkeypatch, tmp_path: Path) -> None:
    release = threading.Event()

    def blocked(config, storage, record, transition):
        release.wait(3)
        complete_runner(config, storage, record, transition)

    scan_a = copy_scan(REAL_SCAN, tmp_path / "a", scan_id="scan_a")
    scan_b = copy_scan(REAL_SCAN, tmp_path / "b", scan_id="scan_b")
    with app_client(monkeypatch, tmp_path / "case", blocked) as client:
        first = client.post("/api/v1/scan/jobs", files=multipart_bytes(scan_a))
        assert first.status_code == 202
        same = client.post("/api/v1/scan/jobs", files=multipart_bytes(scan_a))
        assert same.status_code == 202
        assert same.json()["service_job_id"] == first.json()["service_job_id"]
        assert same.json()["idempotent"] is True
        busy = client.post("/api/v1/scan/jobs", files=multipart_bytes(scan_b))
        assert busy.status_code == 503
        assert busy.json()["error_code"] == "E_SERVICE_BUSY"
        before = client.get(first.json()["result_url"])
        assert before.status_code == 409
        release.set()
        wait_status(client, first.json()["status_url"])


def test_completed_idempotency_and_not_found(monkeypatch, tmp_path: Path) -> None:
    scan = copy_scan(REAL_SCAN, tmp_path / "scan", scan_id="done")
    with app_client(monkeypatch, tmp_path / "case") as client:
        first = client.post("/api/v1/scan/jobs", files=multipart_bytes(scan)).json()
        wait_status(client, first["status_url"])
        again = client.post("/api/v1/scan/jobs", files=multipart_bytes(scan))
        assert again.status_code == 200
        assert again.json()["idempotent"] is True
        assert client.get("/api/v1/scan/jobs/svc_missing").status_code == 404


def test_restart_reconstructs_completed_and_fails_stale(monkeypatch, tmp_path: Path) -> None:
    data = tmp_path / "data"
    storage = ServiceStorage(data)
    result_dir = storage.result_dir("complete")
    result_dir.mkdir()
    record = {"schema_version": "deltar_scan_service_job_internal.v1", "service_job_id": "svc_" + "a" * 32,
              "pipeline_job_id": None, "scan_id": "complete", "status": "running", "pipeline_result_status": None,
              "created_at": "2026-07-18T00:00:00Z", "started_at": None, "finished_at": None, "error": None}
    storage.atomic_json(result_dir / "service_job.json", record)
    storage.atomic_json(result_dir / "result.json", {"schema_version": "deltar_scan_result.v1.1", "status": "success",
                        "scan_id": "complete", "job_id": "job_restart", "objects": []})
    storage.atomic_json(result_dir / "job_report.json", {"schema_version": "deltar_pc_job_report.v1.1",
                        "scan_id": "complete", "job_id": "job_restart", "result_status": "success"})
    stale_dir = storage.validated_dir("stale")
    stale_dir.mkdir()
    stale = dict(record, service_job_id="svc_" + "b" * 32, scan_id="stale")
    storage.atomic_json(stale_dir / "service_job.json", stale)
    monkeypatch.setattr(service_module, "validate_scan", fake_validate)
    monkeypatch.setattr(service_module, "run_pipeline", complete_runner)
    with TestClient(create_app(ServiceConfig(data_root=data, pipeline_root=Path(r"C:\ec\_deltar_p4_pipeline")))) as client:
        completed = client.get(f"/api/v1/scan/jobs/{record['service_job_id']}").json()
        failed = client.get(f"/api/v1/scan/jobs/{stale['service_job_id']}").json()
        assert completed["status"] == "failed"
        assert completed["error"]["error_code"] == "E_DECISION_SCHEMA"
        assert failed["status"] == "failed"
        assert failed["error"]["message"] == "stale_failed_no_resume"


def test_visualization_http_assets_are_same_origin_png_and_content_is_unchanged(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        service_module, "vision_health",
        lambda: {"online": True, "status_code": 200, "health": {"status": "ok"}},
    )
    config = ServiceConfig(data_root=tmp_path / "data", pipeline_root=tmp_path)
    app = create_app(config)
    manager = app.state.scan_manager
    scan_id = "scan_viewer"
    result_dir = manager.storage.result_dir(scan_id)
    frames = []
    source_png = (
        Path(r"C:\ec\_deltar_scan_service_data\results\scan_20260803_083540_000") /
        "logs" / "frame_000000" / "source_rgb.png"
    ).read_bytes()
    attempt_root = (
        result_dir.parent / ".frozen_attempts" / scan_id /
        "generation_0001_attempt_1"
    )
    for sequence in range(11):
        image = result_dir / "logs" / f"frame_{sequence:06d}" / "source_rgb.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(source_png)
        attempt_image = (
            attempt_root / "logs" / f"frame_{sequence:06d}" / "source_rgb.png"
        )
        frames.append(
            f'<section id="frame-{sequence}"><img src="{attempt_image.as_uri()}">bbox object-{sequence}</section>'
        )
    html = result_dir / "visualization" / "index.html"
    html.parent.mkdir()
    original = (
        '<div class="frame-nav">' + "".join(f'<a href="#frame-{i}">{i}</a>' for i in range(11)) +
        '</div><aside class="detail">固定详情面板</aside>' + "".join(frames)
    )
    html.write_text(original, encoding="utf-8")
    record = {
        "service_job_id": "svc_viewer", "scan_id": scan_id, "status": "completed",
        "visualization_html_path": str(html),
    }
    manager._index(record)

    with TestClient(app) as client:
        viewer = client.get(f"/api/v1/vision-console/scans/{scan_id}/visualization")
        assert viewer.status_code == 200
        urls = re.findall(r'src="([^"]+/source\.png)"', viewer.text)
        assert len(urls) == 11
        assert urls == [
            f"/api/v1/vision-console/scans/{scan_id}/scene-assets/"
            f"frame_{sequence:06d}/source.png"
            for sequence in range(11)
        ]
        assert "file:///" not in viewer.text
        assert viewer.text.count("bbox object-") == 11
        assert viewer.text.count('href="#frame-') == 11
        assert "固定详情面板" in viewer.text
        assert html.read_text(encoding="utf-8") == original
        for url in urls:
            image = client.get(url)
            assert image.status_code == 200
            assert image.headers["content-type"] == "image/png"
            assert image.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_portable_visualization_redirects_and_serves_disk_bytes_without_rewrite(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        service_module, "vision_health",
        lambda: {"online": True, "status_code": 200, "health": {"status": "ok"}},
    )
    app = create_app(ServiceConfig(data_root=tmp_path / "data", pipeline_root=tmp_path))
    manager = app.state.scan_manager
    scan_id = "scan_portable"
    result_dir = manager.storage.result_dir(scan_id)
    refs = []
    for sequence in range(11):
        frame_id = f"frame_{sequence:06d}"
        image = result_dir / "logs" / frame_id / "source_rgb.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"\x89PNG\r\n\x1a\nportable")
        refs.append(f'<img src="../logs/{frame_id}/source_rgb.png">')
    html = result_dir / "visualization" / "index.html"
    html.parent.mkdir()
    raw = (
        '<meta name="viewer-asset-mode" content="portable_relative_v1">' + "".join(refs)
    ).encode("utf-8")
    html.write_bytes(raw)
    manager._index({
        "service_job_id": "svc_portable", "scan_id": scan_id, "status": "completed",
        "visualization_html_path": str(html),
    })
    unindexed = manager.storage.result_dir("scan_unindexed")
    unindexed_image = unindexed / "logs" / "frame_000000" / "source_rgb.png"
    unindexed_image.parent.mkdir(parents=True)
    unindexed_image.write_bytes(b"\x89PNG\r\n\x1a\nunindexed")

    with TestClient(app) as client:
        old = client.get(
            f"/api/v1/vision-console/scans/{scan_id}/visualization",
            follow_redirects=False,
        )
        assert old.status_code == 307
        assert old.headers["location"].endswith(f"/{scan_id}/visualization/")
        viewer = client.get(old.headers["location"])
        assert viewer.status_code == 200
        assert viewer.content == raw
        assert viewer.headers["x-visionpc-legacy-rewrite"] == "false"
        for sequence in range(11):
            frame_id = f"frame_{sequence:06d}"
            image = client.get(
                f"/api/v1/vision-console/scans/{scan_id}/logs/{frame_id}/source_rgb.png"
            )
            assert image.status_code == 200
            assert image.headers["content-type"] == "image/png"
            assert image.headers["x-content-type-options"] == "nosniff"
        assert client.get(
            f"/api/v1/vision-console/scans/{scan_id}/logs/frame_000011/source_rgb.png"
        ).status_code == 404
        assert client.get(
            "/api/v1/vision-console/scans/missing/logs/frame_000000/source_rgb.png"
        ).status_code == 404
        assert client.get(
            "/api/v1/vision-console/scans/scan_unindexed/logs/frame_000000/source_rgb.png"
        ).status_code == 404
        blocked = (
            f"/api/v1/vision-console/scans/{scan_id}/logs/frame_..%2F../source_rgb.png",
            f"/api/v1/vision-console/scans/{scan_id}/logs/%2e%2e/source_rgb.png",
            f"/api/v1/vision-console/scans/{scan_id}/logs/C:%5CWindows/source_rgb.png",
            f"/api/v1/vision-console/scans/{scan_id}/logs/frame_000000/not_source.png",
        )
        assert all(client.get(path).status_code == 404 for path in blocked)


def test_vision_console_uses_canonical_visualization_url() -> None:
    assert "encodeURIComponent(c.scan_id)+'/visualization/'" in VISION_CONSOLE_HTML


def test_visualization_asset_route_rejects_traversal_and_non_png(tmp_path: Path) -> None:
    config = ServiceConfig(data_root=tmp_path / "data", pipeline_root=tmp_path)
    app = create_app(config)
    manager = app.state.scan_manager
    scan_id = "scan_safe"
    result_dir = manager.storage.result_dir(scan_id)
    html = result_dir / "visualization" / "index.html"
    html.parent.mkdir(parents=True)
    html.write_text("viewer", encoding="utf-8")
    (result_dir / "directory.png").mkdir()
    (result_dir / "not-image.txt").write_text("x", encoding="utf-8")
    manager._index({
        "service_job_id": "svc_safe", "scan_id": scan_id, "status": "completed",
        "visualization_html_path": str(html),
    })
    base = f"/api/v1/vision-console/scans/{scan_id}/assets/"
    with TestClient(app) as client:
        paths = (
            "../outside.png",
            "%2e%2e%2foutside.png",
            "%252e%252e%252foutside.png",
            "C:%5CWindows%5Cwin.ini.png",
            "not-image.txt",
            "directory.png",
        )
        assert all(client.get(base + path).status_code in {404, 422} for path in paths)
        assert client.get(
            "/api/v1/vision-console/scans/missing/assets/logs/frame_000000/source_rgb.png"
        ).status_code == 404
        source_base = f"/api/v1/vision-console/scans/{scan_id}/scene-assets/"
        assert client.get(source_base + "frame_000011/source.png").status_code == 404
        assert client.get(source_base + "../frame_000000/source.png").status_code == 404
        assert client.get(
            "/api/v1/vision-console/scans/missing/scene-assets/frame_000000/source.png"
        ).status_code == 404


@pytest.mark.skipif(
    not all(
        (Path(r"C:\ec\_deltar_scan_service_data\results") / scan_id / "visualization" / "index.html").is_file()
        for scan_id in ("scan_20260803_025133_000", "scan_20260803_083540_000")
    ),
    reason="retained target viewer scans are unavailable",
)
def test_retained_target_scan_viewers_serve_all_22_pngs_over_http() -> None:
    data_root = Path(r"C:\ec\_deltar_scan_service_data")
    app = create_app(ServiceConfig(
        data_root=data_root,
        pipeline_root=Path(r"C:\ec\_deltar_p4_pipeline"),
    ))
    with TestClient(app) as client:
        for scan_id in ("scan_20260803_025133_000", "scan_20260803_083540_000"):
            original_path = data_root / "results" / scan_id / "visualization" / "index.html"
            original = original_path.read_text(encoding="utf-8")
            viewer = client.get(f"/api/v1/vision-console/scans/{scan_id}/visualization")
            assert viewer.status_code == 200
            urls = list(dict.fromkeys(re.findall(r'src="([^"]+/source\.png)"', viewer.text)))
            assert len(urls) == 11
            assert "file:///" not in viewer.text
            assert viewer.text.count('href="#frame-') == 11
            assert 'class="detail-panel"' in viewer.text
            normalized_original = re.sub(r"file:///[^\"'<>\s]+source_rgb\.png", "<PNG_URL>", original)
            normalized_http = re.sub(
                rf"/api/v1/vision-console/scans/{scan_id}/scene-assets/"
                r"frame_\d{6}/source\.png",
                "<PNG_URL>", viewer.text,
            )
            assert normalized_http == normalized_original
            for url in urls:
                image = client.get(url)
                assert image.status_code == 200
                assert image.headers["content-type"] == "image/png"
                assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
                assert int.from_bytes(image.content[16:20], "big") == 640
                assert int.from_bytes(image.content[20:24], "big") == 360
