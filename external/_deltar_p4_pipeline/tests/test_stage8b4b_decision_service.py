from __future__ import annotations

import hashlib
import json
import time
import threading
from pathlib import Path

from fastapi.testclient import TestClient

import deltar_p4_pipeline.scan_service as service_module
from deltar_p4_pipeline.scan_service import ServiceConfig, create_app
from deltar_p4_pipeline.scan_service_storage import ServiceStorage
from deltar_p4_pipeline.stage8b4a_contracts import P4_PACKAGE_FILENAME
from tests.scan_service_helpers import copy_scan, multipart_bytes
from deltar_p4_pipeline.validation_receipt import capture_input_manifest


REAL_SCAN = Path(r"C:\ec\scan_test\scan_20260718_081523_000")
REAL_PACKAGE = Path(
    r"C:\ec\_codex_reports\stage3_formal_geometry_d_cutover_20260818_01"
    r"\evidence\replays\G001\stage8b4a_p4_decision_package.v1.json"
)


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


def write_package(folder: Path, scan_id: str) -> bytes:
    package = json.loads(REAL_PACKAGE.read_text(encoding="utf-8"))
    package["scan_id"] = scan_id
    raw = (
        json.dumps(
            package, ensure_ascii=False, indent=2, sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    (folder / P4_PACKAGE_FILENAME).write_bytes(raw)
    (folder / f"{P4_PACKAGE_FILENAME}.sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "\n", encoding="ascii"
    )
    return raw


def ready_runner(_config, storage: ServiceStorage, record, transition) -> None:
    folder = storage.result_dir(record["scan_id"])
    folder.mkdir()
    result = {
        "schema_version": "deltar_scan_result.v1.1",
        "status": "success", "scan_id": record["scan_id"],
        "job_id": "job_stage8b4b", "objects": [],
    }
    report = {
        "schema_version": "deltar_pc_job_report.v1.1",
        "result_status": "success", "scan_id": record["scan_id"],
        "job_id": "job_stage8b4b",
    }
    storage.atomic_json(folder / "result.json", result, replace=False)
    storage.atomic_json(folder / "job_report.json", report, replace=False)
    raw = write_package(folder, record["scan_id"])
    visualization = folder / "visualization"
    visualization.mkdir()
    html = visualization / "index.html"
    html.write_text("<!doctype html><title>ready</title>", encoding="utf-8")
    storage.atomic_json(
        visualization / "visualization_manifest.v1.json",
        {
            "visualization_complete": True,
            "html": {"path": "visualization/index.html"},
        },
    )
    record.update({
        "status": "completed", "pipeline_job_id": "job_stage8b4b",
        "pipeline_result_status": "success",
        "finished_at": "2026-07-25T00:00:00Z", "error": None,
        "package_length": len(raw),
        "package_sha256": hashlib.sha256(raw).hexdigest(),
        "package_ready": True,
        "full_scene_status": "ready",
        "full_scene_ready": True,
        "visualization_html_path": str(html.resolve()),
    })
    transition(record)


def failed_runner(_config, _storage, record, transition) -> None:
    record.update({
        "status": "failed", "finished_at": "2026-07-25T00:00:00Z",
        "error": {
            "error_code": "E_PIPELINE_FAILED",
            "message": "x" * 500,
        },
    })
    transition(record)


def client(monkeypatch, tmp_path: Path, runner) -> TestClient:
    monkeypatch.setattr(service_module, "validate_scan", fake_validate)
    monkeypatch.setattr(service_module, "run_pipeline", runner)
    return TestClient(create_app(ServiceConfig(
        data_root=tmp_path / "data",
        pipeline_root=Path(r"C:\ec\_deltar_p4_pipeline"),
    )))


def wait_decision(client_value: TestClient, scan_id: str) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        body = client_value.get(
            f"/api/v1/scans/{scan_id}/decision-status"
        ).json()
        if body["status"] in {"decision_ready", "failed"}:
            return body
        time.sleep(0.01)
    raise AssertionError("decision status did not become terminal")


def test_auto_start_idempotency_status_and_immutable_package(
    monkeypatch, tmp_path: Path
) -> None:
    scan_id = "scan_stage8b4b_ready"
    scan = copy_scan(REAL_SCAN, tmp_path / "scan", scan_id=scan_id)
    with client(monkeypatch, tmp_path / "case", ready_runner) as api:
        missing = api.get(
            "/api/v1/scans/not_present/decision-status"
        ).json()
        assert missing["status"] == "not_found"

        first = api.post(
            "/api/v1/scan/jobs", files=multipart_bytes(scan)
        )
        assert first.status_code == 202
        ready = wait_decision(api, scan_id)
        assert ready["status"] == "decision_ready"
        assert ready["package_ready"] is True
        assert ready["full_scene_ready"] is True
        assert ready["error_message"] is None

        package_a = api.get(
            f"/api/v1/scans/{scan_id}/decision-package"
        )
        package_b = api.get(
            f"/api/v1/scans/{scan_id}/decision-package"
        )
        assert package_a.status_code == 200
        assert package_a.content == package_b.content
        assert int(package_a.headers["content-length"]) == len(package_a.content)
        assert ready["package_length"] == len(package_a.content)
        assert ready["package_sha256"] == hashlib.sha256(
            package_a.content
        ).hexdigest()

        repeated = api.post(
            "/api/v1/scan/jobs", files=multipart_bytes(scan)
        )
        assert repeated.status_code == 200
        assert repeated.json()["service_job_id"] == first.json()[
            "service_job_id"
        ]
        assert repeated.json()["idempotent"] is True


def test_different_source_same_scan_id_is_rejected(
    monkeypatch, tmp_path: Path
) -> None:
    scan_id = "scan_stage8b4b_identity"
    scan = copy_scan(REAL_SCAN, tmp_path / "scan", scan_id=scan_id)
    with client(monkeypatch, tmp_path / "case", ready_runner) as api:
        first = api.post(
            "/api/v1/scan/jobs", files=multipart_bytes(scan)
        )
        assert first.status_code == 202
        wait_decision(api, scan_id)
        changed = multipart_bytes(scan)
        name, (filename, data, content_type) = changed[-1]
        changed[-1] = (
            name, (filename, data[:-1] + bytes([data[-1] ^ 1]), content_type)
        )
        conflict = api.post("/api/v1/scan/jobs", files=changed)
        assert conflict.status_code == 409
        assert conflict.json()["error_code"] == "E_SCAN_CONFLICT"


def test_processing_failed_not_ready_and_restart_recovery(
    monkeypatch, tmp_path: Path
) -> None:
    scan_id = "scan_stage8b4b_failed"
    scan = copy_scan(REAL_SCAN, tmp_path / "scan", scan_id=scan_id)
    with client(monkeypatch, tmp_path / "failed", failed_runner) as api:
        response = api.post(
            "/api/v1/scan/jobs", files=multipart_bytes(scan)
        )
        assert response.status_code == 202
        failed = wait_decision(api, scan_id)
        assert failed["status"] == "failed"
        assert len(failed["error_message"]) == 160
        package = api.get(
            f"/api/v1/scans/{scan_id}/decision-package"
        )
        assert package.status_code == 409
        assert package.headers["content-type"].startswith("application/json")

    ready_id = "scan_stage8b4b_restart"
    ready_scan = copy_scan(
        REAL_SCAN, tmp_path / "ready_scan", scan_id=ready_id
    )
    root = tmp_path / "restart"
    with client(monkeypatch, root, ready_runner) as api:
        api.post("/api/v1/scan/jobs", files=multipart_bytes(ready_scan))
        before = wait_decision(api, ready_id)
    with client(monkeypatch, root, ready_runner) as restarted:
        after = restarted.get(
            f"/api/v1/scans/{ready_id}/decision-status"
        ).json()
        assert after == before
        package = restarted.get(
            f"/api/v1/scans/{ready_id}/decision-package"
        )
        assert hashlib.sha256(package.content).hexdigest() == after[
            "package_sha256"
        ]


def test_processing_does_not_publish_a_fake_package(
    monkeypatch, tmp_path: Path
) -> None:
    release = threading.Event()

    def blocked(config, storage, record, transition):
        release.wait(3)
        ready_runner(config, storage, record, transition)

    scan_id = "scan_stage8b4b_processing"
    scan = copy_scan(REAL_SCAN, tmp_path / "scan", scan_id=scan_id)
    with client(monkeypatch, tmp_path / "case", blocked) as api:
        response = api.post(
            "/api/v1/scan/jobs", files=multipart_bytes(scan)
        )
        assert response.status_code == 202
        status = api.get(
            f"/api/v1/scans/{scan_id}/decision-status"
        )
        assert status.status_code == 200
        assert status.json()["status"] in {"queued", "processing"}
        assert status.json()["package_ready"] is False
        package = api.get(
            f"/api/v1/scans/{scan_id}/decision-package"
        )
        assert package.status_code == 425
        assert package.json()["error_code"] == "E_DECISION_NOT_READY"
        release.set()
        assert wait_decision(api, scan_id)["status"] == "decision_ready"
