"""Persistent reservation, upload, identity, and recovery store for score jobs."""

import csv
import hashlib
import json
import os
import re
import shutil
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterable, Dict, Iterable, List, Optional, Tuple


PC_ROOT = Path(__file__).resolve().parents[2]
if str(PC_ROOT) not in sys.path:
    sys.path.insert(0, str(PC_ROOT))

from rehab_training_data.resolver import TrainingDataResolver


TASK_MAX_BYTES = 16 * 1024
CSV_MAX_BYTES = 128 * 1024 * 1024
SESSION_ID_RE = re.compile(r"^[0-9]{8}_TASK[0-9]{3}$")
CSV_FILE_RE = re.compile(r"^DATA_SEQ[0-9]{10}\.csv$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TASK_FIELDS = frozenset(
    {
        "task_name",
        "csv_file",
        "start_time",
        "end_time",
        "start_log_seq",
        "end_log_seq",
        "end_reason",
    }
)
CSV_HEADER = (
    "log_seq,time_ms,output_rate_hz,flags,"
    "node1_rx,node1_tx_seq,node1_tx_gap,node1_age_ms,"
    "node2_rx,node2_tx_seq,node2_tx_gap,node2_age_ms,"
    "emg_valid,emg_latest_1,emg_latest_2,emg_latest_3,emg_latest_4,"
    "emg_rms_1,emg_rms_2,emg_rms_3,emg_rms_4,"
    "emg_mav_1,emg_mav_2,emg_mav_3,emg_mav_4,"
    "emg_peak_1,emg_peak_2,emg_peak_3,emg_peak_4,emg_n,"
    "imu1_valid,imu1_age_ms,imu1_roll,imu1_pitch,imu1_yaw,imu1_gx,imu1_gy,imu1_gz,imu1_ax,imu1_ay,imu1_az,imu1_lax,imu1_lay,imu1_laz,"
    "imu2_valid,imu2_age_ms,imu2_roll,imu2_pitch,imu2_yaw,imu2_gx,imu2_gy,imu2_gz,imu2_ax,imu2_ay,imu2_az,imu2_lax,imu2_lay,imu2_laz,"
    "imu3_valid,imu3_age_ms,imu3_roll,imu3_pitch,imu3_yaw,imu3_gx,imu3_gy,imu3_gz,imu3_ax,imu3_ay,imu3_az,imu3_lax,imu3_lay,imu3_laz,"
    "imu4_valid,imu4_age_ms,imu4_roll,imu4_pitch,imu4_yaw,imu4_gx,imu4_gy,imu4_gz,imu4_ax,imu4_ay,imu4_az,imu4_lax,imu4_lay,imu4_laz"
)


