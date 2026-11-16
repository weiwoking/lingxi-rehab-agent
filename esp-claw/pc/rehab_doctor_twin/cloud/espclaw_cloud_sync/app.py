from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile

SCHEMA_VERSION = "espclaw_cloud_sensor_v1"
DATA_ROOT = Path(os.getenv("ESPCLAW_CLOUD_DATA_ROOT", "/var/lib/espclaw_cloud_sync")).resolve()
API_TOKEN = os.getenv("ESPCLAW_CLOUD_API_TOKEN", "")
MAX_UPLOAD_BYTES = int(os.getenv("ESPCLAW_CLOUD_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
SESSION_ID_RE = re.compile(r"^[0-9]{8}_TASK[0-9]{3}$")
CLOUD_SUBJECT_RE = re.compile(r"^[0-9a-f]{24}$")
TASK_ID_RE = re.compile(r"^[a-z0-9_\\-]{1,64}$")

app = FastAPI(title="ESP-Claw Cloud Sensor Sync", version="1.0.0")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_token(authorization: str | None) -> None:
    if not API_TOKEN:
        raise HTTPException(status_code=503, detail="cloud sync token is not configured")
    expected = f"Bearer {API_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


def db_path() -> Path:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / "sessions").mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / "staging").mkdir(parents=True, exist_ok=True)
    return DATA_ROOT / "cloud_sessions.db"


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(db_path())
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS cloud_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_version TEXT NOT NULL,
            device_id TEXT NOT NULL,
            cloud_subject_id TEXT NOT NULL,
            session_id TEXT NOT NULL UNIQUE,
            task_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            duration_s REAL,
            row_count INTEGER NOT NULL,
            imu_channel_count INTEGER NOT NULL DEFAULT 0,
            emg_channel_count INTEGER NOT NULL DEFAULT 0,
            csv_sha256 TEXT NOT NULL,
            original_size INTEGER NOT NULL,
            compressed_size INTEGER NOT NULL,
            storage_path TEXT NOT NULL,
            uploaded_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(cloud_sessions)").fetchall()
    }
    if "imu_channel_count" not in columns:
        connection.execute("ALTER TABLE cloud_sessions ADD COLUMN imu_channel_count INTEGER NOT NULL DEFAULT 0")
    if "emg_channel_count" not in columns:
        connection.execute("ALTER TABLE cloud_sessions ADD COLUMN emg_channel_count INTEGER NOT NULL DEFAULT 0")
    connection.commit()
    return connection


@app.get("/api/v1/cloud/health")
def health() -> dict[str, str]:
    db()
    return {
        "service": "espclaw_cloud_sync",
        "status": "ready",
        "schema_version": SCHEMA_VERSION,
    }


