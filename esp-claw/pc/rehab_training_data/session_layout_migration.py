"""Copy/verify/delete migration from R3 metadata folders to the final session layout."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .manifest import sha256_file
from .session_identity import InvalidSessionId, validate_session_id


SCHEMA = "rehab-training-data-final-session-layout-migration/v1"


def _mappings(session_dir: Path) -> list[tuple[Path, Path, str]]:
    mappings = [
        (session_dir / "metadata" / "session.json", session_dir / "session.json", "session_descriptor"),
        (
            session_dir / "metadata" / "runtime_summary.json",
            session_dir / "runtime" / "doctor_state.json",
            "doctor_runtime_state",
        ),
        (
            session_dir / "metadata" / "session_v12_scoring.json",
            session_dir / "doctor" / "session_v12_scoring.json",
            "doctor_scoring_state",
        ),
    ]
    trajectories = session_dir / "doctor" / "trajectories"
    if trajectories.is_dir():
        for source in sorted(trajectories.rglob("*")):
            if source.is_symlink():
                raise ValueError(f"symbolic layout source is forbidden: {source}")
            if source.is_file():
                mappings.append(
                    (
                        source,
                        session_dir / "doctor" / "trajectory" / source.relative_to(trajectories),
                        "doctor_trajectory",
                    )
                )
    return mappings


def plan_layout_migration(root: Path) -> dict[str, Any]:
    authority = Path(root).resolve()
    sessions_root = authority / "sessions"
    entries: list[dict[str, Any]] = []
    legacy_directories: list[str] = []
    if sessions_root.is_dir():
        for session_dir in sorted(sessions_root.iterdir()):
            if not session_dir.is_dir():
                continue
            try:
                validate_session_id(session_dir.name)
            except InvalidSessionId:
                continue
            for legacy_directory in (
                session_dir / "metadata",
                session_dir / "doctor" / "trajectories",
            ):
                if legacy_directory.is_dir():
                    legacy_directories.append(str(legacy_directory))
            for source, target, artifact in _mappings(session_dir):
                if not source.is_file():
                    continue
                entries.append(
                    {
                        "session_id": session_dir.name,
                        "artifact": artifact,
                        "source": str(source),
                        "target": str(target),
                        "size": source.stat().st_size,
                        "sha256": sha256_file(source),
                    }
                )
    return {
        "schema": SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "authority_root": str(authority),
        "entry_count": len(entries),
        "session_count": len({entry["session_id"] for entry in entries}),
        "legacy_directory_count": len(legacy_directories),
        "legacy_directories": legacy_directories,
        "entries": entries,
    }


def _copy_verified(entry: dict[str, Any]) -> None:
    source = Path(entry["source"])
    target = Path(entry["target"])
    size = int(entry["size"])
    digest = str(entry["sha256"])
    if target.exists():
        if not target.is_file() or target.stat().st_size != size or sha256_file(target) != digest:
            raise FileExistsError(f"conflicting final session layout target: {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{os.getpid()}.layout.tmp")
    try:
        with source.open("rb") as source_handle, temporary.open("xb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        if temporary.stat().st_size != size or sha256_file(temporary) != digest:
            raise IOError(f"final session layout copy verification failed: {source}")
        temporary.replace(target)
        if target.stat().st_size != size or sha256_file(target) != digest:
            raise IOError(f"final session layout target verification failed: {target}")
    finally:
        temporary.unlink(missing_ok=True)


def execute_layout_migration(root: Path, manifest_path: Path) -> dict[str, Any]:
    plan = plan_layout_migration(root)
    manifest = Path(manifest_path)
    if manifest.exists():
        raise FileExistsError(f"layout migration manifest already exists: {manifest}")
    for entry in plan["entries"]:
        _copy_verified(entry)

    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary_manifest = manifest.with_suffix(manifest.suffix + ".tmp")
    temporary_manifest.write_text(
        json.dumps({**plan, "copy_verification": "PASS"}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_manifest.replace(manifest)

    for entry in plan["entries"]:
        source = Path(entry["source"])
        source.unlink()
    sessions_root = Path(plan["authority_root"]) / "sessions"
    session_directories = [path for path in sessions_root.iterdir() if path.is_dir()] if sessions_root.is_dir() else []
    for session_dir in session_directories:
        old_trajectory = session_dir / "doctor" / "trajectories"
        old_directories = (
            sorted(
                (path for path in old_trajectory.rglob("*") if path.is_dir()),
                key=lambda path: len(path.parts),
                reverse=True,
            )
            if old_trajectory.is_dir()
            else []
        )
        for directory in old_directories:
            directory.rmdir()
        if old_trajectory.is_dir():
            old_trajectory.rmdir()
        metadata = session_dir / "metadata"
        if metadata.is_dir():
            if any(metadata.iterdir()):
                raise IOError(f"unmapped legacy metadata remains: {metadata}")
            metadata.rmdir()

    verification = plan_layout_migration(Path(plan["authority_root"]))
    if verification["entry_count"] or verification["legacy_directory_count"]:
        raise IOError("old metadata or trajectory layout remains after migration")
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
            result = execute_layout_migration(arguments.root, arguments.manifest)
        else:
            result = plan_layout_migration(arguments.root)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print(f"error={error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