class JobError(Exception):
    def __init__(self, status_code: int, error_code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_duplicate_keys(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate task.json field: {key}")
        result[key] = value
    return result


def parse_task_bytes(task_bytes: bytes) -> Dict[str, Any]:
    try:
        task = json.loads(
            task_bytes.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise JobError(422, "invalid_task_contract", str(exc)) from exc
    if not isinstance(task, dict) or set(task) != TASK_FIELDS:
        raise JobError(422, "invalid_task_contract", "task.json must contain exactly seven frozen fields")
    for field in ("task_name", "csv_file", "start_time", "end_time", "end_reason"):
        if not isinstance(task[field], str) or not task[field].strip():
            raise JobError(422, "invalid_task_contract", f"{field} must be a non-empty string")
    for field in ("start_log_seq", "end_log_seq"):
        if isinstance(task[field], bool) or not isinstance(task[field], int):
            raise JobError(422, "invalid_task_contract", f"{field} must be an integer")
    if task["end_log_seq"] < task["start_log_seq"]:
        raise JobError(422, "invalid_task_contract", "end_log_seq precedes start_log_seq")
    if not CSV_FILE_RE.fullmatch(task["csv_file"]):
        raise JobError(422, "invalid_task_contract", "csv_file does not match the frozen filename contract")
    if task["end_reason"] not in {"user_stop", "completed"}:
        raise JobError(422, "invalid_task_contract", "end_reason is not a finalized successful session")
    try:
        start = datetime.fromisoformat(task["start_time"])
        end = datetime.fromisoformat(task["end_time"])
    except ValueError as exc:
        raise JobError(422, "invalid_task_contract", "start_time/end_time must be ISO-8601") from exc
    try:
        if end < start:
            raise JobError(422, "invalid_task_contract", "end_time precedes start_time")
    except TypeError as exc:
        raise JobError(422, "invalid_task_contract", "start_time/end_time timezone forms must match") from exc
    return task


class ScoreJobStore:
    def __init__(self, runtime_root: Path):
        self.runtime_root = Path(runtime_root).resolve()
        self.path_resolver = TrainingDataResolver(training_root=self.runtime_root)
        self.staging_root = self.path_resolver.score_staging_root()
        self.sessions_root = self.path_resolver.score_sessions_root()
        self.scoring_staging_root = self.path_resolver.scoring_staging_root()
        self._lock = threading.RLock()

    def ensure_ready(self) -> None:
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self.scoring_staging_root.mkdir(parents=True, exist_ok=True)
        self.path_resolver.running_root().mkdir(parents=True, exist_ok=True)
        self.path_resolver.locks_root().mkdir(parents=True, exist_ok=True)
        self.path_resolver.queue_root().mkdir(parents=True, exist_ok=True)
        self.sessions_root.mkdir(parents=True, exist_ok=True)

    @property
    def ready(self) -> bool:
        return all(
            path.is_dir()
            for path in (
                self.staging_root,
                self.scoring_staging_root,
                self.path_resolver.running_root(),
                self.path_resolver.locks_root(),
                self.path_resolver.queue_root(),
                self.sessions_root,
            )
        )

    def _job_dir(self, job_id: str) -> Optional[Path]:
        if not SESSION_ID_RE.fullmatch(job_id):
            return None
        session_dir = self.path_resolver.resolve_score_session(job_id)
        staging_dir = self.path_resolver.score_staging_session_path(job_id)
        if self.path_resolver.session_input_path(job_id).is_dir():
            return session_dir
        if staging_dir.is_dir():
            return staging_dir
        return None

    def _status_path(self, job_id: str) -> Path:
        return self.path_resolver.job_status_path(job_id)

    def read_status(self, job_id: str) -> Dict[str, Any]:
        if not SESSION_ID_RE.fullmatch(job_id) or not self._status_path(job_id).is_file():
            raise JobError(404, "job_not_found", "score job was not found")
        try:
            status = json.loads(self._status_path(job_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise JobError(500, "job_status_unreadable", "persistent job status is unreadable") from exc
        if not isinstance(status, dict) or status.get("job_id") != job_id:
            raise JobError(500, "job_status_invalid", "persistent job status is invalid")
        return status

    def atomic_write_status(self, job_id: str, status: Dict[str, Any]) -> None:
        status = dict(status)
        status["updated_at"] = utc_now()
        target = self._status_path(job_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        encoded = json.dumps(status, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        try:
            with temp.open("wb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, target)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def public_status(status: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "schema_version": "rehab_score_job_status_v1",
            "job_id": status["job_id"],
            "session_id": status["session_id"],
            "state": status["state"],
            "retry_count": int(status.get("retry_count", 0)),
            "created_at": status["created_at"],
            "updated_at": status["updated_at"],
            "error_code": status.get("error_code"),
            "message": status.get("message"),
        }

    @staticmethod
    def job_response(status: Dict[str, Any], upload_required: bool) -> Dict[str, Any]:
        return {
            "schema_version": "rehab_score_job_response_v1",
            "job_id": status["job_id"],
            "session_id": status["session_id"],
            "state": status["state"],
            "upload_required": upload_required,
            "created_at": status["created_at"],
            "updated_at": status["updated_at"],
        }

    @staticmethod
    def _same_identity(status: Dict[str, Any], identity: Dict[str, Any]) -> bool:
        fields = (
            "task_sha256",
            "csv_file",
            "csv_size",
            "csv_sha256",
            "start_log_seq",
            "end_log_seq",
        )
        return all(status.get(field) == identity.get(field) for field in fields)

    def create_or_reuse(
        self,
        session_id: str,
        csv_size: int,
        csv_sha256: str,
        task_bytes: bytes,
    ) -> Tuple[Dict[str, Any], bool]:
        if not SESSION_ID_RE.fullmatch(session_id):
            raise JobError(422, "invalid_session_id", "X-Session-Id has an invalid format")
        if csv_size < 1:
            raise JobError(422, "invalid_csv_size", "X-CSV-Size must be positive")
        if csv_size > CSV_MAX_BYTES:
            raise JobError(413, "csv_too_large", "CSV reservation exceeds 128 MiB")
        if not SHA256_RE.fullmatch(csv_sha256):
            raise JobError(422, "invalid_csv_sha256", "X-CSV-SHA256 must be 64 lowercase hex characters")
        if len(task_bytes) > TASK_MAX_BYTES:
            raise JobError(413, "task_too_large", "task.json exceeds 16 KiB")
        task = parse_task_bytes(task_bytes)
        identity = {
            "task_sha256": hashlib.sha256(task_bytes).hexdigest(),
            "csv_file": task["csv_file"],
            "csv_size": csv_size,
            "csv_sha256": csv_sha256,
            "start_log_seq": task["start_log_seq"],
            "end_log_seq": task["end_log_seq"],
        }
        with self._lock:
            self.ensure_ready()
            existing_dir = self._job_dir(session_id)
            if existing_dir is not None:
                status_path = self._status_path(session_id)
                if not status_path.is_file() and existing_dir.parent == self.sessions_root:
                    input_dir = self.path_resolver.session_input_path(session_id)
                    task_path = input_dir / "task.json"
                    csv_path = input_dir / task["csv_file"]
                    if (
                        not task_path.is_file()
                        or not csv_path.is_file()
                        or hashlib.sha256(task_path.read_bytes()).hexdigest()
                        != identity["task_sha256"]
                        or csv_path.stat().st_size != csv_size
                        or sha256_file(csv_path) != csv_sha256
                    ):
                        raise JobError(
                            409,
                            "session_content_conflict",
                            "existing session content does not match the score job identity",
                        )
                    now = utc_now()
                    status = {
                        "schema_version": "rehab_score_pc_job_v1",
                        "job_id": session_id,
                        "session_id": session_id,
                        "state": "accepted",
                        **identity,
                        "task_name": task["task_name"],
                        "inputs_validated": True,
                        "retry_count": 0,
                        "created_at": now,
                        "updated_at": now,
                        "completed_at": None,
                        "error_code": None,
                        "message": None,
                    }
                    self.atomic_write_status(session_id, status)
                    return self.read_status(session_id), False
                existing = self.read_status(session_id)
                if not self._same_identity(existing, identity):
                    raise JobError(409, "session_content_conflict", "session_id is reserved for different content")
                upload_required = existing_dir.parent == self.staging_root
                return existing, False

            job_dir = self.path_resolver.score_staging_session_path(session_id)
            session_dir = self.path_resolver.resolve_score_session(session_id)
            session_preexisted = session_dir.exists()
            try:
                job_dir.mkdir()
                input_dir = job_dir / "input"
                input_dir.mkdir()
                with (input_dir / "task.json").open("wb") as task_file:
                    task_file.write(task_bytes)
                    task_file.flush()
                    os.fsync(task_file.fileno())
                now = utc_now()
                status = {
                    "schema_version": "rehab_score_pc_job_v1",
                    "job_id": session_id,
                    "session_id": session_id,
                    "state": "uploading",
                    **identity,
                    "task_name": task["task_name"],
                    "inputs_validated": False,
                    "retry_count": 0,
                    "created_at": now,
                    "updated_at": now,
                    "completed_at": None,
                    "error_code": None,
                    "message": None,
                }
                self.atomic_write_status(session_id, status)
                return self.read_status(session_id), True
            except Exception:
                shutil.rmtree(job_dir, ignore_errors=True)
                shutil.rmtree(self.path_resolver.session_runtime_path(session_id), ignore_errors=True)
                if not session_preexisted:
                    try:
                        session_dir.rmdir()
                    except OSError:
                        pass
                raise

    def update_state(
        self,
        job_id: str,
        state: str,
        error_code: Optional[str] = None,
        message: Optional[str] = None,
        **fields: Any,
    ) -> Dict[str, Any]:
        with self._lock:
            job_dir = self._job_dir(job_id)
            if job_dir is None:
                raise JobError(404, "job_not_found", "score job was not found")
            status = self.read_status(job_id)
            status.update(fields)
            status.update(state=state, error_code=error_code, message=message)
            self.atomic_write_status(job_id, status)
            return self.read_status(job_id)

    @staticmethod
    def _parse_log_seq(line: bytes) -> int:
        try:
            return int(line.split(b",", 1)[0].decode("ascii"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise JobError(422, "invalid_csv_log_seq", "CSV row has an invalid log_seq") from exc

    async def receive_csv(
        self,
        job_id: str,
        content_length: int,
        chunks: AsyncIterable[bytes],
    ) -> Tuple[Dict[str, Any], bool]:
        if not SESSION_ID_RE.fullmatch(job_id):
            raise JobError(422, "invalid_job_id", "job_id has an invalid format")
        with self._lock:
            status = self.read_status(job_id)
            job_dir = self._job_dir(job_id)
            assert job_dir is not None
            if job_dir.parent == self.sessions_root:
                if content_length != int(status["csv_size"]):
                    raise JobError(409, "session_content_conflict", "CSV length conflicts with the accepted job")
                return status, False
            if content_length != int(status["csv_size"]):
                shutil.rmtree(job_dir, ignore_errors=True)
                shutil.rmtree(
                    self.path_resolver.resolve_score_session(job_id),
                    ignore_errors=True,
                )
                raise JobError(422, "csv_size_mismatch", "Content-Length does not match reservation")
            part_path = job_dir / "input" / f"{status['csv_file']}.part"

        digest = hashlib.sha256()
        total = 0
        buffer = b""
        header_seen = False
        first_seq: Optional[int] = None
        last_seq: Optional[int] = None
        try:
            with part_path.open("wb") as output:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > int(status["csv_size"]):
                        raise JobError(422, "csv_size_mismatch", "uploaded CSV exceeds its reservation")
                    output.write(chunk)
                    digest.update(chunk)
                    buffer += chunk
                    if len(buffer) > 1024 * 1024 and b"\n" not in buffer:
                        raise JobError(422, "invalid_csv_row", "CSV row exceeds the safety limit")
                    while b"\n" in buffer:
                        raw_line, buffer = buffer.split(b"\n", 1)
                        line = raw_line.rstrip(b"\r")
                        if not header_seen:
                            try:
                                header = line.decode("ascii")
                            except UnicodeDecodeError as exc:
                                raise JobError(422, "invalid_csv_header", "CSV header must be ASCII") from exc
                            if header != CSV_HEADER or len(next(csv.reader([header]))) != 86:
                                raise JobError(422, "invalid_csv_header", "CSV header does not match the frozen 86-column contract")
                            header_seen = True
                        elif line:
                            seq = self._parse_log_seq(line)
                            first_seq = seq if first_seq is None else first_seq
                            last_seq = seq
                if buffer:
                    line = buffer.rstrip(b"\r")
                    if not header_seen:
                        raise JobError(422, "invalid_csv_header", "CSV has no complete header")
                    if line:
                        seq = self._parse_log_seq(line)
                        first_seq = seq if first_seq is None else first_seq
                        last_seq = seq
                output.flush()
                os.fsync(output.fileno())
            if total != int(status["csv_size"]):
                raise JobError(422, "csv_size_mismatch", "uploaded CSV size does not match reservation")
            if digest.hexdigest() != status["csv_sha256"]:
                raise JobError(422, "csv_sha256_mismatch", "uploaded CSV SHA-256 does not match reservation")
            if not header_seen or first_seq is None or last_seq is None:
                raise JobError(422, "csv_empty", "CSV must contain its header and at least one data row")
            if first_seq != int(status["start_log_seq"]) or last_seq != int(status["end_log_seq"]):
                raise JobError(422, "csv_log_seq_mismatch", "CSV first/last log_seq do not match task.json")

            with self._lock:
                current = self.read_status(job_id)
                if not self._same_identity(current, status):
                    raise JobError(409, "session_content_conflict", "reservation changed during upload")
                final_csv = job_dir / "input" / status["csv_file"]
                os.replace(part_path, final_csv)
                current.update(
                    state="accepted",
                    inputs_validated=True,
                    error_code=None,
                    message=None,
                )
                self.atomic_write_status(job_id, current)
                session_dir = self.path_resolver.resolve_score_session(job_id)
                input_dir = self.path_resolver.session_input_path(job_id)
                if input_dir.exists():
                    raise JobError(409, "session_content_conflict", "formal session input already exists")
                session_dir.mkdir(parents=True, exist_ok=True)
                os.replace(job_dir / "input", input_dir)
                job_dir.rmdir()
                return self.read_status(job_id), True
        except JobError as exc:
            with self._lock:
                job_dir = self._job_dir(job_id)
                if job_dir is not None and job_dir.parent == self.staging_root:
                    shutil.rmtree(job_dir, ignore_errors=True)
                    shutil.rmtree(
                        self.path_resolver.resolve_score_session(job_id),
                        ignore_errors=True,
                    )
            raise
        except Exception as exc:
            with self._lock:
                job_dir = self._job_dir(job_id)
                if job_dir is not None and job_dir.parent == self.staging_root:
                    shutil.rmtree(job_dir, ignore_errors=True)
                    shutil.rmtree(
                        self.path_resolver.resolve_score_session(job_id),
                        ignore_errors=True,
                    )
            raise JobError(422, "csv_upload_failed", "CSV upload could not be completed") from exc

    def validated_session_dir(self, job_id: str) -> Path:
        status = self.read_status(job_id)
        input_dir = self.path_resolver.session_input_path(job_id)
        if not input_dir.is_dir() or not status.get("inputs_validated"):
            raise JobError(409, "validated_inputs_unavailable", "job does not have validated formal inputs")
        return input_dir

    def retry(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            status = self.read_status(job_id)
            if status["state"] != "failed":
                code = "succeeded_job_not_retryable" if status["state"] == "succeeded" else "job_not_retryable"
                raise JobError(409, code, "only failed jobs with validated inputs can be retried")
            self.validated_session_dir(job_id)
            return self.update_state(
                job_id,
                "accepted",
                retry_count=int(status.get("retry_count", 0)) + 1,
            )

    def recover(self) -> List[str]:
        self.ensure_ready()
        self._discard_orphan_staging()
        enqueue: List[str] = []
        for session_dir in self.sessions_root.iterdir():
            if not session_dir.is_dir():
                continue
            job_id = session_dir.name
            if not SESSION_ID_RE.fullmatch(job_id):
                continue
            status_path = self.path_resolver.job_status_path(job_id)
            if not status_path.is_file():
                continue
            try:
                status = self.read_status(job_id)
            except JobError:
                continue
            state = status.get("state")
            input_dir = self.path_resolver.session_input_path(job_id)
            full_result_path = (
                self.path_resolver.session_scoring_path(job_id) / "full_result.json"
            )
            if full_result_path.is_file():
                if state != "succeeded":
                    self.update_state(job_id, "succeeded", completed_at=utc_now())
            elif state == "scoring":
                shutil.rmtree(
                    self.path_resolver.scoring_staging_session_path(job_id),
                    ignore_errors=True,
                )
                if input_dir.is_dir():
                    self.update_state(job_id, "accepted")
                    enqueue.append(job_id)
            elif state == "accepted":
                if input_dir.is_dir():
                    enqueue.append(job_id)
            elif state == "succeeded" and not full_result_path.is_file():
                self.update_state(job_id, "failed", "result_missing_after_restart", "succeeded result is incomplete")
        return enqueue

    def _discard_orphan_staging(self) -> None:
        """Remove temporary work whose owning session has been deleted."""

        for staging_root in (self.staging_root, self.scoring_staging_root):
            if not staging_root.is_dir():
                continue
            for candidate in staging_root.iterdir():
                if (
                    candidate.is_dir()
                    and SESSION_ID_RE.fullmatch(candidate.name)
                    and not self.path_resolver.resolve_score_session(candidate.name).is_dir()
                ):
                    shutil.rmtree(candidate, ignore_errors=True)
