from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def _source(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def test_score_server_activates_only_rehab_training_data_root() -> None:
    source = _source("pc/rehab_score_service/cloud_interface/score_server.py")
    root_block = source.split("\nTRAINING_DATA_ROOT =", 1)[1].split("\nRUNTIME_DATA_ROOT", 1)[0]
    assert "REHAB_TRAINING_DATA_ROOT" in root_block
    assert "REHAB_SCORE_RUNTIME_DATA_ROOT" not in root_block


def test_doctor_no_longer_calls_training_fact_copy_writers() -> None:
    runtime = _source("pc/rehab_doctor_twin/backend/app/pc_local_runtime_service.py")
    for forbidden in (
        "save_stream_csv(",
        "save_task(",
        "save_score_result(",
        "save_score_job_result(",
    ):
        assert forbidden not in runtime

    store = _source("pc/rehab_doctor_twin/backend/app/device_history_store.py")
    for forbidden_definition in (
        "def save_stream_csv",
        "def save_task",
        "def save_score_result",
        "def save_score_job_result",
    ):
        assert forbidden_definition not in store


def test_cloud_discovers_only_unified_input_directory() -> None:
    source = _source("pc/rehab_doctor_twin/backend/app/cloud_sync_service.py")
    candidate = source.split("def _build_light_candidate", 1)[1].split("def _task_finalized", 1)[0]
    assert 'input_dir = resolved_session_dir / "input"' in candidate
    assert 'task_path = input_dir / "task.json"' in candidate
    assert "csv_path.relative_to(input_dir.resolve())" in candidate
