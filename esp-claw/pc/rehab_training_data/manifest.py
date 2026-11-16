"""Deterministic file manifests and SHA-256 helpers for offline migration."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence


MANIFEST_SCHEMA = "rehab-training-data-manifest/v1"
HASH_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class FileRecord:
    path: str
    size: int
    sha256: str
    source_path: str | None = None


def sha256_file(path: Path, chunk_size: int = HASH_CHUNK_SIZE) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def record_file(path: Path, relative_to: Path, *, output_path: str | None = None) -> FileRecord:
    file_path = Path(path)
    relative_path = file_path.relative_to(relative_to).as_posix()
    return FileRecord(
        path=output_path or relative_path,
        size=file_path.stat().st_size,
        sha256=sha256_file(file_path),
        source_path=relative_path if output_path is not None else None,
    )


def iter_regular_files(root: Path) -> Iterable[Path]:
    """Yield regular files in stable order and reject every symbolic link."""

    tree_root = Path(root)
    for current, directories, files in os.walk(tree_root, followlinks=False):
        current_path = Path(current)
        for directory in sorted(directories):
            if (current_path / directory).is_symlink():
                raise ValueError(f"symbolic links are not allowed: {current_path / directory}")
        for filename in sorted(files):
            path = current_path / filename
            if path.is_symlink():
                raise ValueError(f"symbolic links are not allowed: {path}")
            if not path.is_file():
                raise ValueError(f"non-regular file is not allowed: {path}")
            yield path


def build_manifest(
    source: Path,
    *,
    target: Path | None = None,
    session_id: str | None = None,
    records: Sequence[FileRecord] | None = None,
) -> dict[str, object]:
    source_root = Path(source)
    file_records = list(records) if records is not None else [
        record_file(path, source_root) for path in iter_regular_files(source_root)
    ]
    file_records.sort(key=lambda item: item.path)
    return {
        "schema": MANIFEST_SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id or source_root.name,
        "source": str(source_root),
        "target": str(target) if target is not None else "",
        "files": [
            {key: value for key, value in asdict(record).items() if value is not None}
            for record in file_records
        ],
        "file_count": len(file_records),
        "total_bytes": sum(record.size for record in file_records),
    }


def write_manifest(manifest: Mapping[str, object], path: Path) -> None:
    """Write a new manifest, refusing to replace any existing evidence file."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def load_manifest(path: Path) -> dict[str, object]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict) or value.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("unsupported or invalid manifest")
    return value
