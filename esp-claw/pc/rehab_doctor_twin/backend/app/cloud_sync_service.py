from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PATIENT_ID, TRAINING_DATA_RESOLVER, TRAINING_DATA_ROOT
from rehab_training_data.resolver import TrainingDataResolver


SESSION_ID_RE = re.compile(r"^[0-9]{8}_TASK[0-9]{3}$")
CSV_FILE_RE = re.compile(r"^DATA_SEQ[0-9]{10}\.csv$")
SCHEMA_VERSION = "espclaw_cloud_sensor_v1"

STATE_NOT_READY = "not_ready"
STATE_NOT_UPLOADED = "not_uploaded"
STATE_QUEUED = "queued"
STATE_UPLOADING = "uploading"
STATE_SYNCED = "synced"
STATE_FAILED = "failed"
STATE_CONFLICT = "conflict"
UPLOAD_STATES = {
    STATE_NOT_READY,
    STATE_NOT_UPLOADED,
    STATE_QUEUED,
    STATE_UPLOADING,
    STATE_SYNCED,
    STATE_FAILED,
    STATE_CONFLICT,
}

CLOUD_CONFIG_DIR = (
    Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    / "ESP-Claw"
    / "doctor_pc_only"
)
CLOUD_CONFIG_FILE = CLOUD_CONFIG_DIR / "cloud_sync.env"
CLOUD_SYNC_DIR = TRAINING_DATA_RESOLVER.sync_root()
QUEUE_FILE = TRAINING_DATA_RESOLVER.sync_queue_path()
TMP_DIR = TRAINING_DATA_RESOLVER.staging_root() / "cloud"

