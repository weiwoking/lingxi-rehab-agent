import json
from pathlib import Path

import pytest

from pc.rehab_training_data.cutover_migration import (
    execute_cloud_queue_cutover,
    execute_cutover,
    normalize_cloud_queue_paths,
    plan_cutover,
)
from pc.rehab_training_data.manifest import sha256_file


SESSION_ID = "20260814_TASK009"


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    score = tmp_path / "legacy-score" / SESSION_ID
    doctor = tmp_path / "legacy-doctor" / SESSION_ID
    dedicated = score / "result" / "dedicated_scoring" / SESSION_ID
    trajectories = doctor / "scoring" / "trajectories"
    dedicated.mkdir(parents=True)
    trajectories.mkdir(parents=True)
    (score / "task.json").write_text('{"task_name":"fixture"}', encoding="utf-8")
    (score / "DATA_SEQ0000000001.csv").write_text("log_seq\n1\n", encoding="utf-8")
    (score / "job_status.json").write_text('{"state":"succeeded"}', encoding="utf-8")
    (score / "result" / "score_result.json").write_text('{"valid":true}', encoding="utf-8")
    (dedicated / "session_summary.json").write_text('{"session_id":"fixture"}', encoding="utf-8")
    (doctor / "session.json").write_text('{"source_mode":"device"}', encoding="utf-8")
    (doctor / "report.json").write_text('{"title":"fixture"}', encoding="utf-8")
    (trajectories / "rep_01_trajectory.json").write_text('{"frames":[]}', encoding="utf-8")
    (doctor / "task.json").write_text('{"duplicate":true}', encoding="utf-8")
    (doctor / "score_result.json").write_text('{"duplicate":true}', encoding="utf-8")
    return score.parent, doctor.parent


def test_cutover_dry_run_has_no_writes_and_filters_doctor_fact_copies(tmp_path: Path) -> None:
    score_root, doctor_root = _fixture(tmp_path)
    target = tmp_path / "unified"
    plan = plan_cutover(score_root, doctor_root, target)

    assert not target.exists()
    assert len(plan.sessions) == 1
    targets = {entry.target_path for entry in plan.sessions[0].entries}
    assert f"sessions/{SESSION_ID}/input/task.json" in targets
    assert f"sessions/{SESSION_ID}/scoring/full_result.json" in targets
    assert f"sessions/{SESSION_ID}/runtime/job_state.json" in targets
    assert f"sessions/{SESSION_ID}/session.json" in targets
    assert f"sessions/{SESSION_ID}/doctor/report.json" in targets
    assert "doctor:task.json" in plan.sessions[0].skipped
    assert "doctor:score_result.json" in plan.sessions[0].skipped


def test_cutover_copy_hash_verify_and_manifest(tmp_path: Path) -> None:
    score_root, doctor_root = _fixture(tmp_path)
    target = tmp_path / "unified"
    manifests = tmp_path / "manifests"
    plan = plan_cutover(score_root, doctor_root, target)

    execute_cutover(plan, manifests)

    manifest = json.loads((manifests / f"{SESSION_ID}.json").read_text(encoding="utf-8"))
    assert manifest["file_count"] == len(plan.sessions[0].entries)
    for entry in plan.sessions[0].entries:
        copied = target / entry.target_path
        assert copied.stat().st_size == entry.size
        assert sha256_file(copied) == entry.sha256
    assert not (target / "sessions" / SESSION_ID / "task.json").exists()
    assert not (target / "sessions" / SESSION_ID / "score_result.json").exists()

    with pytest.raises(FileExistsError):
        execute_cutover(plan, tmp_path / "other-manifests")


def test_cloud_queue_copy_is_exclusive_and_hash_verified(tmp_path: Path) -> None:
    source = tmp_path / "legacy" / "queue.json"
    source.parent.mkdir()
    source.write_text('{"sessions": {}}\n', encoding="utf-8")
    target_root = tmp_path / "unified"
    manifest = tmp_path / "evidence" / "cloud_queue.json"

    execute_cloud_queue_cutover(source, target_root, manifest)

    copied = target_root / "operations" / "queue" / "cloud_sync.json"
    assert copied.read_bytes() == source.read_bytes()
    evidence = json.loads(manifest.read_text(encoding="utf-8"))
    assert evidence["artifact"] == "cloud_sync_queue"
    assert evidence["sha256"] == sha256_file(source)
    with pytest.raises(FileExistsError):
        execute_cloud_queue_cutover(source, target_root, manifest)


def test_cloud_queue_path_normalization_preserves_state(tmp_path: Path) -> None:
    root = tmp_path / "unified"
    queue = root / "operations" / "queue" / "cloud_sync.json"
    queue.parent.mkdir(parents=True)
    queue.write_text(
        json.dumps(
            {
                "sessions": {
                    SESSION_ID: {
                        "status": "failed",
                        "attempt_count": 3,
                        "csv_file": "DATA_SEQ0000000001.csv",
                        "csv_path": r"C:\legacy\DATA_SEQ0000000001.csv",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    count = normalize_cloud_queue_paths(root, tmp_path / "normalization.json")

    normalized = json.loads(queue.read_text(encoding="utf-8"))["sessions"][SESSION_ID]
    assert count == 1
    assert normalized["status"] == "failed"
    assert normalized["attempt_count"] == 3
    assert normalized["csv_path"] == str(
        root / "sessions" / SESSION_ID / "input" / "DATA_SEQ0000000001.csv"
    )
