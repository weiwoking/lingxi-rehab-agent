from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

SERVICE_SCHEMA = "deltar_scan_service.v1"
HEALTH_SCHEMA = "deltar_scan_service_health.v1"
JOB_SCHEMA = "deltar_scan_service_job.v1"
ERROR_SCHEMA = "deltar_scan_service_error.v1"

MAX_UPLOAD_BYTES = 6 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
IMAGE_BYTES = 640 * 360 * 2
MAX_RESULT_BYTES = 256 * 1024
MAX_OBJECTS = 100
MAX_FRAMES = 11

SCAN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
FIELD_RE = re.compile(r"^frame_([0-9]{6})_(frame_json|tof_json|image_rgb565)$")
SERVICE_JOB_ID_RE = re.compile(r"^svc_[0-9a-f]{32}$")


@dataclass(frozen=True)
class ServiceError(Exception):
    code: str
    message: str
    status_code: int
    field: str | None = None
    service_job_id: str | None = None
    scan_id: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": ERROR_SCHEMA,
            "ok": False,
            "error_code": self.code,
            "message": self.message,
            "field": self.field,
            "service_job_id": self.service_job_id,
            "scan_id": self.scan_id,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def reject_constant(_: str) -> None:
    raise ValueError("non-finite JSON constant")


def assert_finite(value: Any) -> None:
    if type(value) is float and not math.isfinite(value):
        raise ValueError("non-finite number")
    if isinstance(value, dict):
        for item in value.values():
            assert_finite(item)
    elif isinstance(value, list):
        for item in value:
            assert_finite(item)


def load_json_bytes(raw: bytes, *, code: str = "E_SCAN_JSON", field: str = "scan_json") -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, parse_constant=reject_constant)
        assert_finite(value)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ServiceError(code, "part must contain strict finite UTF-8 JSON", 422, field) from exc
    if type(value) is not dict:
        raise ServiceError(code, "JSON root must be an object", 422, field)
    return value


def scan_identity(scan: dict[str, Any], *, max_frames: int = MAX_FRAMES) -> tuple[str, int]:
    scan_id = scan.get("scan_id")
    if type(scan_id) is not str or SCAN_ID_RE.fullmatch(scan_id) is None:
        raise ServiceError("E_SCAN_JSON", "scan_id must be a safe identifier", 422, "scan_json.scan_id")
    frame_count = scan.get("frame_count")
    if type(frame_count) is not int or not 1 <= frame_count <= max_frames:
        raise ServiceError("E_SCAN_JSON", f"frame_count must be in 1..{max_frames}", 422, "scan_json.frame_count", scan_id=scan_id)
    capture_mode = scan.get("capture_mode")
    expected_mode = "single_frame" if frame_count == 1 else "multi_frame"
    if capture_mode != expected_mode:
        raise ServiceError("E_SCAN_JSON", f"capture_mode must be {expected_mode}", 422, "scan_json.capture_mode", scan_id=scan_id)
    return scan_id, frame_count


def expected_fields(frame_count: int) -> set[str]:
    fields = {"scan_json"}
    for index in range(frame_count):
        prefix = f"frame_{index:06d}_"
        fields.update({prefix + "frame_json", prefix + "tof_json", prefix + "image_rgb565"})
    return fields


def field_spec(field: str) -> tuple[str, str, int, str]:
    if field == "scan_json":
        return "scan.json", "scan.json", MAX_JSON_BYTES, "json"
    match = FIELD_RE.fullmatch(field)
    if match is None:
        raise ServiceError("E_MULTIPART_FIELD", "unknown multipart field", 422, field)
    index, kind = match.groups()
    mapping = {
        "frame_json": ("frame.json", MAX_JSON_BYTES, "json"),
        "tof_json": ("tof.json", MAX_JSON_BYTES, "json"),
        "image_rgb565": ("image.rgb565", IMAGE_BYTES, "image"),
    }
    leaf, limit, category = mapping[kind]
    return f"frames/frame_{index}/{leaf}", leaf, limit, category


def validate_filename(field: str, filename: str | None) -> None:
    _, expected, _, _ = field_spec(field)
    if filename != expected or "/" in filename or "\\" in filename or ".." in filename:
        raise ServiceError("E_PART_FILENAME", "filename must be the frozen leaf name", 422, field)


def validate_content_type(field: str, content_type: str | None) -> None:
    _, _, _, category = field_spec(field)
    allowed = {None, "", "application/json"} if category == "json" else {"application/octet-stream"}
    if content_type not in allowed:
        raise ServiceError("E_PART_CONTENT_TYPE", "part Content-Type does not match its field", 422, field)


def health_payload(*, busy: bool, active_service_job_id: str | None, pipeline_version: str,
                   max_upload_bytes: int, max_frames: int, max_result_bytes: int,
                   max_objects: int = MAX_OBJECTS) -> dict[str, Any]:
    return {
        "schema_version": HEALTH_SCHEMA,
        "status": "ok",
        "busy": busy,
        "active_service_job_id": active_service_job_id,
        "pipeline_version": pipeline_version,
        "limits": {
            "max_upload_bytes": max_upload_bytes,
            "max_frames": max_frames,
            "max_result_bytes": max_result_bytes,
            "max_objects": max_objects,
        },
    }


def public_job(record: dict[str, Any], *, idempotent: bool | None = None) -> dict[str, Any]:
    payload = {
        "schema_version": JOB_SCHEMA,
        "service_job_id": record["service_job_id"],
        "pipeline_job_id": record.get("pipeline_job_id"),
        "scan_id": record["scan_id"],
        "mode": record.get("mode", "full_pipeline"),
        "status": record["status"],
        "pipeline_result_status": record.get("pipeline_result_status"),
        "created_at": record["created_at"],
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "error": record.get("error"),
        "package_ready": bool(record.get("package_ready", False)),
        "package_length": record.get("package_length"),
        "package_sha256": record.get("package_sha256"),
        "full_scene_status": record.get("full_scene_status"),
        "full_scene_ready": bool(record.get("full_scene_ready", False)),
        "full_scene_error_code": record.get("full_scene_error_code"),
        "full_scene_error_message": record.get("full_scene_error_message"),
        "visualization_html_path": record.get("visualization_html_path"),
        "visualization_ready_at": record.get("visualization_ready_at"),
        "visualization_opened_at": record.get("visualization_opened_at"),
        "visualization_open_error": record.get("visualization_open_error"),
        "visualization_open_attempts": int(record.get("visualization_open_attempts", 0)),
        "status_url": f"/api/v1/scan/jobs/{record['service_job_id']}",
        "result_url": f"/api/v1/scan/jobs/{record['service_job_id']}/result",
    }
    if idempotent is not None:
        payload["idempotent"] = idempotent
    return payload


def upload_only_receipt(
    record: dict[str, Any], *, idempotent: bool
) -> dict[str, Any]:
    payload = public_job(record, idempotent=idempotent)
    payload.update({
        "ok": True,
        "mode": "upload_only",
        "frame_count": record["frame_count"],
        "saved_path": f"validated/{record['scan_id']}",
    })
    return payload