DEFAULT_BASE_URL = "http://120.26.28.31"
DEFAULT_DEVICE_ID = "ESPCLAW-DEMO-01"
DEFAULT_FILE_STABLE_SECONDS = 10.0
DEFAULT_HEALTH_INTERVAL_SECONDS = 20.0
DEFAULT_TIMEOUT_SECONDS = 20.0
RETRY_DELAYS_SECONDS = (10, 30, 60, 300)
WORKER_INTERVAL_SECONDS = 2.0
MAX_ERROR_LENGTH = 180
TASK_NAME_TO_TASK_ID = {
    "喝水动作模拟": "drink_water_right",
    "饮水动作训练": "drink_water_right",
    "右手拿杯饮水": "drink_water_right",
    "拿书动作训练": "pick_book_right",
}
ANALYSIS_CACHE_FIELDS = {
    "csv_sha256",
    "row_count",
    "imu_channel_count",
    "emg_channel_count",
    "analysis_size",
    "analysis_mtime_ns",
    "csv_analysis_cached_at",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _monotonic() -> float:
    return time.monotonic()


def _safe_error(error: object) -> str:
    text = str(error).replace("\r", " ").replace("\n", " ").strip()
    return text[:MAX_ERROR_LENGTH] or error.__class__.__name__


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _float_config(value: str | None, default: float, minimum: float = 0.0) -> float:
    try:
        parsed = float(str(value or "").strip())
    except ValueError:
        parsed = default
    return max(minimum, parsed)


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    path.write_text(content, encoding="utf-8")


def _ensure_cloud_config() -> dict[str, str]:
    values = _read_env_file(CLOUD_CONFIG_FILE)
    changed = False
    defaults = {
        "ESPCLAW_CLOUD_SYNC_ENABLED": "false",
        "ESPCLAW_CLOUD_BASE_URL": DEFAULT_BASE_URL,
        "ESPCLAW_CLOUD_DEVICE_ID": DEFAULT_DEVICE_ID,
        "ESPCLAW_CLOUD_TIMEOUT_SECONDS": str(int(DEFAULT_TIMEOUT_SECONDS)),
        "ESPCLAW_CLOUD_FILE_STABLE_SECONDS": str(int(DEFAULT_FILE_STABLE_SECONDS)),
        "ESPCLAW_CLOUD_HEALTH_INTERVAL_SECONDS": str(int(DEFAULT_HEALTH_INTERVAL_SECONDS)),
    }
    for key, value in defaults.items():
        if not values.get(key):
            values[key] = value
            changed = True
    if not values.get("ESPCLAW_CLOUD_API_TOKEN"):
        values["ESPCLAW_CLOUD_API_TOKEN"] = secrets.token_urlsafe(64)
        changed = True
    if not values.get("ESPCLAW_CLOUD_SUBJECT_SALT"):
        values["ESPCLAW_CLOUD_SUBJECT_SALT"] = secrets.token_hex(32)
        changed = True
    if changed:
        _write_env_file(CLOUD_CONFIG_FILE, values)
    return values


def _subject_id(patient_id: str, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}:{patient_id}".encode("utf-8")).hexdigest()
    return digest[:24]


class CloudSyncService:
    def __init__(self) -> None:
        # Cloud Sync must remain reachable when a process-level proxy points to
        # a VPN client that is not running. Keep this transport instance-local
        # so other Doctor HTTP clients can continue to honor system proxies.
        self._direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._task: asyncio.Task | None = None
        self._queue: dict[str, dict[str, Any]] = {}
        self._observations: dict[str, dict[str, Any]] = {}
        self._session_records: dict[str, dict[str, Any]] = {}
        self._active_session_ids: set[str] = set()
        self._config: dict[str, str] = {}
        self._cloud_online = False
        self._last_check_at: str | None = None
        self._last_sync_at: str | None = None
        self._last_health_check_monotonic = 0.0
        self._auto_upload_enabled = False
        self._started = False
        self._path_resolver = TrainingDataResolver(training_root=TRAINING_DATA_ROOT)

    async def start(self) -> None:
        if self._started:
            return
        self._config = _ensure_cloud_config()
        CLOUD_SYNC_DIR.mkdir(parents=True, exist_ok=True)
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        self._load_queue()
        self._started = True
        self._task = asyncio.create_task(self._worker(), name="cloud-sync-service")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._auto_upload_enabled = False
        self._started = False

    @property
    def enabled(self) -> bool:
        return _enabled(self._config.get("ESPCLAW_CLOUD_SYNC_ENABLED"))

    @property
    def auto_upload_enabled(self) -> bool:
        return self._auto_upload_enabled if self.enabled else False

    def summary(self) -> dict[str, Any]:
        self._discover_sessions()
        counts = {state: 0 for state in UPLOAD_STATES}
        for item in self._queue.values():
            status = self._normalize_state(item.get("status"))
            counts[status] += 1
        state = (
            "disabled"
            if not self.enabled
            else "online"
            if self._cloud_online
            else "offline"
        )
        return {
            "enabled": self.enabled,
            "cloud_online": self._cloud_online if self.enabled else False,
            "state": state,
            "auto_upload_enabled": self.auto_upload_enabled,
            "not_ready_count": counts[STATE_NOT_READY],
            "not_uploaded_count": counts[STATE_NOT_UPLOADED],
            "queued_count": counts[STATE_QUEUED],
            "uploading_count": counts[STATE_UPLOADING],
            "synced_count": counts[STATE_SYNCED],
            "failed_count": counts[STATE_FAILED],
            "conflict_count": counts[STATE_CONFLICT],
            "last_sync_at": self._last_sync_at,
            "last_check_at": self._last_check_at,
        }

    def session_status(self, session_id: str, refresh: bool = True) -> dict[str, Any]:
        if refresh:
            self._discover_sessions()
        item = self._queue.get(session_id)
        if not item:
            return self._public_status(
                {
                    "session_id": session_id,
                    "status": STATE_NOT_UPLOADED if SESSION_ID_RE.fullmatch(session_id) else STATE_NOT_READY,
                    "attempt_count": 0,
                    "synced_at": None,
                    "last_error": None,
                    "remote_record_id": None,
                    "next_retry_at": None,
                }
            )
        return self._public_status(item)

    def refresh_session_records(self, sessions: list[dict[str, Any]]) -> None:
        records: dict[str, dict[str, Any]] = {}
        active: set[str] = set()
        for session in sessions:
            session_id = str(session.get("session_id") or "")
            if not SESSION_ID_RE.fullmatch(session_id):
                continue
            records[session_id] = deepcopy(session)
            if not self._session_record_finalized(session):
                active.add(session_id)
        self._session_records = records
        self._active_session_ids = active

    def enrich_sessions(self, sessions: list[dict[str, Any]], refresh_discovery: bool = True) -> list[dict[str, Any]]:
        self.refresh_session_records(sessions)
        if refresh_discovery:
            self._discover_sessions()
        enriched = []
        for session in sessions:
            item = deepcopy(session)
            session_id = str(item.get("session_id") or "")
            if SESSION_ID_RE.fullmatch(session_id):
                item["cloud_sync"] = self.session_status(session_id, refresh=False)
            enriched.append(item)
        return enriched

    def check_cloud(self, force: bool = True) -> dict[str, Any]:
        if not self.enabled:
            self._cloud_online = False
            return self.summary()
        self._check_cloud_health(force=force)
        return self.summary()

    def request_upload(self, session_id: str, session_record: dict[str, Any] | None = None) -> dict[str, Any]:
        if session_record is not None:
            self.refresh_session_records([session_record])
        self._discover_sessions()
        item = self._queue.get(session_id)
        if not self.enabled:
            self._auto_upload_enabled = False
            return self._upload_response(False, session_id, item, "feature_disabled")
        if item is None:
            return self._upload_response(False, session_id, None, "session_not_found")
        if item["status"] == STATE_NOT_READY:
            return self._upload_response(False, session_id, item, item.get("last_error") or "not_ready")
        if item["status"] == STATE_SYNCED:
            return self._upload_response(False, session_id, item, "already_synced")
        if item["status"] in {STATE_QUEUED, STATE_UPLOADING}:
            return self._upload_response(True, session_id, item, None)
        if item["status"] == STATE_CONFLICT:
            return self._upload_response(False, session_id, item, "remote_session_sha_conflict")
        self._check_cloud_health(force=True)
        if not self._cloud_online:
            if item["status"] == STATE_FAILED and item.get("last_error") == "cloud_offline":
                item["status"] = STATE_NOT_UPLOADED
                item["last_error"] = None
                item["next_retry_at"] = None
                self._save_queue()
            return self._upload_response(False, session_id, item, "cloud_offline")
        item["status"] = STATE_QUEUED
        item["last_error"] = None
        item["next_retry_at"] = None
        self._save_queue()
        return self._upload_response(True, session_id, item, None)

    def upload_pending(self) -> dict[str, Any]:
        self._discover_sessions()
        eligible = [
            item
            for item in self._queue.values()
            if item.get("status") in {STATE_NOT_UPLOADED, STATE_FAILED}
            and self._retry_due(item, _utc_now())
        ]
        if not self.enabled:
            return {
                "accepted": False,
                "reason": "feature_disabled",
                "eligible_count": len(eligible),
                "queued_count": 0,
                "skipped_count": len(self._queue) - len(eligible),
            }
        self._check_cloud_health(force=True)
        if not self._cloud_online:
            return {
                "accepted": False,
                "reason": "cloud_offline",
                "eligible_count": len(eligible),
                "queued_count": 0,
                "skipped_count": len(self._queue) - len(eligible),
            }
        queued = 0
        for item in eligible:
            item["status"] = STATE_QUEUED
            item["last_error"] = None
            item["next_retry_at"] = None
            queued += 1
        if queued:
            self._save_queue()
        return {
            "accepted": True,
            "reason": None,
            "eligible_count": len(eligible),
            "queued_count": queued,
            "skipped_count": len(self._queue) - queued,
        }

    def set_auto_upload(self, enabled: bool) -> dict[str, Any]:
        if not self.enabled:
            self._auto_upload_enabled = False
            return {"auto_upload_enabled": False, "accepted": False, "reason": "feature_disabled"}
        self._auto_upload_enabled = bool(enabled)
        if not self._auto_upload_enabled:
            changed = False
            for item in self._queue.values():
                if item.get("status") == STATE_QUEUED:
                    item["status"] = STATE_NOT_UPLOADED
                    changed = True
            if changed:
                self._save_queue()
        return {"auto_upload_enabled": self._auto_upload_enabled, "accepted": True, "reason": None}

    async def _worker(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self._discover_sessions)
                if self.enabled:
                    await asyncio.to_thread(self._check_cloud_health, False)
                    await asyncio.to_thread(self._process_uploads)
                else:
                    self._cloud_online = False
                    self._auto_upload_enabled = False
                await asyncio.sleep(WORKER_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(WORKER_INTERVAL_SECONDS)

    def _discover_sessions(self) -> None:
        sessions_root = self._path_resolver.score_sessions_root().resolve()
        fixed_root = self._path_resolver.get_score_root()
        try:
            sessions_root.relative_to(fixed_root)
        except ValueError:
            return
        if not sessions_root.is_dir():
            if self._queue:
                self._queue.clear()
                self._observations.clear()
                self._save_queue()
            return
        changed = False
        present_session_ids: set[str] = set()
        for session_dir in sessions_root.iterdir():
            session_id = session_dir.name
            if (
                not session_dir.is_dir()
                or session_dir.name.startswith(".")
                or session_dir.name.startswith("_")
                or session_dir.name in {".staging", "staging", "tmp", "temp", "replay"}
                or not SESSION_ID_RE.fullmatch(session_id)
            ):
                continue
            present_session_ids.add(session_id)
            status_before = deepcopy(self._queue.get(session_id))
            self._refresh_candidate(session_id, session_dir)
            if self._queue.get(session_id) != status_before:
                changed = True
        for deleted_session_id in set(self._queue) - present_session_ids:
            self._queue.pop(deleted_session_id, None)
            self._observations.pop(deleted_session_id, None)
            changed = True
        if changed:
            self._save_queue()

    def _refresh_candidate(self, session_id: str, session_dir: Path) -> None:
        existing = self._queue.get(session_id)
        if existing and existing.get("status") == STATE_UPLOADING:
            return
        if existing and existing.get("status") in {STATE_SYNCED, STATE_CONFLICT}:
            return
        candidate, reason = self._build_light_candidate(session_id, session_dir)
        if candidate is None:
            self._set_not_ready(session_id, reason)
            return
        old = self._queue.get(session_id)
        status = old.get("status") if old else STATE_NOT_UPLOADED
        if status not in UPLOAD_STATES or status in {STATE_NOT_READY, STATE_QUEUED}:
            status = STATE_NOT_UPLOADED if status != STATE_QUEUED else STATE_QUEUED
        signature_unchanged = bool(
            old
            and old.get("csv_size") == candidate["csv_size"]
            and old.get("csv_mtime_ns") == candidate["csv_mtime_ns"]
        )
        merged = {
            **candidate,
            "status": status,
            "attempt_count": int((old or {}).get("attempt_count") or 0),
            "last_attempt_at": (old or {}).get("last_attempt_at"),
            "synced_at": (old or {}).get("synced_at") if status == STATE_SYNCED else None,
            "last_error": (old or {}).get("last_error") if status == STATE_FAILED else None,
            "remote_record_id": (old or {}).get("remote_record_id"),
            "next_retry_at": (old or {}).get("next_retry_at") if status == STATE_FAILED else None,
        }
        if signature_unchanged:
            for key in ANALYSIS_CACHE_FIELDS:
                if key in old:
                    merged[key] = old[key]
        self._queue[session_id] = merged

    def _build_light_candidate(self, session_id: str, session_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
        if session_id in self._active_session_ids:
            return None, "active_session"
        try:
            resolved_session_dir = session_dir.resolve()
            resolved_session_dir.relative_to(
                self._path_resolver.score_sessions_root().resolve()
            )
        except (OSError, ValueError):
            return None, "invalid_session_path"
        input_dir = resolved_session_dir / "input"
        task_path = input_dir / "task.json"
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None, "task_metadata_missing"
        if not isinstance(task, dict):
            return None, "task_metadata_missing"
        if not self._task_finalized(task):
            return None, "session_not_finalized"
        csv_name = str(task.get("csv_file") or "")
        if not CSV_FILE_RE.fullmatch(csv_name):
            return None, "invalid_csv_name"
        csv_path = (input_dir / csv_name).resolve()
        try:
            csv_path.relative_to(input_dir.resolve())
        except ValueError:
            return None, "invalid_csv_path"
        if csv_path.suffix.lower() != ".csv" or any(part.endswith((".tmp", ".part", ".uploading", ".gz")) for part in csv_path.parts):
            return None, "temporary_csv_file"
        if not csv_path.is_file():
            return None, "csv_missing"
        try:
            stat = csv_path.stat()
        except OSError:
            return None, "csv_unreadable"
        if stat.st_size <= 0:
            return None, "csv_empty"
        if not self._file_stable(session_id, stat.st_size, stat.st_mtime_ns):
            return None, "csv_not_stable"
        task_id = self.resolve_cloud_task_id(resolved_session_dir, self._session_records.get(session_id), task)
        started_at = str(task.get("start_time") or "")
        ended_at = str(task.get("end_time") or "")
        return (
            {
                "session_id": session_id,
                "csv_path": str(csv_path),
                "csv_file": csv_name,
                "csv_size": stat.st_size,
                "csv_mtime_ns": stat.st_mtime_ns,
                "stable_since": self._observations.get(session_id, {}).get("stable_since"),
                "task_id": task_id,
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_s": self._duration_seconds(started_at, ended_at),
                "original_size": stat.st_size,
            },
            None,
        )

    def _file_stable(self, session_id: str, size: int, mtime_ns: int) -> bool:
        now = _monotonic()
        prior = self._observations.get(session_id)
        same_file_signature = bool(
            prior
            and prior.get("size") == size
            and prior.get("mtime_ns") == mtime_ns
        )
        self._observations[session_id] = {
            "size": size,
            "mtime_ns": mtime_ns,
            "first_observed_at": prior.get("first_observed_at") if same_file_signature else now,
            "last_observed_at": now,
            "stable_since": prior.get("stable_since") if same_file_signature else _utc_now(),
        }
        current = self._observations[session_id]
        return (
            same_file_signature
            and now - float(current["first_observed_at"]) >= self.file_stable_seconds
        )

    @property
    def file_stable_seconds(self) -> float:
        return _float_config(
            self._config.get("ESPCLAW_CLOUD_FILE_STABLE_SECONDS"),
            DEFAULT_FILE_STABLE_SECONDS,
            0.0,
        )

    @property
    def health_interval_seconds(self) -> float:
        return _float_config(
            self._config.get("ESPCLAW_CLOUD_HEALTH_INTERVAL_SECONDS"),
            DEFAULT_HEALTH_INTERVAL_SECONDS,
            1.0,
        )

    @staticmethod
    def _task_finalized(task: dict[str, Any]) -> bool:
        return bool(task.get("end_time") and task.get("end_reason"))

    @staticmethod
    def _session_record_finalized(session: dict[str, Any]) -> bool:
        if session.get("task_finalized") is True:
            return True
        if session.get("completed_at") and session.get("end_reason"):
            return True
        if session.get("ended_at_ms") is not None and session.get("status") in {"COMPLETED", "FAILED", "INVALID"}:
            return True
        return False

    @staticmethod
    def resolve_cloud_task_id(
        session_dir: Path,
        session_record: dict[str, Any] | None,
        task: dict[str, Any] | None = None,
    ) -> str:
        for source in (session_record or {}, task or {}):
            for key in ("task_id", "task_type", "task_code", "action"):
                value = source.get(key)
                if isinstance(value, str) and re.fullmatch(r"[a-z0-9_\-]{1,64}", value):
                    return value
        task_path = session_dir / "input" / "task.json"
        if task is None:
            try:
                loaded = json.loads(task_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                loaded = None
            task = loaded if isinstance(loaded, dict) else None
        if isinstance(task, dict):
            value = task.get("task_name")
            if isinstance(value, str) and re.fullmatch(r"[a-z0-9_\-]{1,64}", value):
                return value
            if isinstance(value, str) and value in TASK_NAME_TO_TASK_ID:
                return TASK_NAME_TO_TASK_ID[value]
        return "unknown"

    def _set_not_ready(self, session_id: str, reason: str | None) -> None:
        old = self._queue.get(session_id) or {}
        if old.get("status") in {STATE_SYNCED, STATE_UPLOADING, STATE_CONFLICT}:
            return
        if old.get("status") == STATE_QUEUED:
            return
        self._queue[session_id] = {
            **old,
            "session_id": session_id,
            "status": STATE_NOT_READY,
            "attempt_count": int(old.get("attempt_count") or 0),
            "synced_at": None,
            "last_error": reason,
            "remote_record_id": old.get("remote_record_id"),
            "next_retry_at": None,
        }

    def _process_uploads(self) -> None:
        if not self._cloud_online:
            return
        if self.auto_upload_enabled:
            now_iso = _utc_now()
            auto_candidates = [
                item
                for item in self._queue.values()
                if item.get("status") in {STATE_NOT_UPLOADED, STATE_FAILED}
                and self._retry_due(item, now_iso)
            ][:2]
            for item in auto_candidates:
                item["status"] = STATE_QUEUED
            if auto_candidates:
                self._save_queue()
        queued = [
            item
            for item in self._queue.values()
            if item.get("status") == STATE_QUEUED
        ]
        if queued:
            self._upload(queued[0])

    def _upload(self, item: dict[str, Any]) -> None:
        session_id = item["session_id"]
        prepared, reason = self._ensure_upload_metadata(item)
        if not prepared:
            item["status"] = STATE_NOT_READY
            item["last_error"] = reason or "csv_unreadable"
            item["next_retry_at"] = None
            self._save_queue()
            return
        item["status"] = STATE_UPLOADING
        item["attempt_count"] = int(item.get("attempt_count") or 0) + 1
        item["last_attempt_at"] = _utc_now()
        item["last_error"] = None
        self._save_queue()
        gz_path: Path | None = None
        try:
            csv_path = Path(item["csv_path"])
            if not csv_path.is_file():
                raise FileNotFoundError("sensor CSV is missing")
            if not self._file_signature_matches(item, csv_path):
                raise ValueError("sensor CSV changed after queueing")
            gz_path = self._gzip_copy(csv_path, session_id)
            metadata = self._metadata(item, gz_path.stat().st_size)
            status, payload = self._upload_multipart(metadata, gz_path)
            if status in {200, 201}:
                self._mark_synced(item, payload)
                return
            if status == 409:
                if self._is_same_sha_idempotent(payload, item):
                    self._mark_synced(item, payload)
                    return
                item["status"] = STATE_CONFLICT
                item["last_error"] = "remote_session_sha_conflict"
                item["next_retry_at"] = None
                self._save_queue()
                return
            raise RuntimeError(self._payload_message(payload) or f"cloud upload failed:{status}")
        except Exception as exc:
            item["status"] = STATE_FAILED
            item["last_error"] = _safe_error(exc)
            item["next_retry_at"] = self._next_retry_at(item["attempt_count"])
        finally:
            if gz_path is not None:
                try:
                    gz_path.unlink(missing_ok=True)
                except OSError:
                    pass
            self._save_queue()

    def _ensure_upload_metadata(self, item: dict[str, Any]) -> tuple[bool, str | None]:
        csv_path = Path(str(item.get("csv_path") or ""))
        try:
            stat = csv_path.stat()
        except OSError:
            self._clear_analysis_cache(item)
            return False, "csv_unreadable"
        if not csv_path.is_file():
            self._clear_analysis_cache(item)
            return False, "csv_missing"
        if stat.st_size <= 0:
            self._clear_analysis_cache(item)
            return False, "csv_empty"
        if (
            item.get("csv_size") is not None
            and item.get("csv_mtime_ns") is not None
            and (
                int(item.get("csv_size") or 0) != stat.st_size
                or int(item.get("csv_mtime_ns") or 0) != stat.st_mtime_ns
            )
        ):
            self._clear_analysis_cache(item)
            item["csv_size"] = stat.st_size
            item["csv_mtime_ns"] = stat.st_mtime_ns
            item["original_size"] = stat.st_size
            self._observations.pop(str(item.get("session_id") or ""), None)
            return False, "csv_changed_after_discovery"
        if self._analysis_cache_valid(item, stat):
            return True, None
        try:
            csv_sha256 = self._sha256_file(csv_path)
            row_count, imu_count, emg_count = self._csv_metadata(csv_path)
        except (OSError, UnicodeError):
            self._clear_analysis_cache(item)
            return False, "csv_unreadable"
        if row_count <= 0:
            self._clear_analysis_cache(item)
            return False, "csv_empty"
        item.update(
            {
                "csv_size": stat.st_size,
                "csv_mtime_ns": stat.st_mtime_ns,
                "original_size": stat.st_size,
                "csv_sha256": csv_sha256,
                "row_count": row_count,
                "imu_channel_count": imu_count,
                "emg_channel_count": emg_count,
                "analysis_size": stat.st_size,
                "analysis_mtime_ns": stat.st_mtime_ns,
                "csv_analysis_cached_at": _utc_now(),
            }
        )
        self._save_queue()
        return True, None

    @staticmethod
    def _analysis_cache_valid(item: dict[str, Any], stat: os.stat_result) -> bool:
        return bool(
            item.get("csv_sha256")
            and isinstance(item.get("row_count"), int)
            and item.get("analysis_size") == stat.st_size
            and item.get("analysis_mtime_ns") == stat.st_mtime_ns
        )

    @staticmethod
    def _file_signature_matches(item: dict[str, Any], path: Path) -> bool:
        try:
            stat = path.stat()
        except OSError:
            return False
        return (
            stat.st_size == int(item.get("analysis_size") or item.get("csv_size") or -1)
            and stat.st_mtime_ns == int(item.get("analysis_mtime_ns") or item.get("csv_mtime_ns") or -1)
        )

    @staticmethod
    def _clear_analysis_cache(item: dict[str, Any]) -> None:
        for key in ANALYSIS_CACHE_FIELDS:
            item.pop(key, None)

    def _mark_synced(self, item: dict[str, Any], payload: dict[str, Any]) -> None:
        item["status"] = STATE_SYNCED
        item["synced_at"] = payload.get("uploaded_at") or _utc_now()
        item["remote_record_id"] = (
            payload.get("id")
            or payload.get("session_id")
            or item["session_id"]
        )
        item["last_error"] = None
        item["next_retry_at"] = None
        self._last_sync_at = item["synced_at"]
        self._save_queue()

    @staticmethod
    def _payload_message(payload: dict[str, Any]) -> str | None:
        detail = payload.get("detail")
        if isinstance(detail, dict):
            return detail.get("message") or detail.get("error_code")
        if isinstance(detail, str):
            return detail
        return payload.get("message") or payload.get("error_code")

    @staticmethod
    def _is_same_sha_idempotent(payload: dict[str, Any], item: dict[str, Any]) -> bool:
        detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
        error_code = payload.get("error_code") or detail.get("error_code")
        remote_sha = payload.get("csv_sha256") or detail.get("csv_sha256")
        remote_session = payload.get("session_id") or detail.get("session_id")
        idempotent_code = error_code in {
            "session_already_exists",
            "session_exists",
            "already_exists",
            "duplicate_session",
            "same_session_already_exists",
        }
        return bool(
            idempotent_code
            and remote_sha == item.get("csv_sha256")
            and (not remote_session or remote_session == item.get("session_id"))
        )

    def _check_cloud_health(self, force: bool = False) -> None:
        if not self.enabled:
            self._cloud_online = False
            return
        now = _monotonic()
        if not force and now - self._last_health_check_monotonic < self.health_interval_seconds:
            return
        self._last_health_check_monotonic = now
        self._last_check_at = _utc_now()
        try:
            status, data = self._request_json("GET", "/api/v1/cloud/health")
            self._cloud_online = status == 200 and data.get("status") == "ready"
        except Exception:
            self._cloud_online = False

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _csv_metadata(path: Path) -> tuple[int, int, int]:
        row_count = 0
        header = ""
        with path.open("r", encoding="utf-8", newline="") as handle:
            for index, line in enumerate(handle):
                if index == 0:
                    header = line.strip()
                    continue
                if line.strip():
                    row_count += 1
        columns = header.split(",") if header else []
        imu_count = len({column[:4] for column in columns if re.match(r"imu[0-9]_", column)})
        emg_count = len({column[-1:] for column in columns if re.match(r"emg_latest_[0-9]", column)})
        return row_count, imu_count, emg_count

    @staticmethod
    def _duration_seconds(started_at: str, ended_at: str) -> float | None:
        try:
            start = datetime.fromisoformat(started_at)
            end = datetime.fromisoformat(ended_at)
            return max(0.0, round((end - start).total_seconds(), 3))
        except (TypeError, ValueError):
            return None

    def _gzip_copy(self, csv_path: Path, session_id: str) -> Path:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f"{session_id}_", suffix=".csv.gz", dir=TMP_DIR)
        os.close(fd)
        gz_path = Path(temp_name)
        with csv_path.open("rb") as source, gzip.open(gz_path, "wb") as target:
            shutil.copyfileobj(source, target)
        return gz_path

    def _metadata(self, item: dict[str, Any], compressed_size: int) -> dict[str, Any]:
        salt = self._config["ESPCLAW_CLOUD_SUBJECT_SALT"]
        return {
            "schema_version": SCHEMA_VERSION,
            "device_id": self._config["ESPCLAW_CLOUD_DEVICE_ID"],
            "cloud_subject_id": _subject_id(PATIENT_ID, salt),
            "session_id": item["session_id"],
            "task_id": item.get("task_id") or "unknown",
            "started_at": item.get("started_at"),
            "ended_at": item.get("ended_at"),
            "duration_s": item.get("duration_s"),
            "csv_file": item.get("csv_file"),
            "original_size": item.get("original_size"),
            "compressed_size": compressed_size,
            "row_count": item.get("row_count"),
            "csv_sha256": item.get("csv_sha256"),
            "imu_channel_count": item.get("imu_channel_count"),
            "emg_channel_count": item.get("emg_channel_count"),
            "uploaded_at": _utc_now(),
        }

    @staticmethod
    def _next_retry_at(attempt_count: int) -> str:
        delay = RETRY_DELAYS_SECONDS[min(max(attempt_count - 1, 0), len(RETRY_DELAYS_SECONDS) - 1)]
        return datetime.fromtimestamp(time.time() + delay, tz=timezone.utc).isoformat()

    @staticmethod
    def _retry_due(item: dict[str, Any], now_iso: str) -> bool:
        next_retry = item.get("next_retry_at")
        return not isinstance(next_retry, str) or next_retry <= now_iso

    @staticmethod
    def _normalize_state(value: Any) -> str:
        state = str(value or "")
        if state in UPLOAD_STATES:
            return state
        if state == "pending":
            return STATE_NOT_UPLOADED
        if state == "ready":
            return STATE_NOT_UPLOADED
        return STATE_NOT_READY

    def _load_queue(self) -> None:
        try:
            data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        queue = data.get("sessions", data)
        if not isinstance(queue, dict):
            queue = {}
        self._queue = {}
        for session_id, item in queue.items():
            if not SESSION_ID_RE.fullmatch(str(session_id)) or not isinstance(item, dict):
                continue
            session_dir = self._path_resolver.resolve_score_session(str(session_id))
            if not session_dir.is_dir():
                continue
            copied = dict(item)
            csv_file = str(copied.get("csv_file") or "")
            if CSV_FILE_RE.fullmatch(csv_file):
                unified_csv = self._path_resolver.session_input_file_path(
                    str(session_id), csv_file
                )
                copied["csv_path"] = str(unified_csv.resolve())
            state = self._normalize_state(copied.get("status"))
            if state == STATE_UPLOADING:
                state = STATE_FAILED
                copied["last_error"] = "upload_interrupted"
                copied["next_retry_at"] = None
            copied["status"] = state
            self._queue[str(session_id)] = copied
        self._last_sync_at = data.get("last_sync_at") if isinstance(data, dict) else None

    def _save_queue(self) -> None:
        QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "doctor_cloud_sync_queue_v2",
            "last_sync_at": self._last_sync_at,
            "sessions": self._queue,
        }
        temporary = QUEUE_FILE.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(QUEUE_FILE)

    def _open_cloud_request(self, request: urllib.request.Request, timeout: float) -> Any:
        return self._direct_opener.open(request, timeout=timeout)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        base_url = self._config.get("ESPCLAW_CLOUD_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        timeout = _float_config(
            self._config.get("ESPCLAW_CLOUD_TIMEOUT_SECONDS"),
            DEFAULT_TIMEOUT_SECONDS,
            1.0,
        )
        headers = {}
        if authenticated:
            token = self._config.get("ESPCLAW_CLOUD_API_TOKEN") or ""
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(f"{base_url}{path}", method=method, headers=headers)
        with self._open_cloud_request(request, timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
            return int(response.status), data if isinstance(data, dict) else {}

    def _upload_multipart(self, metadata: dict[str, Any], gz_path: Path) -> tuple[int, dict[str, Any]]:
        base_url = self._config.get("ESPCLAW_CLOUD_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        token = self._config.get("ESPCLAW_CLOUD_API_TOKEN") or ""
        timeout = _float_config(
            self._config.get("ESPCLAW_CLOUD_TIMEOUT_SECONDS"),
            DEFAULT_TIMEOUT_SECONDS,
            1.0,
        )
        boundary = f"----espclaw{secrets.token_hex(16)}"
        body = self._multipart_body(boundary, metadata, gz_path)
        request = urllib.request.Request(
            f"{base_url}/api/v1/cloud/sessions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
        )
        try:
            with self._open_cloud_request(request, timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return int(response.status), payload if isinstance(payload, dict) else {}
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                payload = {"message": exc.reason}
            return int(exc.code), payload if isinstance(payload, dict) else {}

    @staticmethod
    def _multipart_body(boundary: str, metadata: dict[str, Any], gz_path: Path) -> bytes:
        encoded_metadata = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return b"".join(
            [
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="metadata"\r\n',
                b"Content-Type: application/json; charset=utf-8\r\n\r\n",
                encoded_metadata,
                b"\r\n",
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="file"; filename="sensor_data.csv.gz"\r\n',
                b"Content-Type: application/gzip\r\n\r\n",
                gz_path.read_bytes(),
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )

    @staticmethod
    def _public_status(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": item.get("session_id"),
            "status": CloudSyncService._normalize_state(item.get("status")),
            "attempt_count": int(item.get("attempt_count") or 0),
            "synced_at": item.get("synced_at"),
            "last_error": item.get("last_error"),
            "remote_record_id": item.get("remote_record_id"),
            "next_retry_at": item.get("next_retry_at"),
        }

    @staticmethod
    def _upload_response(
        accepted: bool,
        session_id: str,
        item: dict[str, Any] | None,
        reason: str | None,
    ) -> dict[str, Any]:
        return {
            "accepted": accepted,
            "session_id": session_id,
            "status": CloudSyncService._normalize_state((item or {}).get("status")),
            "reason": reason,
        }


cloud_sync_service = CloudSyncService()