@app.post("/api/v1/cloud/sessions")
async def upload_session(
    metadata: str = Form(...),
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_token(authorization)
    meta = parse_metadata(metadata)
    compressed_path, compressed_size = await store_upload_temporarily(file)
    try:
        if compressed_size != int(meta["compressed_size"]):
            raise HTTPException(status_code=422, detail="compressed_size mismatch")
        original_sha, original_size = validate_gzip_payload(compressed_path)
        if original_sha != meta["csv_sha256"]:
            raise HTTPException(status_code=422, detail="csv_sha256 mismatch")
        if original_size != int(meta["original_size"]):
            raise HTTPException(status_code=422, detail="original_size mismatch")
        return persist_session(meta, compressed_path, compressed_size)
    finally:
        compressed_path.unlink(missing_ok=True)


@app.get("/api/v1/cloud/sessions/{session_id}")
def get_session(session_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_token(authorization)
    if not SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=422, detail="invalid session_id")
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM cloud_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    return public_row(row)


@app.get("/api/v1/cloud/sessions")
def list_sessions(limit: int = 50, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_token(authorization)
    limit = max(1, min(int(limit), 200))
    with db() as connection:
        rows = connection.execute(
            "SELECT * FROM cloud_sessions ORDER BY uploaded_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"sessions": [public_row(row) for row in rows]}


def parse_metadata(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="metadata is not valid JSON") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="metadata must be an object")
    required = {
        "schema_version",
        "device_id",
        "cloud_subject_id",
        "session_id",
        "task_id",
        "started_at",
        "ended_at",
        "duration_s",
        "csv_file",
        "original_size",
        "compressed_size",
        "row_count",
        "csv_sha256",
        "imu_channel_count",
        "emg_channel_count",
        "uploaded_at",
    }
    if set(value) != required:
        raise HTTPException(status_code=422, detail="metadata fields do not match contract")
    if value["schema_version"] != SCHEMA_VERSION:
        raise HTTPException(status_code=422, detail="invalid schema_version")
    if not SESSION_ID_RE.fullmatch(str(value["session_id"])):
        raise HTTPException(status_code=422, detail="invalid session_id")
    if not CLOUD_SUBJECT_RE.fullmatch(str(value["cloud_subject_id"])):
        raise HTTPException(status_code=422, detail="invalid cloud_subject_id")
    if not TASK_ID_RE.fullmatch(str(value["task_id"])):
        raise HTTPException(status_code=422, detail="invalid task_id")
    if Path(str(value["csv_file"])).name != value["csv_file"] or not str(value["csv_file"]).endswith(".csv"):
        raise HTTPException(status_code=422, detail="invalid csv_file")
    if not re.fullmatch(r"^[0-9a-f]{64}$", str(value["csv_sha256"])):
        raise HTTPException(status_code=422, detail="invalid csv_sha256")
    for key in ("original_size", "compressed_size", "row_count", "imu_channel_count", "emg_channel_count"):
        if not isinstance(value[key], int) or value[key] < 0:
            raise HTTPException(status_code=422, detail=f"invalid {key}")
    if value["original_size"] <= 0 or value["compressed_size"] <= 0 or value["row_count"] <= 0:
        raise HTTPException(status_code=422, detail="empty sensor payload is not allowed")
    return value


async def store_upload_temporarily(file: UploadFile) -> tuple[Path, int]:
    if not file.filename or not file.filename.endswith(".csv.gz"):
        raise HTTPException(status_code=422, detail="only .csv.gz upload is allowed")
    staging = DATA_ROOT / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="upload_", suffix=".csv.gz", dir=staging)
    os.close(fd)
    path = Path(temp_name)
    size = 0
    try:
        with path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="upload too large")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        return path, size
    except Exception:
        path.unlink(missing_ok=True)
        raise


def validate_gzip_payload(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with gzip.open(path, "rb") as source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise HTTPException(status_code=422, detail="invalid gzip payload") from exc
    if size <= 0:
        raise HTTPException(status_code=422, detail="empty decompressed payload")
    return digest.hexdigest(), size


def persist_session(meta: dict[str, Any], temp_path: Path, compressed_size: int) -> dict[str, Any]:
    session_id = str(meta["session_id"])
    with db() as connection:
        existing = connection.execute(
            "SELECT * FROM cloud_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if existing is not None:
            if existing["csv_sha256"] == meta["csv_sha256"]:
                return public_row(existing)
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "session_content_conflict",
                    "message": "same session_id already exists with different CSV SHA-256",
                },
            )
        final_dir = DATA_ROOT / "sessions" / str(meta["cloud_subject_id"]) / session_id
        final_dir.mkdir(parents=True, exist_ok=True)
        final_path = final_dir / "sensor_data.csv.gz"
        staged_final = final_dir / "sensor_data.csv.gz.tmp"
        shutil.copyfile(temp_path, staged_final)
        os.replace(staged_final, final_path)
        uploaded_at = utc_now()
        connection.execute(
            """
            INSERT INTO cloud_sessions (
                schema_version, device_id, cloud_subject_id, session_id, task_id,
                started_at, ended_at, duration_s, row_count, imu_channel_count,
                emg_channel_count, csv_sha256,
                original_size, compressed_size, storage_path, uploaded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                SCHEMA_VERSION,
                str(meta["device_id"]),
                str(meta["cloud_subject_id"]),
                session_id,
                str(meta["task_id"]),
                str(meta["started_at"]),
                str(meta["ended_at"]),
                meta.get("duration_s"),
                int(meta["row_count"]),
                int(meta["imu_channel_count"]),
                int(meta["emg_channel_count"]),
                str(meta["csv_sha256"]),
                int(meta["original_size"]),
                int(compressed_size),
                str(final_path),
                uploaded_at,
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM cloud_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return public_row(row)


def public_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "session_id": row["session_id"],
        "cloud_subject_id": row["cloud_subject_id"],
        "task_id": row["task_id"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "duration_s": row["duration_s"],
        "row_count": row["row_count"],
        "imu_channel_count": row["imu_channel_count"],
        "emg_channel_count": row["emg_channel_count"],
        "csv_sha256": row["csv_sha256"],
        "original_size": row["original_size"],
        "compressed_size": row["compressed_size"],
        "uploaded_at": row["uploaded_at"],
    }
