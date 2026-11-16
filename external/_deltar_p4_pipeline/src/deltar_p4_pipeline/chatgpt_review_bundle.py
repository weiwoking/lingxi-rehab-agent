from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


SCHEMA_VERSION = "deltar_chatgpt_review_bundle.v1"
REVIEW_ZIP_SUFFIX = "_chatgpt_review.zip"
MANIFEST_ARCHIVE_PATH = "review_manifest.json"

CORE_ARTIFACTS = (
    "job_report.json",
    "stage8b_all_object_geometry.v1.json",
    "stage8b4a_p4_decision_package.v1.json",
    "result.json",
    "logs/pipeline_progress.jsonl",
)
OPTIONAL_ARTIFACTS = (
    "scan.json",
    "service_job.json",
    "validation_receipt.json",
    "validation_receipt.v1.json",
    "stage8b4a_p4_decision_package.v1.json.sha256",
    "stage8b4a_selected_objects.v2.json",
    "stage8b_dual_gate_selection.v1.json",
    "stage8b_dual_gate_diagnostics.v1.json",
    "stage8b1_yolo_batch_manifest.json",
    "stage8b1_yolo_batch_timing.json",
    "stage7d/stage7d_summary.v1.json",
    "visualization/visualization_manifest.v1.json",
    "logs/pipeline_phases.jsonl",
    "logs/service_phases.jsonl",
    "frame_processing_status.json",
    "tof_frame_diagnostics.json",
    "support_authority_summary.json",
)
OPTIONAL_ROOT_NAME_RE = re.compile(
    r"^(?:[A-Za-z0-9_.-]*(?:manifest|receipt|status|decision_ready)[A-Za-z0-9_.-]*\.json|"
    r"[A-Za-z0-9_.-]+\.sha256)$",
    re.IGNORECASE,
)
SECRET_NAME_RE = re.compile(
    r"(?:credential|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|password|\.env)",
    re.IGNORECASE,
)
FORBIDDEN_SUFFIXES = frozenset(
    {
        ".rgb565",
        ".bin",
        ".raw",
        ".npy",
        ".npz",
        ".pt",
        ".pth",
        ".ckpt",
        ".onnx",
        ".safetensors",
        ".zip",
    }
)
FORBIDDEN_PATH_TOKENS = frozenset(
    {
        "raw_tof",
        "tof_raw",
        "dense_depth",
        "range_array",
        "masks",
        "checkpoint",
        "checkpoints",
        "models",
        "training_data",
        "credentials",
        "secrets",
    }
)
MAX_CORE_FILE_BYTES = 4 * 1024 * 1024
MAX_OPTIONAL_FILE_BYTES = 512 * 1024
MAX_PREVIEW_FILE_BYTES = 4 * 1024 * 1024
MAX_ALL_PREVIEWS_BYTES = 8 * 1024 * 1024


class ReviewBundleError(RuntimeError):
    pass


@dataclass(frozen=True)
class BundleSource:
    source_path: Path
    source_relative_path: str
    archive_path: str
    maximum_bytes: int


