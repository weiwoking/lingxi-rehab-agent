"""R3-2 copy-and-verify migration into the unified authority layout."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .manifest import sha256_file


SESSION_ID_RE = re.compile(r"^[0-9]{8}_TASK[0-9]{3}$")
CSV_FILE_RE = re.compile(r"^DATA_SEQ[0-9]{10}\.csv$")
SCHEMA = "rehab-training-data-cutover-manifest/v1"


@dataclass(frozen=True)
class CutoverEntry:
    source: str
    source_path: str
    target_path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class SessionCutoverPlan:
    session_id: str
    entries: tuple[CutoverEntry, ...]
    skipped: tuple[str, ...]

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries)


@dataclass(frozen=True)
class CutoverPlan:
    score_sessions_root: Path
    doctor_sessions_root: Path
    target_root: Path
    sessions: tuple[SessionCutoverPlan, ...]
    invalid_session_directories: tuple[str, ...]

    @property
    def file_count(self) -> int:
        return sum(len(session.entries) for session in self.sessions)

    @property
    def total_bytes(self) -> int:
        return sum(session.total_bytes for session in self.sessions)


def _score_target(relative: Path, session_id: str) -> Path | None:
    parts = relative.parts
    lowered = tuple(part.casefold() for part in parts)
    name = relative.name.casefold()
    session_prefix = Path("sessions") / session_id
    if len(parts) == 1 and name == "task.json":
        return session_prefix / "input" / "task.json"
    if len(parts) == 1 and re.fullmatch(r"data_seq[0-9]{10}\.csv", name):
        return session_prefix / "input" / relative.name
    if len(parts) == 1 and name == "job_status.json":
        return session_prefix / "runtime" / "job_state.json"
    if lowered == ("result", "score_result.json"):
        return session_prefix / "scoring" / "full_result.json"
    if len(parts) >= 2 and lowered[0] == "result":
        return session_prefix / "scoring" / "intermediate" / Path(*parts[1:])
    return None


def _doctor_target(relative: Path, session_id: str) -> Path | None:
    parts = relative.parts
    lowered = tuple(part.casefold() for part in parts)
    name = relative.name.casefold()
    session_prefix = Path("sessions") / session_id
    if lowered == ("session.json",):
        return session_prefix / "session.json"
    if lowered == ("runtime_summary.json",):
        return session_prefix / "runtime" / "doctor_state.json"
    if lowered == ("session_v12_scoring.json",):
        return session_prefix / "doctor" / "session_v12_scoring.json"
    if lowered == ("report.json",):
        return session_prefix / "doctor" / "report.json"
    if lowered == ("agent_trace.json",):
        return session_prefix / "doctor" / "presentation" / "agent_trace.json"
    if len(parts) >= 2 and lowered[0] == "scoring" and lowered[1] == "trajectories":
        return session_prefix / "doctor" / "trajectory" / Path(*parts[2:])
    if len(parts) == 1 and name.endswith(".jsonl"):
        return session_prefix / "doctor" / "logs" / relative.name
    return None


def _regular_files(root: Path):
    if not root.is_dir():
        return
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for directory in directories:
            if (current_path / directory).is_symlink():
                raise ValueError(f"symbolic directory is forbidden: {current_path / directory}")
        for filename in sorted(filenames):
            path = current_path / filename
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"non-regular source is forbidden: {path}")
            yield path


def _session_names(*roots: Path) -> tuple[list[str], list[str]]:
    names: set[str] = set()
    invalid: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if not path.is_dir():
                continue
            if SESSION_ID_RE.fullmatch(path.name):
                names.add(path.name)
            else:
                invalid.add(str(path))
    return sorted(names), sorted(invalid)


def plan_cutover(score_sessions_root: Path, doctor_sessions_root: Path, target_root: Path) -> CutoverPlan:
    score_root = Path(score_sessions_root).resolve()
    doctor_root = Path(doctor_sessions_root).resolve()
    target = Path(target_root).resolve()
    session_ids, invalid = _session_names(score_root, doctor_root)
    sessions: list[SessionCutoverPlan] = []
    for session_id in session_ids:
        entries: list[CutoverEntry] = []
        skipped: list[str] = []
        targets: set[str] = set()
        for source_name, source_root, classifier in (
            ("score", score_root / session_id, _score_target),
            ("doctor", doctor_root / session_id, _doctor_target),
        ):
            for source_path in _regular_files(source_root) or ():
                relative = source_path.relative_to(source_root)
                destination = classifier(relative, session_id)
                if destination is None:
                    skipped.append(f"{source_name}:{relative.as_posix()}")
                    continue
                target_key = destination.as_posix().casefold()
                if target_key in targets:
                    raise ValueError(f"multiple sources map to {destination}")
                targets.add(target_key)
                entries.append(
                    CutoverEntry(
                        source=source_name,
                        source_path=str(source_path),
                        target_path=destination.as_posix(),
                        size=source_path.stat().st_size,
                        sha256=sha256_file(source_path),
                    )
                )
        entries.sort(key=lambda entry: entry.target_path)
        if entries:
            sessions.append(
                SessionCutoverPlan(
                    session_id=session_id,
                    entries=tuple(entries),
                    skipped=tuple(sorted(skipped)),
                )
            )
    return CutoverPlan(
        score_sessions_root=score_root,
        doctor_sessions_root=doctor_root,
        target_root=target,
        sessions=tuple(sessions),
        invalid_session_directories=tuple(invalid),
    )


def _manifest(plan: CutoverPlan, session: SessionCutoverPlan) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "session_id": session.session_id,
        "sources": {
            "score": str(plan.score_sessions_root / session.session_id),
            "doctor": str(plan.doctor_sessions_root / session.session_id),
        },
        "target": str(plan.target_root / "sessions" / session.session_id),
        "files": [asdict(entry) for entry in session.entries],
        "file_count": len(session.entries),
        "total_bytes": session.total_bytes,
        "skipped": list(session.skipped),
    }


def execute_cutover(plan: CutoverPlan, manifest_root: Path) -> None:
    if plan.target_root.exists():
        raise FileExistsError(f"target authority already exists: {plan.target_root}")
    manifest_dir = Path(manifest_root)
    if manifest_dir.exists() and any(manifest_dir.iterdir()):
        raise FileExistsError(f"manifest directory is not empty: {manifest_dir}")
    for path in (
        plan.target_root / "sessions",
        plan.target_root / "operations" / "staging" / "uploads",
        plan.target_root / "operations" / "staging" / "scoring",
        plan.target_root / "operations" / "running",
        plan.target_root / "operations" / "locks",
        plan.target_root / "operations" / "queue",
    ):
        path.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    for session in plan.sessions:
        for entry in session.entries:
            source = Path(entry.source_path)
            destination = plan.target_root / Path(entry.target_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as source_handle, destination.open("xb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            if destination.stat().st_size != entry.size or sha256_file(destination) != entry.sha256:
                raise IOError(f"copy verification failed: {entry.target_path}")
        manifest_path = manifest_dir / f"{session.session_id}.json"
        with manifest_path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(_manifest(plan, session), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")


def execute_cloud_queue_cutover(source_queue: Path, target_root: Path, manifest_path: Path) -> None:
    """Copy the CloudSync queue without overwrite and verify the copied bytes."""

    source = Path(source_queue).resolve()
    target = Path(target_root).resolve() / "operations" / "queue" / "cloud_sync.json"
    manifest = Path(manifest_path)
    if not source.is_file() or source.is_symlink():
        raise FileNotFoundError(f"cloud queue source is not a regular file: {source}")
    if target.exists():
        raise FileExistsError(f"cloud queue target already exists: {target}")
    if manifest.exists():
        raise FileExistsError(f"cloud queue manifest already exists: {manifest}")
    expected_size = source.stat().st_size
    expected_sha = sha256_file(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as source_handle, target.open("xb") as target_handle:
        shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
    if target.stat().st_size != expected_size or sha256_file(target) != expected_sha:
        raise IOError("cloud queue copy verification failed")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "artifact": "cloud_sync_queue",
        "source": str(source),
        "target": str(target),
        "size": expected_size,
        "sha256": expected_sha,
    }
    with manifest.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def normalize_cloud_queue_paths(target_root: Path, manifest_path: Path) -> int:
    """Rebind copied queue records to unified inputs without changing queue state."""

    root = Path(target_root).resolve()
    queue_path = root / "operations" / "queue" / "cloud_sync.json"
    manifest = Path(manifest_path)
    if manifest.exists():
        raise FileExistsError(f"cloud queue normalization manifest already exists: {manifest}")
    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    sessions = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(sessions, dict):
        raise ValueError("cloud queue sessions object is missing")
    before_sha = sha256_file(queue_path)
    normalized = 0
    for session_id, item in sessions.items():
        if not SESSION_ID_RE.fullmatch(str(session_id)) or not isinstance(item, dict):
            continue
        csv_file = str(item.get("csv_file") or "")
        if not CSV_FILE_RE.fullmatch(csv_file):
            continue
        item["csv_path"] = str(root / "sessions" / str(session_id) / "input" / csv_file)
        normalized += 1
    temporary = queue_path.with_suffix(".json.migration.tmp")
    if temporary.exists():
        raise FileExistsError(f"cloud queue normalization temporary exists: {temporary}")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(queue_path)
    after_sha = sha256_file(queue_path)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    evidence = {
        "schema": SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "artifact": "cloud_sync_queue_path_normalization",
        "target": str(queue_path),
        "records_normalized": normalized,
        "before_sha256": before_sha,
        "after_sha256": after_sha,
    }
    with manifest.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(evidence, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return normalized


def format_plan(plan: CutoverPlan, execute: bool) -> str:
    return "\n".join(
        (
            f"mode={'EXECUTE' if execute else 'DRY-RUN'}",
            f"target={plan.target_root}",
            f"sessions={len(plan.sessions)}",
            f"files={plan.file_count}",
            f"bytes={plan.total_bytes}",
            f"invalid_session_directories={len(plan.invalid_session_directories)}",
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score-sessions-root", required=True, type=Path)
    parser.add_argument("--doctor-sessions-root", required=True, type=Path)
    parser.add_argument("--target-root", required=True, type=Path)
    parser.add_argument("--manifest-root", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.execute and arguments.manifest_root is None:
        parser.error("--execute requires --manifest-root")
    try:
        plan = plan_cutover(
            arguments.score_sessions_root,
            arguments.doctor_sessions_root,
            arguments.target_root,
        )
        print(format_plan(plan, arguments.execute))
        if arguments.execute:
            execute_cutover(plan, arguments.manifest_root)
            print("verification=PASS")
        else:
            print("writes_performed=0")
        return 0
    except (OSError, ValueError) as error:
        print(f"error={error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
