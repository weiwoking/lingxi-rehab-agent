from __future__ import annotations

import hashlib
import subprocess
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

import deltar_p4_pipeline.scan_service as service_module
import deltar_p4_pipeline.scan_service_worker as worker_module
from deltar_p4_pipeline.scan_service import JobManager, ServiceConfig, create_app
from deltar_p4_pipeline.scan_service_storage import ServiceStorage
from deltar_p4_pipeline.scan_service_worker import WorkerConfig
from tests.scan_service_helpers import copy_scan, multipart_bytes
from tests.test_stage8b4b_decision_service import (
    REAL_SCAN,
    fake_validate,
    write_package,
)


def test_package_is_ready_before_full_scene_and_get_is_side_effect_free(
    monkeypatch, tmp_path: Path
) -> None:
    decision_published = threading.Event()
    finish_scene = threading.Event()

    def early_runner(_config, storage, record, transition):
        folder = storage.result_dir(record["scan_id"])
        folder.mkdir()
        raw = write_package(folder, record["scan_id"])
        record.update(
            {
                "status": "decision_ready",
                "pipeline_job_id": "job_early",
                "pipeline_result_status": "success",
                "package_ready": True,
                "package_length": len(raw),
                "package_sha256": hashlib.sha256(raw).hexdigest(),
                "full_scene_status": "running",
                "full_scene_ready": False,
                "error": None,
            }
        )
        transition(record)
        decision_published.set()
        finish_scene.wait(3)
        record.update(
            {
                "status": "completed",
                "full_scene_status": "ready",
                "full_scene_ready": True,
            }
        )
        transition(record)

    monkeypatch.setattr(service_module, "validate_scan", fake_validate)
    monkeypatch.setattr(service_module, "run_pipeline", early_runner)
    scan_id = "scan_stage8b4b2_early"
    scan = copy_scan(REAL_SCAN, tmp_path / "scan", scan_id=scan_id)
    config = ServiceConfig(
        data_root=tmp_path / "data",
        pipeline_root=Path(r"C:\ec\_deltar_p4_pipeline"),
    )
    with TestClient(create_app(config)) as api:
        assert api.post(
            "/api/v1/scan/jobs", files=multipart_bytes(scan)
        ).status_code == 202
        assert decision_published.wait(2)
        status = api.get(
            f"/api/v1/scans/{scan_id}/decision-status"
        ).json()
        assert status["status"] == "decision_ready"
        assert status["package_ready"] is True
        assert status["full_scene_ready"] is False
        first = api.get(f"/api/v1/scans/{scan_id}/decision-package")
        second = api.get(f"/api/v1/scans/{scan_id}/decision-package")
        assert first.content == second.content
        assert status["package_sha256"] == hashlib.sha256(first.content).hexdigest()
        finish_scene.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            completed = api.get(
                f"/api/v1/scans/{scan_id}/decision-status"
            ).json()
            if completed["full_scene_ready"]:
                break
            time.sleep(0.01)
        assert completed["status"] == "decision_ready"
        assert completed["full_scene_ready"] is True


def _full_scene_fixture(tmp_path: Path) -> tuple[ServiceStorage, dict, bytes, Path]:
    storage = ServiceStorage(tmp_path / "data")
    scan_id = "scan_full_scene"
    storage.validated_dir(scan_id).mkdir()
    result = storage.result_dir(scan_id)
    result.mkdir()
    package = b'{"immutable":true}\n'
    (result / "stage8b4a_p4_decision_package.v1.json").write_bytes(package)
    html = result / "visualization" / "index.html"
    html.parent.mkdir()
    html.write_text("<!doctype html><title>scene</title>", encoding="utf-8")
    record = {
        "schema_version": "deltar_scan_service_job_internal.v1",
        "service_job_id": "svc_" + "a" * 32,
        "scan_id": scan_id,
        "status": "decision_ready",
        "created_at": "2026-07-26T00:00:00Z",
        "package_ready": True,
        "package_length": len(package),
        "package_sha256": hashlib.sha256(package).hexdigest(),
        "full_scene_ready": False,
    }
    return storage, record, package, html


