from pathlib import Path

from pc.rehab_training_data.manifest import sha256_file
from pc.rehab_training_data.session_layout_migration import (
    execute_layout_migration,
    plan_layout_migration,
)


def test_final_layout_copy_verify_delete(tmp_path: Path) -> None:
    root = tmp_path / "authority"
    session = root / "sessions" / "20260814_TASK009"
    metadata = session / "metadata"
    trajectories = session / "doctor" / "trajectories"
    metadata.mkdir(parents=True)
    trajectories.mkdir(parents=True)
    sources = {
        metadata / "session.json": b'{"session_id":"20260814_TASK009"}',
        metadata / "runtime_summary.json": b'{"state":"done"}',
        metadata / "session_v12_scoring.json": b'{"status":"completed"}',
        trajectories / "rep_01_trajectory.json": b'{"frames":[]}',
    }
    hashes = {}
    for path, content in sources.items():
        path.write_bytes(content)
        hashes[path.name] = sha256_file(path)
    manifest = tmp_path / "layout_manifest.json"

    plan = plan_layout_migration(root)
    assert plan["entry_count"] == 4
    assert plan["legacy_directory_count"] == 2
    result = execute_layout_migration(root, manifest)

    assert sha256_file(session / "session.json") == hashes["session.json"]
    assert sha256_file(session / "runtime" / "doctor_state.json") == hashes["runtime_summary.json"]
    assert sha256_file(session / "doctor" / "session_v12_scoring.json") == hashes["session_v12_scoring.json"]
    assert sha256_file(session / "doctor" / "trajectory" / "rep_01_trajectory.json") == hashes["rep_01_trajectory.json"]
    assert not metadata.exists()
    assert not trajectories.exists()
    assert result["copy_verification"] == "PASS"
    assert result["delete_verification"] == "PASS"


def test_final_layout_removes_empty_legacy_directories(tmp_path: Path) -> None:
    root = tmp_path / "authority"
    session = root / "sessions" / "20260814_TASK010"
    (session / "metadata").mkdir(parents=True)
    (session / "doctor" / "trajectories").mkdir(parents=True)

    result = execute_layout_migration(root, tmp_path / "layout_manifest.json")

    assert result["entry_count"] == 0
    assert result["legacy_directory_count"] == 2
    assert not (session / "metadata").exists()
    assert not (session / "doctor" / "trajectories").exists()
