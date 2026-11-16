from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SCAN_SCHEMA = "deltar_scan_input.v1"
FRAME_SCHEMA = "deltar_scan_frame.v1"
TOF_SCHEMA = "rehab_tof_raw.v1"
RESULT_SCHEMA = "deltar_scan_result.v1"
JOB_SCHEMA = "deltar_pc_job_report.v1"

PROFILE_IDS = {
    "camera_profile_id": "camera_sc2336_640x360_rgb565_v1",
    "tof_profile_id": "tof_vl53l5cx_orientation_identity_v1",
    "calibration_profile_id": "calibration_unverified_stage6_v1",
    "algorithm_profile_id": "algorithm_stage6_official_v1",
    "scan_strategy_profile_id": "scan_strategy_stage6_guidance_v1",
}
SCAN_KEYS = frozenset({"schema_version", "scan_id", "created_at", "frame_count", "capture_mode", "device", "profiles", "notes"})
DEVICE_KEYS = frozenset({"board", "camera", "tof"})
FRAME_KEYS = frozenset({"schema_version", "frame_id", "sequence", "camera_timestamp_us", "tof_timestamp_us", "pair_delta_us", "image", "tof"})
IMAGE_KEYS = frozenset({"filename", "width", "height", "pixel_format", "stride_bytes", "payload_bytes"})
TOF_REF_KEYS = frozenset({"filename", "resolution"})
TOF_KEYS = frozenset({"schema_version", "sequence", "timestamp_us", "distance_mm", "range_sigma_mm", "target_status", "nb_target_detected"})
SCAN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
FRAME_ID_RE = re.compile(r"^frame_[0-9]{6}$")
RFC3339_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$")


@dataclass(frozen=True)
class ScanContractError(Exception):
    code: str
    message: str
    field: str | None = None
    details: Any = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" [{self.field}]" if self.field else "")

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field is not None:
            result["field"] = self.field
        if self.details is not None:
            result["details"] = self.details
        return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def load_strict_json(path: Path, *, code: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ScanContractError(code, f"cannot read UTF-8 JSON: {exc}", str(path.name)) from exc
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ScanContractError(code, f"invalid strict JSON: {exc}", str(path.name)) from exc
    if type(value) is not dict:
        raise ScanContractError(code, "JSON root must be an object", str(path.name))
    _assert_finite(value, code=code)
    return value


def _assert_finite(value: Any, *, code: str, field: str = "$") -> None:
    if type(value) is float and not math.isfinite(value):
        raise ScanContractError(code, "number must be finite", field)
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_finite(item, code=code, field=f"{field}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_finite(item, code=code, field=f"{field}[{index}]")


def require(obj: dict[str, Any], key: str, *, code: str, prefix: str) -> Any:
    if key not in obj:
        raise ScanContractError(code, "required field is missing", f"{prefix}.{key}")
    return obj[key]


def require_exact_keys(obj: dict[str, Any], expected: frozenset[str] | set[str], *, code: str, prefix: str) -> None:
    actual = set(obj)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise ScanContractError(code, "required fields are missing", prefix, missing)
    if unknown:
        raise ScanContractError(code, "unknown fields are not allowed", prefix, unknown)


def require_rfc3339_datetime(value: Any, field: str, *, code: str) -> str:
    checked = require_string(value, field, code=code, max_length=64)
    if RFC3339_RE.fullmatch(checked) is None:
        raise ScanContractError(code, "expected RFC3339 date-time with Z or an explicit offset", field)
    try:
        parsed = datetime.fromisoformat(checked[:-1] + "+00:00" if checked.endswith("Z") else checked)
    except ValueError as exc:
        raise ScanContractError(code, "invalid RFC3339 date-time", field) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ScanContractError(code, "date-time must include a timezone", field)
    return checked


def require_string(value: Any, field: str, *, code: str, max_length: int = 256) -> str:
    if type(value) is not str or not value or len(value) > max_length:
        raise ScanContractError(code, f"expected non-empty string up to {max_length} characters", field)
    return value


def require_int(value: Any, field: str, *, code: str, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ScanContractError(code, f"expected integer in {minimum}..{maximum}", field)
    return value


def safe_leaf_filename(value: Any, field: str) -> str:
    name = require_string(value, field, code="E_SCAN_PATH")
    candidate = Path(name)
    if candidate.is_absolute() or name in {".", ".."} or len(candidate.parts) != 1 or "/" in name or "\\" in name:
        raise ScanContractError("E_SCAN_PATH", "filename must be one relative leaf without traversal", field)
    return name


def resolve_inside(root: Path, relative: Path, field: str) -> Path:
    root = root.resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ScanContractError("E_SCAN_PATH", "path escapes scan directory", field) from exc
    return target


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ScanContractError("E_PROFILE_HASH", f"cannot hash file: {exc}", path.name) from exc
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    temp_path: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        temp_path = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except (OSError, ValueError) as exc:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise ScanContractError("E_SCAN_OUTPUT_WRITE", f"atomic JSON write failed: {exc}", path.name) from exc


def validate_result_shape(result: dict[str, Any]) -> None:
    if result.get("schema_version") != RESULT_SCHEMA:
        raise ScanContractError("E_SCAN_RESULT_SCHEMA", f"expected {RESULT_SCHEMA}", "schema_version")
    if result.get("status") not in {"success", "partial_success", "rejected", "failed"}:
        raise ScanContractError("E_SCAN_RESULT_SCHEMA", "invalid result status", "status")
    if type(result.get("objects")) is not list or type(result.get("warnings")) is not list or type(result.get("errors")) is not list:
        raise ScanContractError("E_SCAN_RESULT_SCHEMA", "objects/warnings/errors must be arrays", "result")
    _assert_finite(result, code="E_SCAN_RESULT_SCHEMA")
