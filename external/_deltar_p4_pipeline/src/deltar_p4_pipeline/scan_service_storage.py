from __future__ import annotations

import json
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, BinaryIO

from .scan_service_contracts import ServiceError, assert_finite, reject_constant


class ServiceStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.incoming = self.root / "incoming"
        self.validated = self.root / "validated"
        self.results = self.root / "results"
        self.failed = self.root / "failed"
        for folder in (self.incoming, self.validated, self.results, self.failed):
            folder.mkdir(parents=True, exist_ok=True)

    def ensure_inside(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ServiceError("E_SERVICE_INTERNAL", "storage target is outside the data root", 500) from exc
        return resolved

    def uploading_dir(self, scan_id: str, service_job_id: str) -> Path:
        return self.ensure_inside(self.incoming / f"{scan_id}.{service_job_id}.uploading")

    def validated_dir(self, scan_id: str) -> Path:
        return self.ensure_inside(self.validated / scan_id)

    def result_dir(self, scan_id: str) -> Path:
        return self.ensure_inside(self.results / scan_id)

    def failed_dir(self, scan_id: str, service_job_id: str) -> Path:
        return self.ensure_inside(self.failed / f"{scan_id}.{service_job_id}")

    @staticmethod
    def write_stream(source: BinaryIO, target: Path, *, maximum: int, exact: int | None = None,
                     block_size: int = 64 * 1024,
                     identity: dict[str, Any] | None = None) -> int:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ServiceError("E_SCAN_CONFLICT", "upload target already exists", 409)
        temporary = target.with_name(target.name + ".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        total = 0
        digest = hashlib.sha256() if identity is not None else None
        try:
            fd = os.open(temporary, flags, 0o600)
            with os.fdopen(fd, "wb") as handle:
                while True:
                    block = source.read(block_size)
                    if not block:
                        break
                    total += len(block)
                    if total > maximum:
                        raise ServiceError("E_PART_TOO_LARGE", "multipart part exceeds its byte limit", 413)
                    handle.write(block)
                    if digest is not None:
                        digest.update(block)
                handle.flush()
                os.fsync(handle.fileno())
            if exact is not None and total != exact:
                raise ServiceError("E_IMAGE_SIZE", f"image must contain exactly {exact} bytes", 422)
            os.rename(temporary, target)
            if identity is not None and digest is not None:
                stat = target.stat()
                identity.update({
                    "size": int(stat.st_size),
                    "mtime_ns": int(stat.st_mtime_ns),
                    "sha256": digest.hexdigest(),
                })
            return total
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @staticmethod
    def atomic_json(path: Path, value: dict[str, Any], *, replace: bool = True) -> None:
        payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if not replace and path.exists():
                raise FileExistsError(path)
            os.replace(temporary, path) if replace else os.rename(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def read_json(path: Path) -> dict[str, Any]:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8", errors="strict"), parse_constant=reject_constant)
        if type(value) is not dict:
            raise ValueError("JSON root is not an object")
        assert_finite(value)
        return value

    def commit_validated(self, uploading: Path, scan_id: str) -> Path:
        target = self.validated_dir(scan_id)
        if target.exists() or self.result_dir(scan_id).exists():
            raise ServiceError("E_SCAN_CONFLICT", "scan already exists", 409, scan_id=scan_id)
        os.rename(uploading, target)
        return target

    def fail_upload(self, uploading: Path, scan_id: str, service_job_id: str, error: dict[str, Any]) -> Path:
        target = self.failed_dir(scan_id, service_job_id)
        if uploading.exists() and not target.exists():
            os.rename(uploading, target)
        else:
            target.mkdir(parents=True, exist_ok=True)
        self.atomic_json(target / "service_error.json", error, replace=False)
        return target

    def fail_validated(self, validated: Path, scan_id: str, service_job_id: str, error: dict[str, Any]) -> Path:
        target = self.failed_dir(scan_id, service_job_id)
        if not validated.is_dir() or target.exists():
            raise ServiceError("E_SERVICE_INTERNAL", "cannot quarantine failed validated input", 500, scan_id=scan_id)
        os.rename(validated, target)
        self.atomic_json(target / "service_error.json", error, replace=False)
        return target

    def recover_uploads(self) -> list[Path]:
        moved: list[Path] = []
        for source in self.incoming.glob("*.uploading"):
            base = source.name[:-len(".uploading")]
            target = self.failed / f"{base}.stale_upload"
            suffix = 2
            while target.exists():
                target = self.failed / f"{base}.stale_upload_{suffix:02d}"
                suffix += 1
            os.rename(source, target)
            self.atomic_json(target / "service_error.json", {
                "error_code": "E_PIPELINE_FAILED", "message": "stale upload found after service restart"
            }, replace=False)
            moved.append(target)
        return moved
