from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

import deltar_p4_pipeline.scan_service_worker as worker
from deltar_p4_pipeline.scan_contracts import ScanContractError
from deltar_p4_pipeline.scan_service_storage import ServiceStorage
from deltar_p4_pipeline.scan_service_worker import (
    WorkerConfig,
    run_pipeline,
    structured_worker_error,
)
from deltar_p4_pipeline.stage6_schema import (
    FRAME_DIAGNOSTIC_CODE,
    FRAME_DIAGNOSTIC_DROPPED_WARNING,
    FRAME_DIAGNOSTIC_FALLBACK,
    normalize_frame_diagnostics,
)


def frame(errors=...) -> dict:
    value = {
        "frame_id": "frame_000000",
        "sequence": 0,
        "warnings": [],
        "objects": [],
    }
    if errors is not ...:
        value["errors"] = errors
    return value


@pytest.mark.parametrize("message", [None, "", "   "])
def test_missing_empty_or_blank_message_uses_fallback(message) -> None:
    diagnostic = {"code": "E_LOCAL"}
    if message is not None:
        diagnostic["message"] = message
    value = frame([diagnostic])
    audit = normalize_frame_diagnostics(value)
    assert value["errors"] == [{
        "code": "E_LOCAL", "message": FRAME_DIAGNOSTIC_FALLBACK
    }]
    assert audit[0]["message_fallback"] is True


def test_non_string_message_is_safely_normalized() -> None:
    value = frame([{"error_code": "E_LOCAL", "message": 42}])
    normalize_frame_diagnostics(value)
    assert value["errors"][0]["message"] == "42"


def test_overlong_message_is_bounded_for_frame_and_job_report_reuse() -> None:
    value = frame([{"code": "E_LOCAL", "message": "x" * 900}])
    normalize_frame_diagnostics(value)
    assert len(value["errors"][0]["message"]) == 512


def test_missing_code_uses_generic_code() -> None:
    value = frame([{"message": "usable diagnostic"}])
    normalize_frame_diagnostics(value)
    assert value["errors"] == [{
        "code": FRAME_DIAGNOSTIC_CODE,
        "message": "usable diagnostic",
    }]


def test_empty_diagnostic_is_dropped_with_bounded_warning() -> None:
    value = frame([{}, None])
    normalize_frame_diagnostics(value)
    assert value["errors"] == []
    assert FRAME_DIAGNOSTIC_DROPPED_WARNING in value["warnings"]


@pytest.mark.parametrize("errors", [..., [], None])
def test_missing_empty_or_invalid_diagnostic_array_is_legal(errors) -> None:
    value = frame() if errors is ... else frame(errors)
    normalize_frame_diagnostics(value)
    assert value["errors"] == []


def test_object_diagnostics_and_partial_frames_continue() -> None:
    first = frame([])
    first["objects"] = [{"errors": [{"code": "E_GEOMETRY", "message": ""}]}]
    second = frame([])
    second.update(frame_id="frame_000001", sequence=1)
    for item in (first, second):
        normalize_frame_diagnostics(item)
    assert first["objects"][0]["errors"][0]["message"] == (
        FRAME_DIAGNOSTIC_FALLBACK
    )
    assert [item["sequence"] for item in (first, second)] == [0, 1]


def test_core_nonfinite_gate_is_not_relaxed() -> None:
    from deltar_p4_pipeline.stage6_schema import validate_frame_result_v1_1

    with pytest.raises(ScanContractError):
        validate_frame_result_v1_1({
            "frame_id": "frame_000000",
            "sequence": 0,
            "warnings": [],
            "errors": [],
            "objects": [],
            "decision_value": float("nan"),
        })


def test_structured_worker_error_is_sanitized() -> None:
    raw = (
        b'noise\n{"status":"failed","error":{"code":'
        b'"E_FRAME_RESULT_SCHEMA","message":" expected   bounded text "}}\n'
    )
    assert structured_worker_error(raw) == (
        "E_FRAME_RESULT_SCHEMA", "expected bounded text"
    )
    assert structured_worker_error(b"Traceback: C:\\secret\\file.py") is None


def test_run_pipeline_persists_structured_error_for_decision_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = ServiceStorage(tmp_path / "data")
    scan_id = "scan_structured_error"
    storage.validated_dir(scan_id).mkdir(parents=True)
    record = {
        "scan_id": scan_id,
        "service_job_id": "svc_test",
        "status": "running",
    }
    stdout = json.dumps({
        "status": "failed",
        "error": {
            "code": "E_FRAME_RESULT_SCHEMA",
            "message": "expected bounded non-empty string",
            "field": "errors[0].message",
        },
    }).encode()
    monkeypatch.setattr(
        worker,
        "_run_process",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=7, stdout=stdout, stderr=b""
        ),
    )
    transitions: list[dict] = []
    run_pipeline(
        WorkerConfig(
            pipeline_root=tmp_path,
            worker_python=Path("python"),
            profile_bundle=tmp_path / "profiles",
        ),
        storage,
        record,
        lambda updated: transitions.append(deepcopy(updated)),
    )
    assert transitions[-1]["status"] == "failed"
    assert transitions[-1]["error"] == {
        "error_code": "E_FRAME_RESULT_SCHEMA",
        "message": "expected bounded non-empty string",
        "phase": "pipeline",
        "error_type": None,
        "failed_frame_count": None,
        "detail": "structured worker stdout error",
    }