def test_browser_open_failure_preserves_ready_package(
    monkeypatch, tmp_path: Path
) -> None:
    storage, record, package, html = _full_scene_fixture(tmp_path)
    monkeypatch.setattr(
        worker_module,
        "_run_process",
        lambda *_args, **kwargs: subprocess.CompletedProcess(
            kwargs["command"], 0, b"", b""
        ),
    )
    monkeypatch.setattr(worker_module, "_visualization_html", lambda _path: html)
    monkeypatch.setattr(
        worker_module,
        "open_visualization",
        lambda _path: (_ for _ in ()).throw(OSError("browser unavailable")),
    )
    transitions = []
    config = WorkerConfig(
        pipeline_root=Path(r"C:\ec\_deltar_p4_pipeline"),
        worker_python=Path("python"),
        profile_bundle=Path(r"C:\ec\_deltar_p4_pipeline\profiles"),
        auto_open_visualization=True,
    )
    worker_module._finish_full_scene(
        config,
        storage,
        record,
        lambda value: transitions.append(dict(value)),
        allow_auto_open=True,
    )
    assert record["status"] == "completed"
    assert record["full_scene_ready"] is True
    assert record["visualization_open_attempts"] == 1
    assert "browser unavailable" in record["visualization_open_error"]
    assert (
        storage.result_dir(record["scan_id"])
        / "stage8b4a_p4_decision_package.v1.json"
    ).read_bytes() == package
    assert all(item["status"] != "failed" for item in transitions)


def test_full_scene_failure_and_auto_open_disabled_preserve_decision(
    monkeypatch, tmp_path: Path
) -> None:
    storage, record, package, _html = _full_scene_fixture(tmp_path)
    monkeypatch.setattr(
        worker_module,
        "_run_process",
        lambda *_args, **kwargs: subprocess.CompletedProcess(
            kwargs["command"], 9, b"", b"render failed"
        ),
    )
    opened = []
    monkeypatch.setattr(
        worker_module, "open_visualization", lambda path: opened.append(path)
    )
    config = WorkerConfig(
        pipeline_root=Path(r"C:\ec\_deltar_p4_pipeline"),
        worker_python=Path("python"),
        profile_bundle=Path(r"C:\ec\_deltar_p4_pipeline\profiles"),
        auto_open_visualization=False,
    )
    worker_module._finish_full_scene(
        config, storage, record, lambda _value: None, allow_auto_open=True
    )
    assert record["status"] == "decision_ready"
    assert record["package_ready"] is True
    assert record["full_scene_ready"] is False
    assert record["full_scene_error_code"] == "E_FULL_SCENE_FAILED"
    assert opened == []
    assert (
        storage.result_dir(record["scan_id"])
        / "stage8b4a_p4_decision_package.v1.json"
    ).read_bytes() == package


def test_results_and_validated_job_mirrors_are_synchronized(
    tmp_path: Path,
) -> None:
    config = ServiceConfig(
        data_root=tmp_path / "data",
        pipeline_root=Path(r"C:\ec\_deltar_p4_pipeline"),
    )
    manager = JobManager(config)
    scan_id = "scan_mirror"
    manager.storage.validated_dir(scan_id).mkdir()
    manager.storage.result_dir(scan_id).mkdir()
    record = {
        "schema_version": "deltar_scan_service_job_internal.v1",
        "service_job_id": "svc_" + "b" * 32,
        "scan_id": scan_id,
        "status": "decision_ready",
        "created_at": "2026-07-26T00:00:00Z",
        "package_ready": True,
        "full_scene_ready": False,
    }
    manager._index(record)
    manager.transition(record)
    result_record = manager.storage.read_json(
        manager.storage.result_dir(scan_id) / "service_job.json"
    )
    validated_record = manager.storage.read_json(
        manager.storage.validated_dir(scan_id) / "service_job.json"
    )
    assert validated_record == result_record

