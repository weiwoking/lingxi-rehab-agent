from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import deltar_p4_pipeline.scan_service_worker as worker_module
from deltar_p4_pipeline.scan_service import ServiceConfig
from deltar_p4_pipeline.scan_service_contracts import ServiceError
from deltar_p4_pipeline.scan_service_storage import ServiceStorage
from deltar_p4_pipeline.scan_service_worker import (
    FORMAL_PIPELINE_TIMEOUT_SECONDS,
    PipelineProgressTailer,
    WorkerConfig,
    run_pipeline,
    validate_result_files,
    worker_environment,
)
from scripts.run_scan_service import PHASE6_WINNER_ENV, activate_phase6_winner, parser


def config(tmp_path: Path, **changes) -> WorkerConfig:
    values = dict(pipeline_root=tmp_path, worker_python=Path("python"), profile_bundle=tmp_path / "profiles")
    values.update(changes)
    return WorkerConfig(**values)


def write_result(storage: ServiceStorage, scan_id: str, *, status: str = "rejected", objects=None) -> bytes:
    folder = storage.result_dir(scan_id)
    folder.mkdir()
    result = {"schema_version": "deltar_scan_result.v1.1", "scan_id": scan_id, "job_id": "job_1",
              "status": status, "objects": [] if objects is None else objects}
    report = {"schema_version": "deltar_pc_job_report.v1.1", "scan_id": scan_id, "job_id": "job_1",
              "result_status": status}
    storage.atomic_json(folder / "result.json", result, replace=False)
    storage.atomic_json(folder / "job_report.json", report, replace=False)
    return (folder / "result.json").read_bytes()


def test_rejected_is_publishable_completed_result(tmp_path: Path) -> None:
    storage = ServiceStorage(tmp_path / "data")
    raw = write_result(storage, "scan")
    result, returned = validate_result_files(config(tmp_path), storage, "scan")
    assert result["status"] == "rejected"
    assert returned == raw


def test_result_object_and_byte_caps(tmp_path: Path) -> None:
    storage = ServiceStorage(tmp_path / "data")
    write_result(storage, "objects", objects=[{}] * 101)
    with pytest.raises(ServiceError) as objects:
        validate_result_files(config(tmp_path), storage, "objects")
    assert objects.value.code == "E_RESULT_OBJECT_LIMIT"
    storage2 = ServiceStorage(tmp_path / "data2")
    write_result(storage2, "bytes")
    with pytest.raises(ServiceError) as size:
        validate_result_files(config(tmp_path, max_result_bytes=10), storage2, "bytes")
    assert size.value.code == "E_RESULT_TOO_LARGE"


def test_worker_environment_is_allowlisted(tmp_path: Path) -> None:
    env = worker_environment(config(tmp_path))
    assert env["PYTHONPATH"] == str((tmp_path / "src").resolve())
    assert env["PYTHONNOUSERSITE"] == "1"
    assert "USERNAME" not in env


def test_formal_pipeline_timeout_contract_is_300_seconds() -> None:
    assert FORMAL_PIPELINE_TIMEOUT_SECONDS == 300
    assert WorkerConfig.job_timeout_seconds == 300
    assert ServiceConfig.job_timeout_seconds == 300
    assert parser().parse_args([]).job_timeout_seconds == 300


def test_production_launcher_activates_complete_phase6_winner_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in PHASE6_WINNER_ENV:
        monkeypatch.setenv(key, "0")

    activate_phase6_winner()

    assert PHASE6_WINNER_ENV == {
        "DELTAR_PHASE6_LAB": "1",
        "DELTAR_PHASE6_GPU_LOOKAHEAD": "1",
        "DELTAR_PHASE6_STAGE7D_LOOKAHEAD": "1",
        "DELTAR_PHASE6_SOURCE_PNG_PARALLEL": "1",
        "DELTAR_PHASE6_STAGE6_REQUIRED_REGION": "1",
        "DELTAR_PHASE6_STAGE7D_RANSAC_BATCH": "1",
        "DELTAR_PHASE6_STAGE7D_PNG_PAIR_PARALLEL": "1",
    }
    assert {key: os.environ[key] for key in PHASE6_WINNER_ENV} == PHASE6_WINNER_ENV


def test_formal_pipeline_timeout_keeps_original_error_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*_args, timeout_seconds: int, **_kwargs):
        assert timeout_seconds == 300
        raise subprocess.TimeoutExpired(
            cmd=["formal-scan"], timeout=timeout_seconds,
            output=b"", stderr=b"",
        )

    monkeypatch.setattr(worker_module, "_run_process", timeout)
    record = {"scan_id": "scan_timeout", "frame_count": 11}
    transitions: list[dict] = []

    run_pipeline(
        config(tmp_path), ServiceStorage(tmp_path / "data"), record,
        lambda value: transitions.append(dict(value)),
    )

    assert record["status"] == "failed"
    assert record["error"]["error_code"] == "E_PIPELINE_TIMEOUT"
    assert record["error"]["timeout_seconds"] == 300
    assert transitions[-1]["error"]["error_code"] == "E_PIPELINE_TIMEOUT"


def test_pipeline_progress_tailer_is_live_newline_strict_and_deduplicates(tmp_path: Path) -> None:
    import json
    import time

    path = tmp_path / "logs" / "pipeline_progress.jsonl"
    received: list[dict] = []
    tailer = PipelineProgressTailer(path, received.append, poll_seconds=0.05)
    tailer.start()
    path.parent.mkdir(parents=True)
    event = {
        "schema_version": "visionpc.pipeline.progress.v1", "event_id": 1,
        "timestamp": "2026-08-03T10:20:17.000000Z", "scan_id": "scan_a",
        "phase": "frame_processing", "subphase": "frame", "status": "started",
        "frame_id": "frame_000000", "frame_index": 1, "frame_total": 11,
        "current": 1, "total": 11, "elapsed_ms": 0, "message": "第一帧",
        "metrics": {}, "error": None,
    }
    raw = json.dumps(event, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    time.sleep(0.12)
    assert received == []
    with path.open("ab") as handle:
        handle.write(b"\n" + raw + b"\n" + b"{bad}\n")
        handle.flush()
    deadline = time.monotonic() + 1
    while not received and time.monotonic() < deadline:
        time.sleep(0.02)
    tailer.child_done.set()
    tailer.finish()
    assert [item["event_id"] for item in received] == [1]
    assert any("malformed" in warning for warning in tailer.warnings)
