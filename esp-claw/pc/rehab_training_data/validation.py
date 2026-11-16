"""Hash-based comparison for migration sources, targets, and manifests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .manifest import FileRecord, iter_regular_files, record_file


@dataclass(frozen=True)
class ValidationResult:
    missing: tuple[str, ...] = ()
    unexpected: tuple[str, ...] = ()
    size_mismatches: tuple[str, ...] = ()
    hash_mismatches: tuple[str, ...] = ()

    @property
    def matches(self) -> bool:
        return not (
            self.missing
            or self.unexpected
            or self.size_mismatches
            or self.hash_mismatches
        )

    def require_match(self) -> None:
        if not self.matches:
            raise ValueError(
                "validation failed: "
                f"missing={list(self.missing)}, unexpected={list(self.unexpected)}, "
                f"size_mismatches={list(self.size_mismatches)}, "
                f"hash_mismatches={list(self.hash_mismatches)}"
            )


def _records_by_path(records: Iterable[FileRecord]) -> dict[str, FileRecord]:
    indexed: dict[str, FileRecord] = {}
    for record in records:
        if record.path in indexed:
            raise ValueError(f"duplicate manifest path: {record.path}")
        indexed[record.path] = record
    return indexed


def records_from_manifest(manifest: Mapping[str, object]) -> list[FileRecord]:
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise ValueError("manifest files must be a list")
    records: list[FileRecord] = []
    for item in raw_files:
        if not isinstance(item, dict):
            raise ValueError("manifest file entry must be an object")
        records.append(
            FileRecord(
                path=str(item["path"]),
                size=int(item["size"]),
                sha256=str(item["sha256"]),
                source_path=str(item["source_path"]) if "source_path" in item else None,
            )
        )
    return records


def validate_records(target: Path, expected_records: Iterable[FileRecord]) -> ValidationResult:
    target_root = Path(target)
    expected = _records_by_path(expected_records)
    actual_records = [record_file(path, target_root) for path in iter_regular_files(target_root)]
    actual = _records_by_path(actual_records)
    expected_paths = set(expected)
    actual_paths = set(actual)
    shared = expected_paths & actual_paths
    return ValidationResult(
        missing=tuple(sorted(expected_paths - actual_paths)),
        unexpected=tuple(sorted(actual_paths - expected_paths)),
        size_mismatches=tuple(
            sorted(path for path in shared if expected[path].size != actual[path].size)
        ),
        hash_mismatches=tuple(
            sorted(path for path in shared if expected[path].sha256 != actual[path].sha256)
        ),
    )


def validate_manifest(target: Path, manifest: Mapping[str, object]) -> ValidationResult:
    return validate_records(target, records_from_manifest(manifest))


def compare_trees(source: Path, target: Path) -> ValidationResult:
    source_root = Path(source)
    expected = [record_file(path, source_root) for path in iter_regular_files(source_root)]
    return validate_records(target, expected)
