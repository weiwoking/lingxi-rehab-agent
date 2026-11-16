"""One-way copy/verify/delete consolidation for R3 session lifecycle state."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .manifest import sha256_file


SESSION_ID_RE = re.compile(r"^[0-9]{8}_TASK[0-9]{3}$")
SCHEMA = "rehab-training-data-session-lifecycle-migration/v1"


def _legacy_jobs_root(root: Path) -> Path:
    return root / "operations" / "jobs"


def _legacy_cloud_queue(root: Path) -> Path:
    return root / "sync" / "queue.json"


def _target_job_state(root: Path, session_id: str) -> Path:
    return root / "sessions" / session_id / "runtime" / "job_state.json"


def _target_cloud_queue(root: Path) -> Path:
    return root / "operations" / "queue" / "cloud_sync.json"


def plan_lifecycle_migration(root: Path) -> dict[str, Any]:
    authority = Path(root).resolve()
    jobs: list[dict[str, Any]] = []
    legacy_jobs = _legacy_jobs_root(authority)
    if legacy_jobs.is_dir():
        for source in sorted(legacy_jobs.glob("*.json")):
            session_id = source.stem
            if not SESSION_ID_RE.fullmatch(session_id):
                raise ValueError(f"invalid legacy job filename: {source.name}")
            session_exists = (authority / "sessions" / session_id).is_dir()
            jobs.append(
                {
                    "session_id": session_id,
                    "source": str(source),
                    "target": str(_target_job_state(authority, session_id)) if session_exists else None,
                    "size": source.stat().st_size,
                    "sha256": sha256_file(source),
                    "disposition": "migrate" if session_exists else "drop_orphan",
                }
            )
    cloud_source = _legacy_cloud_queue(authority)
    cloud = None
    if cloud_source.is_file():
        cloud = {
            "source": str(cloud_source),
            "target": str(_target_cloud_queue(authority)),
            "size": cloud_source.stat().st_size,
            "sha256": sha256_file(cloud_source),
        }
    return {
        "schema": SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "authority_root": str(authority),
        "jobs": jobs,
        "cloud_queue": cloud,
        "job_count": len(jobs),
        "migrated_job_count": sum(item["disposition"] == "migrate" for item in jobs),
        "orphan_job_count": sum(item["disposition"] == "drop_orphan" for item in jobs),
    }


def _copy_and_verify(source: Path, target: Path, expected_size: int, expected_sha: str) -> None:
    if target.exists():
        if not target.is_file() or target.stat().st_size != expected_size or sha256_file(target) != expected_sha:
            raise FileExistsError(f"conflicting lifecycle target: {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{os.getpid()}.migration.tmp")
    if temporary.exists():
        raise FileExistsError(f"migration temporary already exists: {temporary}")
    try:
        with source.open("rb") as source_handle, temporary.open("xb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        if temporary.stat().st_size != expected_size or sha256_file(temporary) != expected_sha:
            raise IOError(f"lifecycle copy verification failed: {source}")
        temporary.replace(target)
        if target.stat().st_size != expected_size or sha256_file(target) != expected_sha:
            raise IOError(f"lifecycle target verification failed: {target}")
    finally:
        temporary.unlink(missing_ok=True)


def execute_lifecycle_migration(root: Path, manifest_path: Path) -> dict[str, Any]:
    plan = plan_lifecycle_migration(root)
    manifest = Path(manifest_path)
    if manifest.exists():
        raise FileExistsError(f"migration manifest already exists: {manifest}")

    for item in plan["jobs"]:
        if item["disposition"] == "migrate":
            _copy_and_verify(
                Path(item["source"]),
                Path(item["target"]),
                int(item["size"]),
                str(item["sha256"]),
            )
    cloud = plan["cloud_queue"]
    if cloud:
        _copy_and_verify(
            Path(cloud["source"]),
            Path(cloud["target"]),
            int(cloud["size"]),
            str(cloud["sha256"]),
        )

    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary_manifest = manifest.with_suffix(manifest.suffix + ".tmp")
    temporary_manifest.write_text(
        json.dumps({**plan, "copy_verification": "PASS"}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_manifest.replace(manifest)

    for item in plan["jobs"]:
        Path(item["source"]).unlink()
    legacy_jobs = _legacy_jobs_root(Path(plan["authority_root"]))
    try:
        legacy_jobs.rmdir()
    except OSError:
        pass
    if cloud:
        Path(cloud["source"]).unlink()
        try:
            Path(cloud["source"]).parent.rmdir()
        except OSError:
            pass

    verification = plan_lifecycle_migration(Path(plan["authority_root"]))
    if verification["job_count"] != 0 or verification["cloud_queue"] is not None:
        raise IOError("legacy lifecycle sources remain after migration")
    final = {**plan, "copy_verification": "PASS", "delete_verification": "PASS"}
    temporary_manifest.write_text(
        json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_manifest.replace(manifest)
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--execute", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        if arguments.execute:
            if arguments.manifest is None:
                parser.error("--execute requires --manifest")
            result = execute_lifecycle_migration(arguments.root, arguments.manifest)
        else:
            result = plan_lifecycle_migration(arguments.root)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print(f"error={error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
