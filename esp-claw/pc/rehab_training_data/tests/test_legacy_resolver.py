from pathlib import Path

import pytest

from pc.rehab_training_data.paths import DEFAULT_TRAINING_ROOT
from pc.rehab_training_data.resolver import (
    TrainingDataResolver,
    get_doctor_root,
    get_score_root,
    get_training_root,
)
from pc.rehab_training_data.session_identity import InvalidSessionId


def test_default_roots_are_the_unified_authority() -> None:
    assert get_score_root() == DEFAULT_TRAINING_ROOT
    assert get_training_root() == DEFAULT_TRAINING_ROOT
    assert get_doctor_root() == DEFAULT_TRAINING_ROOT


def test_score_and_doctor_session_resolution_has_no_side_effects(tmp_path: Path) -> None:
    score_root = tmp_path / "unified"
    resolver = TrainingDataResolver(training_root=score_root)

    assert resolver.resolve_score_session("20260814_TASK009") == (
        score_root / "sessions" / "20260814_TASK009"
    )
    assert resolver.resolve_doctor_session("P-0217", "20260814_TASK009") == (
        score_root / "sessions" / "20260814_TASK009"
    )
    assert resolver.score_staging_session_path("20260814_TASK009") == (
        score_root / "operations" / "staging" / "uploads" / "20260814_TASK009"
    )
    assert resolver.job_status_path("20260814_TASK009") == (
        score_root / "sessions" / "20260814_TASK009" / "runtime" / "job_state.json"
    )
    assert resolver.session_input_path("20260814_TASK009") == (
        score_root / "sessions" / "20260814_TASK009" / "input"
    )
    assert resolver.session_input_file_path(
        "20260814_TASK009", "DATA_SEQ0000000001.csv"
    ) == (
        score_root
        / "sessions"
        / "20260814_TASK009"
        / "input"
        / "DATA_SEQ0000000001.csv"
    )
    assert resolver.session_scoring_path("20260814_TASK009") == (
        score_root / "sessions" / "20260814_TASK009" / "scoring"
    )
    assert resolver.running_root() == score_root / "operations" / "running"
    assert resolver.locks_root() == score_root / "operations" / "locks"
    assert resolver.sync_queue_path() == (
        score_root / "operations" / "queue" / "cloud_sync.json"
    )
    assert not score_root.exists()
    assert not (score_root / "sessions").exists()


def test_windows_roots_are_preserved_exactly() -> None:
    resolver = TrainingDataResolver(
        training_root=Path(r"C:\ec\_rehab_training_data"),
    )

    assert str(resolver.get_score_root()) == r"C:\ec\_rehab_training_data"
    assert str(resolver.resolve_score_session("20260814_TASK009")).endswith(
        r"_rehab_training_data\sessions\20260814_TASK009"
    )
    assert str(resolver.resolve_doctor_session("P-0217", "20260814_TASK009")).endswith(
        r"_rehab_training_data\sessions\20260814_TASK009"
    )


@pytest.mark.parametrize(
    "invalid",
    ["", ".", "..", "../escape", r"folder\session", "folder/session", "CON", "bad:name"],
)
def test_score_session_traversal_and_invalid_paths_are_rejected(invalid: str) -> None:
    resolver = TrainingDataResolver()
    with pytest.raises(InvalidSessionId):
        resolver.resolve_score_session(invalid)


@pytest.mark.parametrize("patient_id", ["..", "../P-0217", r"patients\P-0217", "NUL"])
def test_doctor_patient_traversal_is_rejected(patient_id: str) -> None:
    resolver = TrainingDataResolver()
    with pytest.raises(InvalidSessionId):
        resolver.resolve_doctor_session(patient_id, "20260814_TASK009")


@pytest.mark.parametrize("file_name", ["", ".", "..", "../task.json", r"x\\task.json", "x/task.json"])
def test_session_input_file_traversal_is_rejected(file_name: str) -> None:
    resolver = TrainingDataResolver()
    with pytest.raises(ValueError):
        resolver.session_input_file_path("20260814_TASK009", file_name)