@dataclass(frozen=True)
class ReviewBundleResult:
    zip_path: Path
    manifest: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _posix_relative(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ReviewBundleError("review source escaped the final result directory") from exc
    value = relative.as_posix()
    _validate_archive_path(value)
    return value


def _validate_archive_path(value: str) -> None:
    if not value or "\\" in value or "\x00" in value:
        raise ReviewBundleError(f"unsafe archive path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReviewBundleError(f"unsafe archive path: {value!r}")
    if re.match(r"^[A-Za-z]:", value):
        raise ReviewBundleError(f"absolute archive path: {value!r}")


def _is_forbidden_archive_path(value: str) -> bool:
    lowered = value.casefold()
    path = PurePosixPath(lowered)
    if path.suffix in FORBIDDEN_SUFFIXES:
        return True
    if SECRET_NAME_RE.search(lowered):
        return True
    tokens = set(part for part in path.parts)
    return bool(tokens & FORBIDDEN_PATH_TOKENS)


def _safe_source(
    root: Path,
    relative: str,
    *,
    archive_path: str | None = None,
    maximum_bytes: int,
) -> BundleSource | None:
    normalized = PurePosixPath(relative)
    _validate_archive_path(normalized.as_posix())
    source = root.joinpath(*normalized.parts)
    if not source.is_file() or source.is_symlink():
        return None
    source_relative = _posix_relative(source, root)
    target = archive_path or source_relative
    _validate_archive_path(target)
    if _is_forbidden_archive_path(source_relative) or _is_forbidden_archive_path(target):
        return None
    if source.stat().st_size > maximum_bytes:
        return None
    return BundleSource(source, source_relative, target, maximum_bytes)


def _preview_sources(root: Path) -> tuple[list[BundleSource], dict[str, Any]]:
    candidates: list[BundleSource] = []
    logs = root / "logs"
    if logs.is_dir():
        for path in sorted(logs.glob("frame_*/source_rgb.png")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = _posix_relative(path, root)
            frame_name = path.parent.name
            source = _safe_source(
                root,
                relative,
                archive_path=f"previews/{frame_name}_source_rgb.png",
                maximum_bytes=MAX_PREVIEW_FILE_BYTES,
            )
            if source is not None:
                candidates.append(source)

    total_bytes = sum(item.source_path.stat().st_size for item in candidates)
    if not candidates:
        return [], {
            "mode": "none",
            "available_count": 0,
            "included_count": 0,
            "reason": "no published source_rgb previews were available",
        }
    if total_bytes <= MAX_ALL_PREVIEWS_BYTES:
        return candidates, {
            "mode": "all",
            "available_count": len(candidates),
            "included_count": len(candidates),
            "total_source_bytes": total_bytes,
            "reason": None,
        }

    indexes = sorted({0, len(candidates) // 2, len(candidates) - 1})
    selected = [candidates[index] for index in indexes]
    return selected, {
        "mode": "representative_first_middle_last",
        "available_count": len(candidates),
        "included_count": len(selected),
        "total_source_bytes": total_bytes,
        "omitted_count": len(candidates) - len(selected),
        "reason": f"all previews exceeded {MAX_ALL_PREVIEWS_BYTES} bytes",
    }


def discover_bundle_sources(
    result_dir: Path,
) -> tuple[list[BundleSource], list[str], dict[str, Any]]:
    root = result_dir.resolve()
    if not root.is_dir():
        raise ReviewBundleError("final result directory does not exist")

    sources: list[BundleSource] = []
    missing: list[str] = []
    for relative in CORE_ARTIFACTS:
        source = _safe_source(root, relative, maximum_bytes=MAX_CORE_FILE_BYTES)
        if source is None:
            missing.append(relative)
        else:
            sources.append(source)
    for relative in OPTIONAL_ARTIFACTS:
        source = _safe_source(root, relative, maximum_bytes=MAX_OPTIONAL_FILE_BYTES)
        if source is None:
            missing.append(relative)
        else:
            sources.append(source)

    known = {item.source_relative_path.casefold() for item in sources}
    for path in sorted(root.iterdir()):
        if (
            not path.is_file()
            or path.is_symlink()
            or path.name.casefold() in known
            or not OPTIONAL_ROOT_NAME_RE.fullmatch(path.name)
            or SECRET_NAME_RE.search(path.name)
        ):
            continue
        source = _safe_source(
            root, path.name, maximum_bytes=MAX_OPTIONAL_FILE_BYTES
        )
        if source is not None:
            sources.append(source)

    previews, preview_policy = _preview_sources(root)
    sources.extend(previews)

    archive_paths: set[str] = set()
    unique_sources: list[BundleSource] = []
    for source in sources:
        key = source.archive_path.casefold()
        if key in archive_paths:
            raise ReviewBundleError(f"duplicate archive path: {source.archive_path}")
        archive_paths.add(key)
        unique_sources.append(source)
    return unique_sources, sorted(set(missing)), preview_policy


def _read_bounded(source: BundleSource) -> bytes:
    with source.source_path.open("rb") as handle:
        payload = handle.read(source.maximum_bytes + 1)
    if len(payload) > source.maximum_bytes:
        raise ReviewBundleError(
            f"review source exceeded its size policy: {source.source_relative_path}"
        )
    return payload


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _metadata_from_artifacts(result_dir: Path) -> dict[str, Any]:
    report = _load_json_object(result_dir / "job_report.json")
    geometry = _load_json_object(
        result_dir / "stage8b_all_object_geometry.v1.json"
    )
    provenance = report.get("provenance")
    profile = geometry.get("profile")
    return {
        "pipeline_version": (
            provenance.get("pipeline_version")
            if isinstance(provenance, dict)
            else None
        ),
        "profile_authority_identifiers": (
            {
                key: profile[key]
                for key in (
                    "profile_id",
                    "camera_authority",
                    "world_z_authority",
                    "geometry_authority",
                    "promoted_payload_set_sha256",
                )
                if key in profile
            }
            if isinstance(profile, dict)
            else {}
        ),
    }


def _infer_terminal_metadata(result_dir: Path) -> tuple[str, str | None]:
    service_job = _load_json_object(result_dir / "service_job.json")
    status = service_job.get("status")
    terminal_status = str(status) if isinstance(status, str) else "unknown"
    error = service_job.get("error")
    if isinstance(error, dict):
        reason = error.get("message") or error.get("error_code")
        return terminal_status, str(reason)[:500] if reason else None
    return terminal_status, None


def _success_terminal(status: str) -> bool:
    return status in {"success", "completed", "decision_ready", "partial_success"}


def _manifest(
    *,
    result_dir: Path,
    scan_id: str,
    scan_terminal_status: str,
    terminal_failure_reason: str | None,
    pipeline_version: str | None,
    included_files: list[dict[str, Any]],
    missing_optional_files: list[str],
    preview_policy: dict[str, Any],
) -> dict[str, Any]:
    metadata = _metadata_from_artifacts(result_dir)
    actual_pipeline_version = pipeline_version or metadata["pipeline_version"]
    missing_core = sorted(set(CORE_ARTIFACTS) & set(missing_optional_files))
    bundle_status = (
        "full_success"
        if _success_terminal(scan_terminal_status) and not missing_core
        else "partial_failure"
    )
    value: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "scan_id": scan_id,
        "generated_at": _utc_now(),
        "scan_terminal_status": scan_terminal_status,
        "bundle_status": bundle_status,
        "source_result_dir": str(result_dir.resolve()),
        "pipeline_version": actual_pipeline_version,
        "profile_authority_identifiers": metadata[
            "profile_authority_identifiers"
        ],
        "included_files": included_files,
        "missing_optional_files": missing_optional_files,
        "excluded_categories": [
            "raw RGB565 and binary camera captures",
            "raw ToF, bins, range arrays, and dense depth arrays",
            "NumPy arrays, masks, and large derived images",
            "models, checkpoints, training data, and build/runtime trees",
            "credentials, secrets, tokens, and environment dumps",
            "other scans and existing ZIP archives",
        ],
        "preview_policy": preview_policy,
        "review_zip_filename": f"{scan_id}{REVIEW_ZIP_SUFFIX}",
    }
    if terminal_failure_reason:
        value["terminal_failure_reason"] = terminal_failure_reason[:500]
    return value


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def validate_review_zip(
    zip_path: Path,
    *,
    required_present: Iterable[str] = (),
) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            folded = [name.casefold() for name in names]
            if len(folded) != len(set(folded)):
                raise ReviewBundleError("review ZIP contains duplicate archive paths")
            for info in infos:
                _validate_archive_path(info.filename)
                if info.filename != MANIFEST_ARCHIVE_PATH and _is_forbidden_archive_path(
                    info.filename
                ):
                    raise ReviewBundleError(
                        f"review ZIP contains forbidden entry: {info.filename}"
                    )
                if info.is_dir():
                    raise ReviewBundleError("review ZIP must not contain directory entries")
            if MANIFEST_ARCHIVE_PATH not in names:
                raise ReviewBundleError("review manifest is missing")
            bad = archive.testzip()
            if bad is not None:
                raise ReviewBundleError(f"review ZIP CRC failure: {bad}")
            manifest = json.loads(
                archive.read(MANIFEST_ARCHIVE_PATH).decode("utf-8", errors="strict")
            )
            if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
                raise ReviewBundleError("review manifest schema is invalid")
            entries = manifest.get("included_files")
            if not isinstance(entries, list):
                raise ReviewBundleError("review manifest included_files is invalid")
            described: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ReviewBundleError("review manifest file entry is invalid")
                archive_path = entry.get("archive_path")
                if not isinstance(archive_path, str) or archive_path not in names:
                    raise ReviewBundleError("review manifest references a missing entry")
                payload = archive.read(archive_path)
                if len(payload) != entry.get("size_bytes"):
                    raise ReviewBundleError("review manifest size does not match bytes")
                if hashlib.sha256(payload).hexdigest() != entry.get("sha256"):
                    raise ReviewBundleError("review manifest SHA-256 does not match bytes")
                described.add(archive_path)
            if set(names) != described | {MANIFEST_ARCHIVE_PATH}:
                raise ReviewBundleError("review ZIP has entries absent from its manifest")
            missing_required = sorted(set(required_present) - described)
            if missing_required:
                raise ReviewBundleError(
                    f"required published files were omitted: {missing_required}"
                )
            return manifest
    except (OSError, UnicodeError, ValueError, zipfile.BadZipFile) as exc:
        if isinstance(exc, ReviewBundleError):
            raise
        raise ReviewBundleError(f"review ZIP validation failed: {exc}") from exc


def build_chatgpt_review_bundle(
    result_dir: Path,
    *,
    scan_id: str | None = None,
    scan_terminal_status: str | None = None,
    terminal_failure_reason: str | None = None,
    pipeline_version: str | None = None,
) -> ReviewBundleResult:
    root = result_dir.resolve()
    actual_scan_id = scan_id or root.name
    if actual_scan_id != root.name or not re.fullmatch(
        r"scan_[A-Za-z0-9_-]+", actual_scan_id
    ):
        raise ReviewBundleError("scan_id does not match the final result directory")
    if not root.is_dir():
        raise ReviewBundleError("final result directory does not exist")

    inferred_status, inferred_reason = _infer_terminal_metadata(root)
    terminal_status = scan_terminal_status or inferred_status
    failure_reason = terminal_failure_reason or inferred_reason
    sources, missing, preview_policy = discover_bundle_sources(root)
    required_present = {
        relative
        for relative in CORE_ARTIFACTS
        if (root.joinpath(*PurePosixPath(relative).parts)).is_file()
    }

    payloads: list[tuple[BundleSource, bytes]] = []
    included_files: list[dict[str, Any]] = []
    for source in sources:
        payload = _read_bounded(source)
        payloads.append((source, payload))
        included_files.append(
            {
                "archive_path": source.archive_path,
                "source_relative_path": source.source_relative_path,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    manifest = _manifest(
        result_dir=root,
        scan_id=actual_scan_id,
        scan_terminal_status=terminal_status,
        terminal_failure_reason=failure_reason,
        pipeline_version=pipeline_version,
        included_files=included_files,
        missing_optional_files=missing,
        preview_policy=preview_policy,
    )
    manifest_payload = _json_bytes(manifest)
    final_path = root / f"{actual_scan_id}{REVIEW_ZIP_SUFFIX}"
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{final_path.name}.", suffix=".tmp", dir=str(root)
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            allowZip64=False,
        ) as archive:
            for source, payload in payloads:
                archive.writestr(source.archive_path, payload)
            archive.writestr(MANIFEST_ARCHIVE_PATH, manifest_payload)
        # Windows requires a writable handle for FlushFileBuffers/os.fsync.
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        validated = validate_review_zip(
            temporary, required_present=required_present
        )
        os.replace(temporary, final_path)
        return ReviewBundleResult(final_path, validated)
    finally:
        temporary.unlink(missing_ok=True)
