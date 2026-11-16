from __future__ import annotations

import json
from pathlib import Path

import pytest

import deltar_p4_pipeline.scan_job as scan_job
from deltar_p4_pipeline.scan_contracts import ScanContractError


def _error(code: str, *, count: int = 101) -> ScanContractError:
    return ScanContractError(
        code,
        "diagnostic failed",
        "candidates",
        {"candidate_count": count, "max_candidates": 100},
    )


@pytest.mark.parametrize("candidate_count", [99, 100])
def test_stage8b2_at_or_below_limit_is_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate_count: int
) -> None:
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(
        scan_job,
        "write_scene_candidates_from_output",
        lambda _output: ({"candidate_count": candidate_count}, tmp_path / "sidecar.json"),
    )
    warnings: list[str] = []

    assert scan_job._run_stage8b2_diagnostic(
        tmp_path, scan_id="scan_fixture", warnings=warnings
    )
    assert warnings == []
    phase = json.loads((tmp_path / "logs" / "pipeline_phases.jsonl").read_text())
    assert phase["details"]["status"] == "success"


@pytest.mark.parametrize("candidate_count", [101, 102])
def test_stage8b2_overflow_is_recorded_without_vetoing_formal_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate_count: int
) -> None:
    (tmp_path / "logs").mkdir()
    package = tmp_path / "stage8b4a_p4_decision_package.v1.json"
    package.write_bytes(b'{"formal":"unchanged"}\n')
    package_before = package.read_bytes()

    def overflow(_output: Path) -> None:
        raise _error("E_STAGE8B2_TOO_MANY_CANDIDATES", count=candidate_count)

    monkeypatch.setattr(scan_job, "write_scene_candidates_from_output", overflow)
    warnings: list[str] = []

    assert not scan_job._run_stage8b2_diagnostic(
        tmp_path, scan_id="scan_fixture", warnings=warnings
    )
    assert warnings == ["W_STAGE8B2_TOO_MANY_CANDIDATES"]
    assert package.read_bytes() == package_before
    phase = json.loads((tmp_path / "logs" / "pipeline_phases.jsonl").read_text())
    assert phase["details"]["status"] == "overflow"
    assert phase["details"]["formal_package_preserved"] is True
    assert phase["details"]["error"]["code"] == "E_STAGE8B2_TOO_MANY_CANDIDATES"


@pytest.mark.parametrize(
    "error",
    [
        _error("E_STAGE8B2_CANDIDATES"),
        RuntimeError("generator exception"),
    ],
)
def test_stage8b2_non_overflow_errors_remain_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    (tmp_path / "logs").mkdir()

    def fail(_output: Path) -> None:
        raise error

    monkeypatch.setattr(scan_job, "write_scene_candidates_from_output", fail)

    with pytest.raises(type(error)):
        scan_job._run_stage8b2_diagnostic(
            tmp_path, scan_id="scan_fixture", warnings=[]
        )


def test_formal_package_failure_cannot_reach_diagnostic_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostic_called = False

    def diagnostic(*_args, **_kwargs) -> bool:
        nonlocal diagnostic_called
        diagnostic_called = True
        return True

    monkeypatch.setattr(scan_job, "_run_stage8b2_diagnostic", diagnostic)
    with pytest.raises(ScanContractError):
        raise ScanContractError(
            "E_STAGE8B_GATE", "formal package failed", "stage8b4a"
        )
    assert not diagnostic_called
