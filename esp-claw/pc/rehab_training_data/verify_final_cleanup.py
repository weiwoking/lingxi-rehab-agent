"""Read-only final verifier for the R3-3 authority cleanup."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TRAINING_ROOT = Path(r"C:\ec\_rehab_training_data")
ASSETS_ROOT = Path(r"C:\ec\_rehab_scoring_assets")
ARCHIVE_ROOT = Path(r"C:\ec\_rehab_archive\rehab_training_data_r3_3_20260820_01")
REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_ROOTS = (
    REPO_ROOT / "pc" / "rehab_score_service" / "runtime_data",
    REPO_ROOT
    / "pc"
    / "rehab_doctor_twin"
    / "backend"
    / "data"
    / "patients"
    / "P-0217"
    / "sessions",
    REPO_ROOT / "pc" / "rehab_doctor_twin" / "backend" / "runtime_data" / "cloud_sync",
)
R3_2_MANIFESTS = Path(
    r"C:\ec\_codex_reports\rehab_training_data_r3_2_authority_cutover_20260820_01\migration_manifests"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_cleanup_manifest(path: Path) -> dict[str, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    count = 0
    total = 0
    for entry in payload["files"]:
        target = Path(entry["target"])
        expected_size = int(entry["size"])
        expected_hash = str(entry["sha256"]).lower()
        if not target.is_file():
            raise FileNotFoundError(f"manifest target missing: {target}")
        if target.stat().st_size != expected_size or sha256_file(target) != expected_hash:
            raise OSError(f"manifest target mismatch: {target}")
        count += 1
        total += expected_size
    return {"files": count, "bytes": total}


def verify_r3_targets(selected_session: str) -> tuple[dict[str, int], list[dict[str, Any]]]:
    checked = 0
    total = 0
    selected: list[dict[str, Any]] = []
    for manifest_path in sorted(R3_2_MANIFESTS.glob("*.json")):
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = payload.get("files")
        if not isinstance(files, list):
            continue
        for entry in files:
            target = TRAINING_ROOT / Path(entry["target_path"])
            expected_size = int(entry["size"])
            expected_hash = str(entry["sha256"]).lower()
            if not target.is_file():
                raise FileNotFoundError(f"training target missing: {target}")
            if target.stat().st_size != expected_size or sha256_file(target) != expected_hash:
                raise OSError(f"training target mismatch: {target}")
            checked += 1
            total += expected_size
            if manifest_path.stem == selected_session:
                selected.append(
                    {
                        "target": str(target),
                        "size": expected_size,
                        "sha256": expected_hash,
                        "matches_pre_cleanup_manifest": True,
                    }
                )
    return {"files": checked, "bytes": total}, selected


def verify_black_box(session_id: str) -> dict[str, Any]:
    from rehab_score_service.cloud_interface.score_job_store import ScoreJobStore
    from rehab_score_service.cloud_interface.score_job_worker import ScoreJobWorker

    session_root = TRAINING_ROOT / "sessions" / session_id
    full_path = session_root / "scoring" / "full_result.json"
    report_path = session_root / "doctor" / "report.json"
    trajectories = sorted((session_root / "doctor" / "trajectory").glob("*.json"))
    metadata_path = session_root / "session.json"
    task_path = session_root / "input" / "task.json"
    csv_paths = sorted((session_root / "input").glob("DATA_SEQ*.csv"))
    required = [full_path, report_path, metadata_path, task_path, *trajectories, *csv_paths]
    if not trajectories or not csv_paths or not all(path.is_file() for path in required):
        raise FileNotFoundError(f"selected black-box fixture is incomplete: {session_id}")

    full = json.loads(full_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    trajectory = json.loads(trajectories[0].read_text(encoding="utf-8"))
    store = ScoreJobStore(TRAINING_ROOT)
    worker = ScoreJobWorker(store, lambda **_: {})
    compact = worker.compact_result(session_id)

    if full.get("session_id") != session_id or compact.get("session_id") != session_id:
        raise ValueError("score/compact session identity changed")
    if not isinstance(full.get("actions"), list) or not full["actions"]:
        raise ValueError("full result actions missing")
    if not isinstance(report.get("action_summaries"), list):
        raise ValueError("Doctor report action summaries missing")
    if not trajectory.get("playback_available") or not isinstance(trajectory.get("frames"), list):
        raise ValueError("trajectory/signal frames unavailable")

    return {
        "session_id": session_id,
        "score": full.get("final_score"),
        "full_result_sha256": sha256_file(full_path),
        "compact_schema": compact.get("schema_version"),
        "compact_average_score": compact.get("average_score"),
        "compact_utf8_bytes": len(json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
        "report_sha256": sha256_file(report_path),
        "trajectory_sha256": sha256_file(trajectories[0]),
        "action_count": len(full["actions"]),
        "report_action_summary_count": len(report["action_summaries"]),
        "trajectory_count": len(trajectories),
        "signal_frame_count": len(trajectory["frames"]),
        "task_present": True,
        "csv_present": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--session-id", default="20260807_TASK058")
    args = parser.parse_args()

    if any(path.exists() for path in LEGACY_ROOTS):
        raise OSError("one or more legacy authority roots still exist")
    if not (TRAINING_ROOT / "sync" / "queue.json").is_file():
        raise FileNotFoundError("unified Cloud queue is missing")

    asset_summary = verify_cleanup_manifest(args.report_dir / "asset_manifest.json")
    archive_summary = verify_cleanup_manifest(args.report_dir / "archive_manifest.json")
    training_summary, selected_entries = verify_r3_targets(args.session_id)
    result = {
        "schema": "rehab-training-data-r3-3-final-verification/v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "legacy_roots_absent": [str(path) for path in LEGACY_ROOTS],
        "training_authority": str(TRAINING_ROOT),
        "scoring_assets_authority": str(ASSETS_ROOT),
        "archive_root": str(ARCHIVE_ROOT),
        "training_targets": training_summary,
        "scoring_assets": asset_summary,
        "archives": archive_summary,
        "selected_session_manifest_entries": selected_entries,
        "black_box": verify_black_box(args.session_id),
        "unified_cloud_queue_present": True,
        "status": "PASS",
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    output = args.report_dir / "final_verification.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
