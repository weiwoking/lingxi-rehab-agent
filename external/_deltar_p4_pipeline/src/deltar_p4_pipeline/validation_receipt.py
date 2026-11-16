from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .scan_contracts import ScanContractError, load_strict_json, sha256_file
from .scan_validator import ValidatedFrame, ValidatedScan


RECEIPT_FILENAME = ".validation_receipt.json"
RECEIPT_SCHEMA = "deltar_pc_validation_receipt.v1"
VALIDATOR_CONTRACT = "deltar_scan_validator.strict_service.v1"
MAX_RECEIPT_BYTES = 256 * 1024
_SERVICE_OWNED_FILES = {RECEIPT_FILENAME, "service_job.json"}
_RECEIPT_KEYS = {
    "schema_version",
    "scan_id",
    "validated_input_root_identity",
    "input_manifest_identity",
    "scan_manifest_identity",
    "expected_frame_count",
    "files",
    "validator_contract_version",
    "validation_success",
    "generated_at",
    "receipt_sha256",
}


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _root_identity(root: Path) -> dict[str, Any]:
    resolved = root.resolve()
    stat = resolved.stat()
    return {
        "resolved_path": str(resolved),
        "leaf_name": resolved.name,
        "inode": int(stat.st_ino),
    }


def _input_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in _SERVICE_OWNED_FILES:
            continue
        paths.append(path)
    return sorted(paths, key=lambda item: item.relative_to(root).as_posix())


def capture_input_manifest(
    root: Path,
    known_file_identities: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    files: list[dict[str, Any]] = []
    total_bytes = 0
    for path in _input_paths(root):
        stat = path.stat()
        relative = path.relative_to(root).as_posix()
        known = (known_file_identities or {}).get(relative)
        if known is not None and (
            known.get("size") != int(stat.st_size)
            or known.get("mtime_ns") != int(stat.st_mtime_ns)
            or not isinstance(known.get("sha256"), str)
            or len(known["sha256"]) != 64
        ):
            raise ScanContractError(
                "E_VALIDATION_RECEIPT_IDENTITY",
                "uploaded file identity hint changed before authoritative validation",
                relative,
            )
        identity = {
            "relative_path": relative,
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "sha256": known["sha256"] if known is not None else sha256_file(path),
        }
        files.append(identity)
        total_bytes += int(stat.st_size)
    canonical_files = [
        {key: item[key] for key in ("relative_path", "size", "mtime_ns", "sha256")}
        for item in files
    ]
    return {
        "files": files,
        "identity": {
            "file_count": len(files),
            "total_bytes": total_bytes,
            "sha256": _canonical_digest(canonical_files),
        },
    }


def _receipt_digest(receipt: dict[str, Any]) -> str:
    return _canonical_digest({key: value for key, value in receipt.items() if key != "receipt_sha256"})


def build_validation_receipt(
    root: Path,
    *,
    scan_id: str,
    expected_frame_count: int,
    authoritative_manifest: dict[str, Any],
) -> dict[str, Any]:
    root = root.resolve()
    current_manifest = capture_input_manifest(root)
    if current_manifest != authoritative_manifest:
        raise ScanContractError(
            "E_VALIDATION_RECEIPT_IDENTITY",
            "validated input changed between authoritative validation and receipt publication",
            "input_manifest_identity",
        )
    scan_identity = next(
        (item for item in current_manifest["files"] if item["relative_path"] == "scan.json"),
        None,
    )
    if scan_identity is None:
        raise ScanContractError(
            "E_VALIDATION_RECEIPT_IDENTITY", "scan.json is absent from the validated manifest", "scan.json"
        )
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA,
        "scan_id": scan_id,
        "validated_input_root_identity": _root_identity(root),
        "input_manifest_identity": current_manifest["identity"],
        "scan_manifest_identity": dict(scan_identity),
        "expected_frame_count": int(expected_frame_count),
        "files": current_manifest["files"],
        "validator_contract_version": VALIDATOR_CONTRACT,
        "validation_success": True,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }
    receipt["receipt_sha256"] = _receipt_digest(receipt)
    return receipt


def write_validation_receipt(path: Path, receipt: dict[str, Any]) -> None:
    payload = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_RECEIPT_BYTES:
        raise ScanContractError(
            "E_VALIDATION_RECEIPT_SCHEMA", "validation receipt exceeds its internal size limit", RECEIPT_FILENAME
        )
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def create_and_write_validation_receipt(
    root: Path,
    *,
    scan_id: str,
    expected_frame_count: int,
    authoritative_manifest: dict[str, Any],
) -> dict[str, Any]:
    receipt = build_validation_receipt(
        root,
        scan_id=scan_id,
        expected_frame_count=expected_frame_count,
        authoritative_manifest=authoritative_manifest,
    )
    write_validation_receipt(root / RECEIPT_FILENAME, receipt)
    return receipt


