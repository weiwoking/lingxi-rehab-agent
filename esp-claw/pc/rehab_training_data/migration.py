"""Conservative, offline copy-and-verify migration CLI.

This module is deliberately not imported by any production service.  Running it
without ``--execute`` performs a read-only dry run.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from rehab_training_data.manifest import FileRecord, build_manifest, sha256_file, write_manifest
    from rehab_training_data.session_identity import validate_session_id
    from rehab_training_data.validation import validate_records
else:
    from .manifest import FileRecord, build_manifest, sha256_file, write_manifest
    from .session_identity import validate_session_id
    from .validation import validate_records


EXCLUDED_DIRECTORY_NAMES = frozenset(
    {"cache", "tmp", "temp", "logs", "log", "build", "node_modules", "__pycache__", ".git"}
)
FULL_RESULT_NAMES = frozenset({"result.json", "score_result.json", "full_result.json"})
INTERMEDIATE_NAMES = frozenset(
    {"session_manifest.json", "session_summary.json", "session_score_report.md"}
)
SCORE_DIRECTORY_NAMES = frozenset(
    {
        "result",
        "scoring",
        "dedicated_scoring",
        "intermediate",
        "per_rep_scores",
        "repetitions",
        "rejected_repetitions",
        "scoring_rejections",
    }
)


@dataclass(frozen=True)
class MigrationEntry:
    source_path: Path
    source_relative: str
    target_relative: str
    size: int
    sha256: str

    def target_record(self) -> FileRecord:
        return FileRecord(
            path=self.target_relative,
            size=self.size,
            sha256=self.sha256,
            source_path=self.source_relative,
        )


@dataclass(frozen=True)
class MigrationPlan:
    source: Path
    target: Path
    session_id: str
    entries: tuple[MigrationEntry, ...]
    skipped: tuple[str, ...]

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries)

    def records(self) -> list[FileRecord]:
        return [entry.target_record() for entry in self.entries]

    def manifest(self) -> dict[str, object]:
        return build_manifest(
            self.source,
            target=self.target,
            session_id=self.session_id,
            records=self.records(),
        )


def _is_excluded(relative_path: Path) -> bool:
    return any(part.casefold() in EXCLUDED_DIRECTORY_NAMES for part in relative_path.parts[:-1])


def _relative_after(parts: tuple[str, ...], marker: str) -> Path:
    lowered = tuple(part.casefold() for part in parts)
    index = lowered.index(marker)
    remainder = parts[index + 1 :]
    return Path(*remainder) if remainder else Path(parts[-1])


def classify_target(relative_path: Path) -> Path | None:
    """Map an allow-listed legacy artifact into the R3 session layout."""

    if _is_excluded(relative_path):
        return None
    parts = relative_path.parts
    lowered = tuple(part.casefold() for part in parts)
    name = relative_path.name.casefold()

    if name == "task.json":
        return Path("input") / "task.json"

    if "trajectories" in lowered:
        return Path("doctor") / "trajectory" / _relative_after(parts, "trajectories")
    if "trajectory" in name:
        return Path("doctor") / "trajectory" / relative_path.name
    if name == "report.json":
        return Path("doctor") / "report.json"

    score_context = any(part in SCORE_DIRECTORY_NAMES for part in lowered)
    if relative_path.suffix.casefold() == ".csv":
        if score_context:
            return Path("scoring") / "intermediate" / relative_path
        return Path("input") / relative_path.name

    if name in FULL_RESULT_NAMES:
        return Path("scoring") / "full_result.json"
    if name in INTERMEDIATE_NAMES or score_context:
        return Path("scoring") / "intermediate" / relative_path
    return None


def _assert_non_overlapping(source: Path, target: Path) -> None:
    source_abs = Path(os.path.abspath(source))
    target_abs = Path(os.path.abspath(target))
    try:
        target_abs.relative_to(source_abs)
    except ValueError:
        pass
    else:
        raise ValueError("target must not be inside source")
    try:
        source_abs.relative_to(target_abs)
    except ValueError:
        pass
    else:
        raise ValueError("source must not be inside target")


def _walk_source(source: Path) -> Iterable[Path]:
    for current, directories, filenames in os.walk(source, followlinks=False):
        current_path = Path(current)
        kept_directories: list[str] = []
        for directory in sorted(directories):
            directory_path = current_path / directory
            relative = directory_path.relative_to(source)
            if directory_path.is_symlink():
                raise ValueError(f"symbolic links are not allowed: {directory_path}")
            if directory.casefold() not in EXCLUDED_DIRECTORY_NAMES and not _is_excluded(relative / "_"):
                kept_directories.append(directory)
        directories[:] = kept_directories
        for filename in sorted(filenames):
            path = current_path / filename
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"only regular files are allowed: {path}")
            yield path


def plan_migration(
    source: Path,
    target: Path,
    *,
    session_id: str | None = None,
) -> MigrationPlan:
    source_root = Path(source)
    target_root = Path(target)
    if not source_root.is_dir():
        raise ValueError(f"source is not a directory: {source_root}")
    _assert_non_overlapping(source_root, target_root)
    valid_session_id = validate_session_id(session_id or source_root.name)

    entries: list[MigrationEntry] = []
    skipped: list[str] = []
    destinations: dict[str, str] = {}
    for path in _walk_source(source_root):
        relative = path.relative_to(source_root)
        destination = classify_target(relative)
        if destination is None:
            skipped.append(relative.as_posix())
            continue
        destination_string = destination.as_posix()
        destination_key = destination_string.casefold()
        prior = destinations.get(destination_key)
        if prior is not None:
            raise ValueError(
                f"multiple source files map to {destination_string}: {prior}, {relative.as_posix()}"
            )
        destinations[destination_key] = relative.as_posix()
        entries.append(
            MigrationEntry(
                source_path=path,
                source_relative=relative.as_posix(),
                target_relative=destination_string,
                size=path.stat().st_size,
                sha256=sha256_file(path),
            )
        )
    entries.sort(key=lambda item: item.target_relative)
    if not entries:
        raise ValueError("source contains no allow-listed migration candidates")
    return MigrationPlan(
        source=source_root,
        target=target_root,
        session_id=valid_session_id,
        entries=tuple(entries),
        skipped=tuple(sorted(skipped)),
    )


def execute_migration(plan: MigrationPlan) -> None:
    """Copy a plan to a new target and verify every byte; never remove anything."""

    if plan.target.exists():
        raise FileExistsError(f"target already exists; overwrite is forbidden: {plan.target}")
    plan.target.mkdir(parents=True, exist_ok=False)
    for entry in plan.entries:
        destination = plan.target / Path(entry.target_relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with entry.source_path.open("rb") as source_handle, destination.open("xb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
        if destination.stat().st_size != entry.size or sha256_file(destination) != entry.sha256:
            raise IOError(f"copy verification failed: {entry.source_relative}")
    validate_records(plan.target, plan.records()).require_match()


def validate_manifest_output_path(plan: MigrationPlan, output: Path) -> None:
    """Keep evidence output outside both immutable source and session target."""

    output_abs = Path(os.path.abspath(output))
    if output_abs.exists():
        raise FileExistsError(f"manifest output already exists; overwrite is forbidden: {output}")
    for protected_root, label in ((plan.source, "source"), (plan.target, "target")):
        protected_abs = Path(os.path.abspath(protected_root))
        try:
            output_abs.relative_to(protected_abs)
        except ValueError:
            continue
        raise ValueError(f"manifest output must not be inside migration {label}")


def format_plan(plan: MigrationPlan, *, execute: bool) -> str:
    mode = "EXECUTE" if execute else "DRY-RUN"
    lines = [
        f"mode={mode}",
        f"session_id={plan.session_id}",
        f"source={plan.source}",
        f"target={plan.target}",
        f"will_copy_files={len(plan.entries)}",
        f"will_copy_bytes={plan.total_bytes}",
        f"skipped_files={len(plan.skipped)}",
    ]
    lines.extend(
        f"COPY {entry.source_relative} -> {entry.target_relative}" for entry in plan.entries
    )
    lines.extend(f"SKIP {path}" for path in plan.skipped)
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="legacy session directory")
    parser.add_argument("--target", type=Path, required=True, help="new, nonexistent session directory")
    parser.add_argument("--session-id", help="stable session ID; defaults to source directory name")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="plan only (the default)")
    mode.add_argument("--execute", action="store_true", help="copy to a new target and verify hashes")
    parser.add_argument(
        "--manifest-output",
        type=Path,
        help="write a new JSON manifest after a successful --execute",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.manifest_output is not None and not arguments.execute:
        parser.error("--manifest-output requires --execute; dry-run never writes files")
    try:
        plan = plan_migration(
            arguments.source,
            arguments.target,
            session_id=arguments.session_id,
        )
        if arguments.manifest_output is not None:
            validate_manifest_output_path(plan, arguments.manifest_output)
        print(format_plan(plan, execute=arguments.execute))
        if arguments.execute:
            execute_migration(plan)
            if arguments.manifest_output is not None:
                write_manifest(plan.manifest(), arguments.manifest_output)
            print("verification=PASS")
        else:
            print("writes_performed=0")
        return 0
    except (OSError, ValueError) as error:
        print(f"error={error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
