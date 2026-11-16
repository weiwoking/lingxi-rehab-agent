import json
from pathlib import Path

from pc.rehab_training_data.manifest import sha256_file
from pc.rehab_training_data.session_lifecycle_migration import (
    execute_lifecycle_migration,
    plan_lifecycle_migration,
)


SESSION_ID = "20260814_TASK009"
ORPHAN_ID = "20260814_TASK010"


def test_lifecycle_migration_copy_verify_delete_and_drop_orphan(tmp_path: Path) -> None:
    root = tmp_path / "authority"
    (root / "sessions" / SESSION_ID / "input").mkdir(parents=True)
    legacy_jobs = root / "operations" / "jobs"
    legacy_jobs.mkdir(parents=True)
    source = legacy_jobs / f"{SESSION_ID}.json"
    source.write_text(json.dumps({"job_id": SESSION_ID, "state": "succeeded"}), encoding="utf-8")
    orphan = legacy_jobs / f"{ORPHAN_ID}.json"
    orphan.write_text(json.dumps({"job_id": ORPHAN_ID, "state": "failed"}), encoding="utf-8")
    legacy_queue = root / "sync" / "queue.json"
    legacy_queue.parent.mkdir()
    legacy_queue.write_text(json.dumps({"sessions": {}}), encoding="utf-8")
    source_hash = sha256_file(source)
    queue_hash = sha256_file(legacy_queue)
    manifest = tmp_path / "migration_manifest.json"

    dry_run = plan_lifecycle_migration(root)
    assert dry_run["migrated_job_count"] == 1
    assert dry_run["orphan_job_count"] == 1
    assert source.is_file()

    result = execute_lifecycle_migration(root, manifest)

    target = root / "sessions" / SESSION_ID / "runtime" / "job_state.json"
    target_queue = root / "operations" / "queue" / "cloud_sync.json"
    assert sha256_file(target) == source_hash
    assert sha256_file(target_queue) == queue_hash
    assert not legacy_jobs.exists()
    assert not (root / "sync").exists()
    assert result["copy_verification"] == "PASS"
    assert result["delete_verification"] == "PASS"
    evidence = json.loads(manifest.read_text(encoding="utf-8"))
    assert evidence["orphan_job_count"] == 1