def verify_validation_receipt(
    root: Path,
    *,
    expected_scan_id: str | None = None,
    expected_frame_count: int | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    path = root / RECEIPT_FILENAME
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ScanContractError(
            "E_VALIDATION_RECEIPT_MISSING", "authoritative validation receipt is missing", RECEIPT_FILENAME
        ) from exc
    if not raw or len(raw) > MAX_RECEIPT_BYTES:
        raise ScanContractError(
            "E_VALIDATION_RECEIPT_SCHEMA", "validation receipt size is invalid", RECEIPT_FILENAME
        )
    try:
        receipt = json.loads(
            raw.decode("utf-8", errors="strict"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ScanContractError(
            "E_VALIDATION_RECEIPT_SCHEMA", "validation receipt is not strict UTF-8 JSON", RECEIPT_FILENAME
        ) from exc
    if type(receipt) is not dict or set(receipt) != _RECEIPT_KEYS:
        raise ScanContractError(
            "E_VALIDATION_RECEIPT_SCHEMA", "validation receipt fields are not exact", RECEIPT_FILENAME
        )
    if (
        receipt.get("schema_version") != RECEIPT_SCHEMA
        or receipt.get("validator_contract_version") != VALIDATOR_CONTRACT
        or receipt.get("validation_success") is not True
    ):
        raise ScanContractError(
            "E_VALIDATION_RECEIPT_SCHEMA", "validation receipt contract is unsupported", RECEIPT_FILENAME
        )
    digest = receipt.get("receipt_sha256")
    if not isinstance(digest, str) or digest != _receipt_digest(receipt):
        raise ScanContractError(
            "E_VALIDATION_RECEIPT_DIGEST", "validation receipt digest does not match", "receipt_sha256"
        )
    scan_id = receipt.get("scan_id")
    if not isinstance(scan_id, str) or scan_id != root.name or (
        expected_scan_id is not None and scan_id != expected_scan_id
    ):
        raise ScanContractError(
            "E_VALIDATION_RECEIPT_IDENTITY", "validation receipt scan_id does not match", "scan_id"
        )
    frame_count = receipt.get("expected_frame_count")
    if type(frame_count) is not int or frame_count < 1 or frame_count > 64 or (
        expected_frame_count is not None and frame_count != expected_frame_count
    ):
        raise ScanContractError(
            "E_VALIDATION_RECEIPT_IDENTITY", "validation receipt frame count does not match", "expected_frame_count"
        )
    if receipt.get("validated_input_root_identity") != _root_identity(root):
        raise ScanContractError(
            "E_VALIDATION_RECEIPT_IDENTITY", "validated input root identity changed", "validated_input_root_identity"
        )
    manifest = capture_input_manifest(root)
    if receipt.get("files") != manifest["files"] or receipt.get("input_manifest_identity") != manifest["identity"]:
        raise ScanContractError(
            "E_VALIDATION_RECEIPT_IDENTITY", "validated input file identity changed", "input_manifest_identity"
        )
    scan_identity = next(
        (item for item in manifest["files"] if item["relative_path"] == "scan.json"),
        None,
    )
    if receipt.get("scan_manifest_identity") != scan_identity:
        raise ScanContractError(
            "E_VALIDATION_RECEIPT_IDENTITY", "scan manifest identity changed", "scan_manifest_identity"
        )
    expected_paths = {"scan.json"}
    for index in range(frame_count):
        frame_id = f"frame_{index:06d}"
        expected_paths.update(
            {
                f"frames/{frame_id}/frame.json",
                f"frames/{frame_id}/image.rgb565",
                f"frames/{frame_id}/tof.json",
            }
        )
    actual_paths = {item["relative_path"] for item in manifest["files"]}
    if actual_paths != expected_paths:
        raise ScanContractError(
            "E_VALIDATION_RECEIPT_IDENTITY", "validated input manifest path set changed", "files"
        )
    return receipt


def load_receipted_scan(root: Path, *, expected_scan_id: str | None = None) -> ValidatedScan:
    root = root.resolve()
    receipt = verify_validation_receipt(root, expected_scan_id=expected_scan_id)
    scan = load_strict_json(root / "scan.json", code="E_VALIDATION_RECEIPT_IDENTITY")
    if scan.get("scan_id") != receipt["scan_id"] or scan.get("frame_count") != receipt["expected_frame_count"]:
        raise ScanContractError(
            "E_VALIDATION_RECEIPT_IDENTITY", "receipted scan manifest fields changed", "scan.json"
        )
    frames: list[ValidatedFrame] = []
    for index in range(receipt["expected_frame_count"]):
        frame_id = f"frame_{index:06d}"
        directory = root / "frames" / frame_id
        frame = load_strict_json(directory / "frame.json", code="E_VALIDATION_RECEIPT_IDENTITY")
        tof = load_strict_json(directory / "tof.json", code="E_VALIDATION_RECEIPT_IDENTITY")
        frames.append(ValidatedFrame(directory, frame, tof, directory / "image.rgb565"))
    return ValidatedScan(
        root,
        scan,
        tuple(frames),
        (),
        int(receipt["input_manifest_identity"]["total_bytes"]),
    )
