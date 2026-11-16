from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import threading
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR, PATIENT_ID, TRAINING_DATA_ROOT
from rehab_training_data.resolver import TrainingDataResolver
from rehab_score_service.scoring_assets import DERIVED_ROOT as SCORE_ASSET_DERIVED_ROOT, TEMPLATE_ROOT

from .device_history_store import device_history_store
from .task_registry_adapter import legacy_task_fields

SESSION_SCORE_STATUS_FILE = "session_v12_scoring.json"
SESSION_V12_STATUS_VALUES = {"not_started", "pending", "running", "completed", "failed"}
DOCTOR_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PC_ROOT = DOCTOR_PROJECT_ROOT.parent
DEFAULT_SCORE_SERVICE_ROOT = DEFAULT_PC_ROOT / "rehab_score_service"
SCORE_SERVICE_ROOT = Path(
    os.getenv("REHAB_SCORE_ROOT")
    or os.getenv("SCORE_SERVICE_ROOT")
    or DEFAULT_SCORE_SERVICE_ROOT
).resolve()
BASELINE_TEMPLATE_PATH = Path(
    os.getenv("REHAB_SCORE_BASELINE_TEMPLATE")
    or TEMPLATE_ROOT / "MHH_healthy_template.json"
).resolve()
SESSION_SCORING_TOOL_PATH = Path(
    os.getenv("REHAB_DRINK_SESSION_SCORING_TOOL")
    or SCORE_SERVICE_ROOT / "tools" / "drink_water_session_scoring_v1.py"
).resolve()
LATERAL_SESSION_SCORING_TOOL_PATH = Path(
    os.getenv("REHAB_LATERAL_SESSION_SCORING_TOOL")
    or SCORE_SERVICE_ROOT / "tools" / "lateral_transfer_session_scoring_v1.py"
).resolve()
LATERAL_TEMPLATE_PATH = Path(
    os.getenv("REHAB_LATERAL_BASELINE_TEMPLATE")
    or TEMPLATE_ROOT / "lateral_transfer_healthy_template_v1.json"
).resolve()


def _default_score_python() -> Path:
    configured = os.getenv("REHAB_SCORE_PYTHON")
    if configured:
        return Path(configured).resolve()
    local_venv = SCORE_SERVICE_ROOT / ".venv" / "Scripts" / "python.exe"
    if local_venv.is_file():
        return local_venv.resolve()
    return Path(__file__).resolve().parents[5] / "external" / "_conda_envs" / "rehab_score" / "python.exe"


SESSION_SCORING_PYTHON = _default_score_python()
EXPECTED_BASELINE_VERSION = "drink_water_v2.1.4_reasonable_speed"
EXPECTED_SENSOR_MAPPING_VERSION = "rehab_sensor_mapping_20260806"
EXPECTED_SEGMENTATION_VERSION = "drink_hybrid_segmentation_v2_1"
EXPECTED_SCORE_VERSION = "drink_water_session_v1_2"
EXPECTED_DETECTOR_RULE_VERSION = "drink_water_ratio_lf_v1"
EXPECTED_LATERAL_BASELINE_VERSION = "lateral_transfer_healthy_template_v1"
EXPECTED_LATERAL_SEGMENTATION_VERSION = "lateral_transfer_segmentation_drink_reuse_v1"
EXPECTED_LATERAL_SCORE_VERSION = "lateral_transfer_session_scoring_v1"
EXPECTED_FORMAL_TEMPLATE_SHA256 = "464A8FF098893BECEA106DCD80360E0FD15A4302CBB1207C8249A8AB941D73F6"
SCORE_JOB_SCORING_AUTHORITY = "score_service_dedicated_job"
SCORE_JOB_ARTIFACT_ORIGIN = "score_service_runtime"
LATERAL_FORMAL_SCORE_ROOT = Path(
    os.getenv(
        "REHAB_LATERAL_FORMAL_SCORE_ROOT",
        str(SCORE_ASSET_DERIVED_ROOT / "lateral_transfer_v1"),
    )
).resolve()
LATERAL_FORMAL_SCORE_DIR_PREFIX = "approved_final_validation_"
LATERAL_COMPENSATION_SOURCE_CURRENT = "current_lateral_formal_per_rep_score"
LATERAL_COMPENSATION_SOURCE_HISTORICAL = "historical_doctor_session_per_rep_score"
FRESHNESS_KEYS = (
    "source_sha256",
    "score_version",
    "baseline_version",
    "formal_template_sha256",
    "sensor_mapping_version",
    "detector_rule_version",
)
MAX_SESSION_DETAIL_CACHE = 64
MAX_SHA256_CACHE = 128


def _score_path_resolver() -> TrainingDataResolver:
    return TrainingDataResolver(training_root=TRAINING_DATA_ROOT)


_SHA256_CACHE_LOCK = threading.RLock()
_SHA256_CACHE: OrderedDict[tuple[str, int, int], str] = OrderedDict()

IMU_MAP = {
    "imu1": "upper_arm_imu",
    "imu2": "lower_back_imu",
    "imu3": "forearm_imu",
    "imu4": "wrist_imu",
}
EMG_MAP = {
    "emg1": "CH1",
    "emg2": "CH2",
    "emg3": "CH3",
    "emg4": "CH4",
}
COMPENSATION_LABELS = {
    "LF": "躯干前倾",
    "TR": "躯干旋转",
    "TREMOR": "手部抖动",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _sha256_file_uncached(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    resolved = path.resolve()
    stat = resolved.stat()
    key = (str(resolved), int(stat.st_size), int(stat.st_mtime_ns))
    with _SHA256_CACHE_LOCK:
        cached = _SHA256_CACHE.get(key)
        if cached is not None:
            _SHA256_CACHE.move_to_end(key)
            return cached
    digest = _sha256_file_uncached(resolved)
    with _SHA256_CACHE_LOCK:
        _SHA256_CACHE[key] = digest
        _SHA256_CACHE.move_to_end(key)
        while len(_SHA256_CACHE) > MAX_SHA256_CACHE:
            _SHA256_CACHE.popitem(last=False)
    return digest


def _normalize_sha256(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text.lower() if text else None


def _sha256_equal(left: Any, right: Any) -> bool:
    return _normalize_sha256(left) == _normalize_sha256(right)


def _finite_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _status_path(session_id: str) -> Path:
    return (
        device_history_store.session_directory(session_id)
        / "doctor"
        / SESSION_SCORE_STATUS_FILE
    )


def _current_formal_template_sha256() -> str | None:
    return _sha256_file(BASELINE_TEMPLATE_PATH) if BASELINE_TEMPLATE_PATH.is_file() else None


class SessionV12ScoringService:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._detail_cache_lock = threading.RLock()
        self._detail_cache: OrderedDict[tuple[str, tuple[Any, ...]], dict[str, Any]] = OrderedDict()

    @staticmethod
    def formal_baseline_precheck() -> dict[str, Any]:
        formal: dict[str, Any] | None = None
        task: Any = None
        reason = None
        if not BASELINE_TEMPLATE_PATH.is_file():
            reason = "formal_template_missing"
        else:
            try:
                parsed = json.loads(BASELINE_TEMPLATE_PATH.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                reason = "formal_template_invalid_json"
            else:
                formal = parsed if isinstance(parsed, dict) else None
                if formal is None:
                    reason = "formal_template_invalid_json"
                else:
                    task = formal.get("12")
                    if not isinstance(task, dict):
                        reason = "task12_missing"
        formal_sha = _current_formal_template_sha256()
        if reason is None and task.get("baseline_version") != EXPECTED_BASELINE_VERSION:
            reason = "baseline_version_mismatch"
        if reason is None and task.get("segmentation_version") != EXPECTED_SEGMENTATION_VERSION:
            reason = "segmentation_version_mismatch"
        if reason is None and task.get("sensor_mapping_version") != EXPECTED_SENSOR_MAPPING_VERSION:
            reason = "sensor_mapping_version_mismatch"
        if reason is None and not _sha256_equal(formal_sha, EXPECTED_FORMAL_TEMPLATE_SHA256):
            reason = "formal_template_sha256_mismatch"
        ok = reason is None
        return {
            "ok": ok,
            "baseline_template": str(BASELINE_TEMPLATE_PATH),
            "baseline_version": task.get("baseline_version") if isinstance(task, dict) else None,
            "segmentation_version": task.get("segmentation_version") if isinstance(task, dict) else None,
            "sensor_mapping_version": task.get("sensor_mapping_version") if isinstance(task, dict) else None,
            "formal_template_sha256": formal_sha,
            "expected_formal_template_sha256": EXPECTED_FORMAL_TEMPLATE_SHA256,
            "detector_rule_version": EXPECTED_DETECTOR_RULE_VERSION,
            "reason": reason,
        }

    def submit_from_runtime_summary(self, summary: dict[str, Any]) -> None:
        # R3-2: Score is the only scoring writer. Doctor observes/adopts the
        # published Score artifact and never launches its legacy local scorer.
        return

    def adopt_from_score_runtime(
        self,
        summary: dict[str, Any],
        runtime_session_dir: Path,
    ) -> dict[str, Any]:
        session_id = str(summary.get("session_id") or "")
        if not session_id or runtime_session_dir.name != session_id:
            return {"accepted": False, "reason": "session_id_mismatch"}
        try:
            fixed_root = _score_path_resolver().score_sessions_root().resolve()
            resolved_session_dir = runtime_session_dir.resolve()
            resolved_session_dir.relative_to(fixed_root)
        except (OSError, ValueError):
            return {"accepted": False, "reason": "runtime_session_path_invalid"}
        try:
            artifact = self._validated_score_job_artifact(summary, resolved_session_dir)
        except Exception as exc:
            self._write_adopted_status(session_id, "failed", summary, None, {}, {}, error=str(exc))
            return {"accepted": False, "reason": str(exc)}

        source_sha = artifact["source_sha256"]
        existing_status = self.status(session_id)
        if existing_status.get("status") == "completed" and _sha256_equal(
            existing_status.get("source_sha256"), source_sha
        ):
            self._write_adopted_status(
                session_id,
                "completed",
                summary,
                source_sha,
                artifact["score_result"],
                artifact["session_summary"],
            )
            return {"accepted": True, "adopted": False, "reused": True}
        try:
            trajectories_dir = (
                device_history_store.session_directory(session_id)
                / "doctor"
                / "trajectory"
            )
            self._generate_trajectories(
                session_id,
                Path(artifact["dedicated_dir"]),
                trajectories_dir,
            )
            self._write_adopted_status(
                session_id,
                "completed",
                summary,
                source_sha,
                artifact["score_result"],
                artifact["session_summary"],
            )
            return {"accepted": True, "adopted": True, "backup": None}
        except Exception as exc:
            self._write_adopted_status(
                session_id,
                "failed",
                summary,
                source_sha,
                artifact.get("score_result", {}),
                artifact.get("session_summary", {}),
                error=str(exc),
            )
            return {"accepted": False, "reason": str(exc)}

    def status(self, session_id: str) -> dict[str, Any]:
        value = _read_json(_status_path(session_id))
        if isinstance(value, dict) and value.get("status") in SESSION_V12_STATUS_VALUES:
            return deepcopy(value)
        return {"session_id": session_id, "status": "not_started"}

    def session_detail(self, session_id: str) -> dict[str, Any] | None:
        fingerprint = self._session_detail_fingerprint(session_id)
        cached = self._get_cached_completed_detail(session_id, fingerprint)
        if cached is not None:
            return cached
        session_dir = device_history_store.session_directory(session_id)
        session = _read_json(session_dir / "session.json")
        if not isinstance(session, dict):
            return None
        scoring = self._read_session_scoring(session_id)
        status = self.status(session_id)
        if scoring is None:
            return self._pending_detail(session, status)
        detail = self._completed_detail(session, scoring, status)
        if detail.get("scoring_status") == "completed":
            self._store_completed_detail(session_id, fingerprint, detail)
        return detail

    def _get_cached_completed_detail(
        self, session_id: str, fingerprint: tuple[Any, ...]
    ) -> dict[str, Any] | None:
        key = (session_id, fingerprint)
        with self._detail_cache_lock:
            cached = self._detail_cache.get(key)
            if cached is None:
                return None
            self._detail_cache.move_to_end(key)
            return deepcopy(cached)

    def _store_completed_detail(
        self, session_id: str, fingerprint: tuple[Any, ...], detail: dict[str, Any]
    ) -> None:
        key = (session_id, fingerprint)
        with self._detail_cache_lock:
            stale_keys = [item for item in self._detail_cache if item[0] == session_id and item != key]
            for item in stale_keys:
                self._detail_cache.pop(item, None)
            self._detail_cache[key] = deepcopy(detail)
            self._detail_cache.move_to_end(key)
            while len(self._detail_cache) > MAX_SESSION_DETAIL_CACHE:
                self._detail_cache.popitem(last=False)

    def _drop_session_detail_cache(self, session_id: str) -> None:
        with self._detail_cache_lock:
            stale_keys = [item for item in self._detail_cache if item[0] == session_id]
            for item in stale_keys:
                self._detail_cache.pop(item, None)

    @staticmethod
    def _file_signature(path: Path) -> tuple[str, bool, int, int]:
        try:
            resolved = path.resolve()
            stat = resolved.stat()
            return (str(resolved), True, int(stat.st_size), int(stat.st_mtime_ns))
        except OSError:
            try:
                return (str(path.resolve()), False, 0, 0)
            except OSError:
                return (str(path), False, 0, 0)

    def _session_detail_fingerprint(self, session_id: str) -> tuple[Any, ...]:
        session_dir = device_history_store.session_directory(session_id)
        scoring_dir = session_dir / "scoring"
        files: list[Path] = [
            session_dir / "session.json",
            _status_path(session_id),
            scoring_dir / "full_result.json",
        ]
        for directory, pattern in (
            (scoring_dir / "intermediate" / "dedicated_scoring" / session_id / "per_rep_scores", "rep_*_score.json"),
            (scoring_dir / "intermediate" / "dedicated_scoring" / session_id / "repetitions", "rep_*.metadata.json"),
            (session_dir / "doctor" / "trajectory", "rep_*_trajectory.json"),
        ):
            try:
                files.extend(sorted(directory.glob(pattern)))
            except OSError:
                files.append(directory)
        latest_lateral_dir = self._latest_lateral_formal_score_dir()
        if latest_lateral_dir is not None:
            files.append(latest_lateral_dir)
            lateral_score_dir = latest_lateral_dir / session_id / "per_rep_scores"
            try:
                files.extend(sorted(lateral_score_dir.glob("rep_*_score.json")))
            except OSError:
                files.append(lateral_score_dir)
        return tuple(self._file_signature(path) for path in files)

    def canonical_session_summary(self, session: dict[str, Any]) -> dict[str, Any]:
        session_id = str(session.get("session_id") or "")
        if not session_id:
            return deepcopy(session)
        detail = self.session_detail(session_id)
        if not detail or detail.get("scoring_status") != "completed":
            return deepcopy(session)
        stats = detail.get("statistics") if isinstance(detail.get("statistics"), dict) else {}
        average = _finite_number(stats.get("average_score"))
        canonical = deepcopy(session)
        canonical.update(
            {
                "session_v12_status": detail.get("scoring_status"),
                "session_v12_error": detail.get("error"),
                "session_v12_baseline_version": detail.get("baseline_version"),
                "final_score": round(average, 2) if average is not None else None,
                "report_ready": average is not None,
                "score_valid": average is not None,
                "repetition_count": detail.get("repetition_count"),
                "valid_repetition_count": detail.get("valid_repetition_count"),
                "score_status": "completed",
                "scoring_status": "completed",
            }
        )
        for key in (
            "task_name",
            "original_task_name",
            "task_registry_version",
            "task_registry_status",
            "unsupported_task_reason",
            "motion_family",
            "object_type",
            "score_profile",
            "twin_profile",
            "task_level",
        ):
            if detail.get(key) is not None:
                canonical[key] = detail.get(key)
        return canonical

    def canonical_report(
        self, session_id: str, legacy_report: dict[str, Any] | None = None, session: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        detail = self.session_detail(session_id)
        if not detail or detail.get("scoring_status") != "completed":
            return deepcopy(legacy_report) if legacy_report else None
        return self._report_from_detail(detail, legacy_report or {}, session or {})

    def repetition_detail(self, session_id: str, rep_index: int) -> dict[str, Any] | None:
        detail = self.session_detail(session_id)
        if detail is None:
            return None
        for rep in detail.get("repetitions", []):
            if int(rep.get("rep_index") or -1) == rep_index:
                return rep
        return None

    def rescore(self, session_id: str) -> dict[str, Any]:
        if not session_id:
            return {"accepted": False, "session_id": session_id, "status": "not_found", "reason": "session_not_found"}
        return {
            "accepted": False,
            "session_id": session_id,
            "status": self.status(session_id).get("status"),
            "reason": "score_service_authority_required",
        }

    def trajectory(self, session_id: str, rep_index: int) -> dict[str, Any] | None:
        path = (
            device_history_store.session_directory(session_id)
            / "doctor"
            / "trajectory"
            / f"rep_{rep_index:02d}_trajectory.json"
        )
        value = _read_json(path)
        return deepcopy(value) if isinstance(value, dict) else None

    @staticmethod
    def _eligible_runtime_summary(summary: dict[str, Any]) -> bool:
        return bool(
            summary.get("source_mode") == "device"
            and summary.get("source_origin") == "pc_udp_6006_ingest"
            and summary.get("task_finalized") is True
            and summary.get("csv_relative_path")
            and summary.get("session_id")
        )

    def _validated_score_job_artifact(
        self,
        summary: dict[str, Any],
        runtime_session_dir: Path,
    ) -> dict[str, Any]:
        session_id = str(summary.get("session_id") or "")
        resolver = _score_path_resolver()
        job_status = _read_json(resolver.job_status_path(session_id))
        if not isinstance(job_status, dict):
            raise RuntimeError("score_job_status_missing")
        if job_status.get("state") != "succeeded":
            raise RuntimeError("score_job_not_succeeded")
        csv_file = str(job_status.get("csv_file") or "")
        if not csv_file or "/" in csv_file or "\\" in csv_file:
            raise RuntimeError("score_job_csv_file_invalid")
        input_dir = resolver.session_input_path(session_id).resolve()
        csv_path = (input_dir / csv_file).resolve()
        csv_path.relative_to(input_dir)
        if not csv_path.is_file():
            raise RuntimeError("score_job_source_csv_missing")
        actual_sha = _sha256_file(csv_path)
        for key in ("job_id", "session_id"):
            value = str(job_status.get(key) or "")
            if value and value != session_id:
                raise RuntimeError(f"score_job_{key}_mismatch")
        if job_status.get("csv_sha256") and not _sha256_equal(job_status.get("csv_sha256"), actual_sha):
            raise RuntimeError("score_job_csv_sha256_mismatch")

        result_dir = resolver.session_scoring_path(session_id)
        score_result = _read_json(result_dir / "full_result.json")
        if not isinstance(score_result, dict):
            raise RuntimeError("score_job_full_result_missing")
        if str(score_result.get("session_id") or "") != session_id:
            raise RuntimeError("score_result_session_id_mismatch")
        pc_job_id = str(score_result.get("pc_job_id") or "")
        if pc_job_id and pc_job_id != session_id:
            raise RuntimeError("score_result_pc_job_id_mismatch")
        task_registry = score_result.get("task_registry") if isinstance(score_result.get("task_registry"), dict) else {}
        result_profile = str(task_registry.get("score_profile") or summary.get("score_profile") or "")
        if result_profile not in {"drink_water", "lateral_transfer"}:
            raise RuntimeError(f"score_profile_invalid:{result_profile or 'missing'}")
        expected_profile = self._score_profile_for_summary(summary)
        if result_profile != expected_profile:
            raise RuntimeError("score_profile_mismatch")

        dedicated_dir = result_dir / "intermediate" / "dedicated_scoring" / session_id
        session_summary = _read_json(dedicated_dir / "session_summary.json")
        if not isinstance(session_summary, dict):
            raise RuntimeError("dedicated_session_summary_missing")
        if str(session_summary.get("session_id") or "") != session_id:
            raise RuntimeError("dedicated_session_id_mismatch")
        motion_family = str(session_summary.get("motion_family") or "")
        if motion_family and motion_family != expected_profile:
            raise RuntimeError("dedicated_motion_family_mismatch")
        if not _sha256_equal(session_summary.get("source_sha256"), actual_sha):
            raise RuntimeError("dedicated_source_sha256_mismatch")
        session_manifest = _read_json(dedicated_dir / "session_manifest.json") or {}
        if session_manifest.get("source_sha256") and not _sha256_equal(session_manifest.get("source_sha256"), actual_sha):
            raise RuntimeError("dedicated_session_manifest_sha256_mismatch")
        return {
            "session_id": session_id,
            "runtime_session_dir": runtime_session_dir,
            "dedicated_dir": dedicated_dir,
            "csv_path": csv_path,
            "source_sha256": actual_sha,
            "job_status": job_status,
            "score_result": score_result,
            "session_summary": session_summary,
            "session_manifest": session_manifest,
            "score_profile": expected_profile,
        }

    def _write_adopted_status(
        self,
        session_id: str,
        status: str,
        summary: dict[str, Any],
        source_sha: str | None,
        score_result: dict[str, Any],
        session_summary: dict[str, Any],
        error: str | None = None,
    ) -> None:
        previous = self.status(session_id)
        _write_json_atomic(
            _status_path(session_id),
            {
                "session_id": session_id,
                "status": status,
                "source_mode": summary.get("source_mode"),
                "source_origin": summary.get("source_origin"),
                "source_csv": str(summary.get("csv_relative_path") or ""),
                "source_sha256": source_sha,
                "source_integrity_status": summary.get("source_integrity_status") or "verified",
                "started_at": previous.get("started_at") or (None if status == "not_started" else _now_iso()),
                "completed_at": _now_iso() if status in {"completed", "failed"} else None,
                "score_version": score_result.get("score_version"),
                "model_version": score_result.get("model_version"),
                "session_version": session_summary.get("session_version"),
                "template_path": session_summary.get("template_path"),
                "template_sha256": session_summary.get("template_sha256"),
                "scoring_authority": SCORE_JOB_SCORING_AUTHORITY,
                "artifact_origin": SCORE_JOB_ARTIFACT_ORIGIN,
                "operation": "adopt_score_job_artifacts",
                "rescore_allowed": False,
                "error": error,
            },
        )

    @staticmethod
    def _is_adopted_scoring(scoring_dir: Path, source_sha: str | None = None) -> bool:
        return bool(
            (scoring_dir / "full_result.json").is_file()
            and (scoring_dir / "intermediate" / "dedicated_scoring").is_dir()
        )

    @staticmethod
    def _source_sha_from_summary(summary: dict[str, Any]) -> str:
        csv_path = SessionV12ScoringService._source_csv_from_summary(summary)
        return _sha256_file(csv_path)

    @staticmethod
    def _source_csv_from_summary(summary: dict[str, Any]) -> Path:
        session_id = str(summary.get("session_id") or "")
        csv_file = str(summary.get("csv_file") or "")
        if not csv_file:
            relative = str(summary.get("csv_relative_path") or "").replace("\\", "/")
            csv_file = relative.rsplit("/", 1)[-1]
        path = _score_path_resolver().session_input_file_path(session_id, csv_file).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"source_csv_not_found:{path}")
        return path

    @staticmethod
    def _assert_formal_baseline_ready() -> None:
        precheck = SessionV12ScoringService.formal_baseline_precheck()
        if not precheck["ok"]:
            raise RuntimeError(f"formal_task12_baseline_precheck_failed:{precheck}")

    @staticmethod
    def _score_profile_for_summary(summary: dict[str, Any]) -> str:
        status = str(summary.get("task_registry_status") or "").strip()
        profile = str(summary.get("score_profile") or "").strip()
        if status == "unknown":
            raise RuntimeError("UNKNOWN_TASK")
        if status == "resolved":
            if profile not in {"drink_water", "lateral_transfer"}:
                raise RuntimeError(f"resolved_task_score_profile_invalid:{profile or 'missing'}")
            return profile
        if profile in {"drink_water", "lateral_transfer"}:
            return profile
        legacy_profile = str(legacy_task_fields(summary).get("score_profile") or "").strip()
        if legacy_profile in {"drink_water", "lateral_transfer"}:
            return legacy_profile
        raise RuntimeError("UNKNOWN_TASK")

    @staticmethod
    def _assert_scoring_profile_ready(summary: dict[str, Any]) -> None:
        profile = SessionV12ScoringService._score_profile_for_summary(summary)
        if profile == "lateral_transfer":
            if not LATERAL_TEMPLATE_PATH.is_file():
                raise RuntimeError("lateral_transfer_template_missing")
            template = _read_json(LATERAL_TEMPLATE_PATH)
            if template is None or template.get("template_version") != EXPECTED_LATERAL_BASELINE_VERSION:
                raise RuntimeError("lateral_transfer_template_version_mismatch")
            return
        if profile == "drink_water":
            SessionV12ScoringService._assert_formal_baseline_ready()
            return
        raise RuntimeError(f"unsupported_score_profile:{profile}")

    @staticmethod
    def _validate_source_csv(path: Path) -> None:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration as exc:
                raise ValueError("csv_empty") from exc
            if len(header) != 86:
                raise ValueError(f"csv_header_contract_invalid:expected_86_actual_{len(header)}")
            required = {"log_seq", "time_ms"}
            missing = sorted(required.difference(header))
            if missing:
                raise ValueError(f"csv_required_columns_missing:{','.join(missing)}")

    @staticmethod
    def _run_tool_process(
        session_id: str, source_csv: Path, output_root: Path, summary: dict[str, Any] | None = None
    ) -> subprocess.CompletedProcess[str]:
        profile = SessionV12ScoringService._score_profile_for_summary(summary or {})
        if profile == "lateral_transfer":
            command = [
                str(SESSION_SCORING_PYTHON),
                str(LATERAL_SESSION_SCORING_TOOL_PATH),
                "--source-session",
                str(source_csv),
                "--template",
                str(LATERAL_TEMPLATE_PATH),
                "--output-root",
                str(output_root),
                "--session-id",
                session_id,
            ]
            cwd = LATERAL_SESSION_SCORING_TOOL_PATH.parents[1]
        elif profile == "drink_water":
            command = [
                str(SESSION_SCORING_PYTHON),
                str(SESSION_SCORING_TOOL_PATH),
                "--source-session",
                str(source_csv),
                "--baseline-template",
                str(BASELINE_TEMPLATE_PATH),
                "--output-root",
                str(output_root),
                "--session-id",
                session_id,
            ]
            cwd = SESSION_SCORING_TOOL_PATH.parents[1]
        else:
            raise RuntimeError(f"unsupported_score_profile:{profile}")
        return subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=False,
        )

    @staticmethod
    def _has_completed_scoring(scoring_dir: Path, source_sha: str) -> bool:
        summary = _read_json(scoring_dir / "session_summary.json")
        manifest = _read_json(scoring_dir / "session_v12_source_manifest.json")
        return bool(
            isinstance(summary, dict)
            and isinstance(manifest, dict)
            and _sha256_equal(manifest.get("source_sha256"), source_sha)
        )

    @staticmethod
    def _source_csv_for_stored_session(session_id: str, session: dict[str, Any]) -> Path:
        csv_file = str(session.get("csv_file") or "")
        if not csv_file:
            relative = str(session.get("csv_relative_path") or "").replace("\\", "/")
            csv_file = relative.rsplit("/", 1)[-1]
        path = _score_path_resolver().session_input_file_path(session_id, csv_file).resolve()
        if not path.is_file():
            raise FileNotFoundError("raw_session_csv_not_found")
        return path

    @staticmethod
    def _historical_source_sha(session_dir: Path, session: dict[str, Any]) -> str | None:
        sources = [
            _read_json(session_dir / "scoring" / "session_v12_source_manifest.json"),
            _read_json(session_dir / SESSION_SCORE_STATUS_FILE),
            session,
        ]
        keys = (
            "source_sha256",
            "raw_csv_sha256",
            "actual_raw_csv_sha256",
            "csv_sha256",
        )
        for source in sources:
            if not isinstance(source, dict):
                continue
            for key in keys:
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    @staticmethod
    def _relative_source_for_status(source_csv: Path) -> str:
        try:
            return str(
                source_csv.resolve().relative_to(
                    _score_path_resolver().get_score_root()
                )
            )
        except ValueError:
            return str(source_csv)

    @staticmethod
    def _write_source_manifest(
        scoring_dir: Path, source_csv: Path, source_sha: str, summary: dict[str, Any]
    ) -> None:
        _write_json_atomic(
            scoring_dir / "session_v12_source_manifest.json",
            {
                "schema_version": "doctor_session_v12_source_manifest_v1",
                "session_id": summary.get("session_id"),
                "source_mode": summary.get("source_mode"),
                "source_origin": summary.get("source_origin"),
                "source_csv": str(source_csv),
                "source_sha256": source_sha,
                "source_integrity_status": summary.get("source_integrity_status") or "verified",
                "original_task_name": summary.get("original_task_name") or summary.get("internal_score_task_name"),
                "task_name": summary.get("task_name") or summary.get("task"),
                "task_registry_version": summary.get("task_registry_version"),
                "task_registry_status": summary.get("task_registry_status"),
                "motion_family": summary.get("motion_family"),
                "object_type": summary.get("object_type"),
                "score_profile": summary.get("score_profile"),
                "twin_profile": summary.get("twin_profile"),
                "task_level": summary.get("task_level"),
                "baseline_template": str(BASELINE_TEMPLATE_PATH),
                "score_version": EXPECTED_SCORE_VERSION,
                "baseline_version": EXPECTED_BASELINE_VERSION,
                "formal_template_sha256": _current_formal_template_sha256(),
                "segmentation_version": EXPECTED_SEGMENTATION_VERSION,
                "sensor_mapping_version": EXPECTED_SENSOR_MAPPING_VERSION,
                "detector_rule_version": EXPECTED_DETECTOR_RULE_VERSION,
                "scored_at": _now_iso(),
                "generated_at": _now_iso(),
            },
        )

    def _write_status(
        self,
        session_id: str,
        status: str,
        summary: dict[str, Any],
        source_sha: str | None,
        error: str | None = None,
        operation: str = "score",
    ) -> None:
        previous = self.status(session_id)
        value = {
            "session_id": session_id,
            "status": status,
            "source_mode": summary.get("source_mode"),
            "source_origin": summary.get("source_origin"),
            "source_csv": str(summary.get("csv_relative_path") or ""),
            "source_sha256": source_sha,
            "source_integrity_status": summary.get("source_integrity_status") or "verified",
            "started_at": previous.get("started_at") or (None if status == "not_started" else _now_iso()),
            "completed_at": _now_iso() if status in {"completed", "failed"} else None,
            "score_version": EXPECTED_SCORE_VERSION,
            "baseline_version": EXPECTED_BASELINE_VERSION,
            "formal_template_sha256": _current_formal_template_sha256(),
            "sensor_mapping_version": EXPECTED_SENSOR_MAPPING_VERSION,
            "detector_rule_version": EXPECTED_DETECTOR_RULE_VERSION,
            "operation": operation,
            "error": error,
        }
        _write_json_atomic(_status_path(session_id), value)
        self._drop_session_detail_cache(session_id)

    def _read_session_scoring(self, session_id: str) -> dict[str, Any] | None:
        scoring_dir = device_history_store.session_directory(session_id) / "scoring"
        dedicated_dir = (
            scoring_dir
            / "intermediate"
            / "dedicated_scoring"
            / session_id
        )
        summary = _read_json(dedicated_dir / "session_summary.json")
        if not isinstance(summary, dict):
            return None
        score_result = _read_json(scoring_dir / "full_result.json")
        status = self.status(session_id)
        manifest = {
            "scoring_authority": SCORE_JOB_SCORING_AUTHORITY,
            "artifact_origin": SCORE_JOB_ARTIFACT_ORIGIN,
            "source_sha256": status.get("source_sha256"),
        }
        return {
            "dir": dedicated_dir,
            "summary": summary,
            "score_result": score_result if isinstance(score_result, dict) else {},
            "manifest": manifest,
        }

    def _pending_detail(self, session: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": session.get("session_id"),
            **self._task_metadata_for_detail(session),
            "scoring_status": status.get("status", "not_started"),
            "source_mode": session.get("source_mode"),
            "source_origin": session.get("source_origin"),
            "started_at": session.get("started_at"),
            "ended_at": session.get("completed_at"),
            "duration_ms": session.get("duration_ms"),
            "baseline_version": status.get("baseline_version"),
            "scoring_freshness": self._scoring_freshness(session.get("session_id"), None, status),
            "repetition_count": 0,
            "valid_repetition_count": 0,
            "rejected_repetition_count": 0,
            "statistics": self._empty_statistics(),
            "repetitions": [],
            "error": status.get("error"),
        }

    def _completed_detail(
        self, session: dict[str, Any], scoring: dict[str, Any], status: dict[str, Any]
    ) -> dict[str, Any]:
        scoring_dir = scoring["dir"]
        summary = scoring["summary"]
        score_result = scoring.get("score_result") if isinstance(scoring.get("score_result"), dict) else {}
        manifest = scoring.get("manifest") if isinstance(scoring.get("manifest"), dict) else {}
        data_quality = score_result.get("data_quality") if isinstance(score_result.get("data_quality"), dict) else {}
        warnings = data_quality.get("warnings") if isinstance(data_quality.get("warnings"), list) else []
        repetitions = self._read_repetitions(scoring_dir, str(session.get("session_id") or ""))
        valid_repetitions = [rep for rep in repetitions if rep.get("valid") is True]
        scores = [rep["final_score"] for rep in valid_repetitions if isinstance(rep.get("final_score"), (int, float))]
        subscore_avgs = self._subscore_averages_from_repetitions(valid_repetitions)
        rejected_count = len([rep for rep in repetitions if rep.get("valid") is not True])
        return {
            "session_id": session.get("session_id"),
            **self._task_metadata_for_detail(session),
            "scoring_status": status.get("status", "completed"),
            "source_mode": session.get("source_mode"),
            "source_origin": session.get("source_origin"),
            "started_at": session.get("started_at"),
            "ended_at": session.get("completed_at"),
            "duration_ms": session.get("duration_ms"),
            "baseline_version": status.get("baseline_version") or status.get("session_version") or summary.get("baseline_version") or summary.get("session_version"),
            "scoring_freshness": self._scoring_freshness(session.get("session_id"), scoring_dir, status),
            "repetition_count": len(repetitions),
            "valid_repetition_count": len(valid_repetitions),
            "rejected_repetition_count": rejected_count,
            "statistics": {
                "average_score": round(sum(scores) / len(scores), 2) if scores else None,
                "maximum_score": max(scores) if scores else None,
                "minimum_score": min(scores) if scores else None,
                "subscore_averages": {
                    key: subscore_avgs.get(key)
                    for key in ("speed_score", "ROM_score", "smoothness_score", "coordination_score", "muscle_score")
                },
            },
            "repetitions": repetitions,
            "error": status.get("error"),
            "valid": score_result.get("valid") if "valid" in score_result else (None if scores else False),
            "invalid_reason": score_result.get("invalid_reason"),
            "data_quality_level": data_quality.get("level"),
            "warnings": [str(item) for item in warnings if isinstance(item, str)],
            "score_version": score_result.get("score_version") or status.get("score_version") or summary.get("session_version"),
            "model_version": score_result.get("model_version") or status.get("model_version") or summary.get("session_version"),
            "scoring_authority": manifest.get("scoring_authority"),
            "artifact_origin": manifest.get("artifact_origin"),
            "rescore_allowed": manifest.get("scoring_authority") != SCORE_JOB_SCORING_AUTHORITY,
        }

    def _scoring_freshness(self, session_id: Any, scoring_dir: Path | None, status: dict[str, Any]) -> dict[str, Any]:
        stored_versions: dict[str, Any] = {}
        manifest: dict[str, Any] = {}
        if scoring_dir is not None:
            manifest = _read_json(scoring_dir / "session_v12_source_manifest.json") or {}
            if not manifest and status.get("scoring_authority") == SCORE_JOB_SCORING_AUTHORITY:
                manifest = {
                    "scoring_authority": SCORE_JOB_SCORING_AUTHORITY,
                    "artifact_origin": status.get("artifact_origin"),
                    "source_sha256": status.get("source_sha256"),
                }
            if manifest.get("scoring_authority") == SCORE_JOB_SCORING_AUTHORITY:
                source_sha = manifest.get("source_sha256") or status.get("source_sha256")
                current = self._current_freshness_versions(str(session_id or ""), source_sha)
                reasons = []
                if source_sha in {None, ""}:
                    reasons.append("missing_source_sha256")
                elif not _sha256_equal(source_sha, current.get("source_sha256")):
                    reasons.append("mismatch_source_sha256")
                return {
                    "status": "current" if not reasons else "stale",
                    "reasons": reasons,
                    "stored_versions": {
                        "source_sha256": source_sha,
                        "scoring_authority": manifest.get("scoring_authority"),
                        "artifact_origin": manifest.get("artifact_origin"),
                        "score_version": manifest.get("score_version"),
                        "model_version": manifest.get("model_version"),
                        "session_version": manifest.get("session_version"),
                        "template_sha256": manifest.get("template_sha256"),
                    },
                    "current_versions": {
                        "source_sha256": current.get("source_sha256"),
                        "scoring_authority": SCORE_JOB_SCORING_AUTHORITY,
                        "artifact_origin": SCORE_JOB_ARTIFACT_ORIGIN,
                    },
                }
            stored_versions.update({key: manifest.get(key) for key in FRESHNESS_KEYS if key in manifest})
        stored_versions.update(
            {key: status.get(key) for key in FRESHNESS_KEYS if status.get(key) is not None and key not in stored_versions}
        )
        current_versions = self._current_freshness_versions(str(session_id or ""), stored_versions.get("source_sha256"))
        missing = [key for key in FRESHNESS_KEYS if stored_versions.get(key) in {None, ""}]
        if missing:
            return {
                "status": "unknown",
                "reasons": [f"missing_{key}" for key in missing],
                "stored_versions": stored_versions,
                "current_versions": current_versions,
            }
        sha_keys = {"source_sha256", "formal_template_sha256"}
        mismatches = []
        for key in FRESHNESS_KEYS:
            stored = stored_versions.get(key)
            current = current_versions.get(key)
            if key in sha_keys:
                if not _sha256_equal(stored, current):
                    mismatches.append(key)
            elif stored != current:
                mismatches.append(key)
        return {
            "status": "stale" if mismatches else "current",
            "reasons": [f"mismatch_{key}" for key in mismatches],
            "stored_versions": stored_versions,
            "current_versions": current_versions,
        }

    def _current_freshness_versions(self, session_id: str, fallback_source_sha: Any = None) -> dict[str, Any]:
        source_sha = fallback_source_sha
        try:
            session = _read_json(device_history_store.session_directory(session_id) / "session.json") or {}
            source_sha = _sha256_file(self._source_csv_for_stored_session(session_id, session))
        except Exception:
            pass
        return {
            "source_sha256": source_sha,
            "score_version": EXPECTED_SCORE_VERSION,
            "baseline_version": EXPECTED_BASELINE_VERSION,
            "formal_template_sha256": _current_formal_template_sha256(),
            "sensor_mapping_version": EXPECTED_SENSOR_MAPPING_VERSION,
            "detector_rule_version": EXPECTED_DETECTOR_RULE_VERSION,
        }

    @staticmethod
    def _empty_statistics() -> dict[str, Any]:
        return {
            "average_score": None,
            "maximum_score": None,
            "minimum_score": None,
            "subscore_averages": {
                "speed_score": None,
                "ROM_score": None,
                "smoothness_score": None,
                "coordination_score": None,
                "muscle_score": None,
            },
        }

    @staticmethod
    def _subscore_averages_from_repetitions(repetitions: list[dict[str, Any]]) -> dict[str, float | None]:
        result: dict[str, float | None] = {}
        for key in ("speed_score", "ROM_score", "smoothness_score", "coordination_score", "muscle_score"):
            values = [
                float(rep.get("subscores", {}).get(key))
                for rep in repetitions
                if isinstance(rep.get("subscores"), dict)
                and _finite_number(rep.get("subscores", {}).get(key)) is not None
            ]
            result[key] = round(sum(values) / len(values), 2) if values else None
        return result

    def _report_from_detail(
        self, detail: dict[str, Any], legacy_report: dict[str, Any], session: dict[str, Any]
    ) -> dict[str, Any]:
        report = deepcopy(legacy_report) if isinstance(legacy_report, dict) else {}
        report.setdefault("title", "上肢康复训练评估报告")
        patient = report.get("patient") if isinstance(report.get("patient"), dict) else {}
        completion = report.get("completion") if isinstance(report.get("completion"), dict) else {}
        score = report.get("score") if isinstance(report.get("score"), dict) else {}
        stats = detail.get("statistics") if isinstance(detail.get("statistics"), dict) else {}
        repetitions = detail.get("repetitions") if isinstance(detail.get("repetitions"), list) else []
        average = _finite_number(stats.get("average_score"))
        compensation = self._aggregate_compensation(repetitions)
        feedback = self._aggregate_feedback(repetitions)
        task_name = detail.get("task_name") or session.get("task_name") or patient.get("task") or ""
        patient.update(
            {
                "patient_id": PATIENT_ID,
                "task": task_name,
                "session_id": detail.get("session_id"),
            }
        )
        if session.get("started_at") and not patient.get("training_time"):
            patient["training_time"] = session.get("started_at")
        completion.update(
            {
                "planned_reps": detail.get("repetition_count"),
                "completed_reps": detail.get("repetition_count"),
                "valid_reps": detail.get("valid_repetition_count"),
                "duration_sec": self._duration_seconds(detail, session, completion),
                "fully_completed": True,
                "end_reason": session.get("end_reason") or completion.get("end_reason"),
            }
        )
        score.update(
            {
                "session_id": detail.get("session_id"),
                "score_version": score.get("score_version") or "session_v12_authoritative",
                "valid": average is not None,
                "final_score": round(average, 2) if average is not None else None,
                "subscores": stats.get("subscore_averages") if isinstance(stats.get("subscore_averages"), dict) else {},
                "compensation": compensation,
                "feedback": feedback,
                "action_summaries": self._action_summaries_from_repetitions(repetitions),
            }
        )
        report["patient"] = patient
        report["completion"] = completion
        report["score"] = score
        report["action_summaries"] = score["action_summaries"]
        report["rep_details"] = self._rep_details_from_repetitions(repetitions)
        report["summary"] = feedback[0] if feedback else report.get("summary") or ""
        versions = report.get("versions") if isinstance(report.get("versions"), dict) else {}
        versions.update(
            {
                "source_mode": session.get("source_mode") or detail.get("source_mode"),
                "source_origin": session.get("source_origin") or detail.get("source_origin"),
                "score_version": score["score_version"],
                "schema_version": versions.get("schema_version") or "doctor_session_v12_authoritative_report_v1",
            }
        )
        report["versions"] = versions
        return report

    @staticmethod
    def _duration_seconds(detail: dict[str, Any], session: dict[str, Any], completion: dict[str, Any]) -> float | None:
        for value in (detail.get("duration_ms"), session.get("duration_ms")):
            parsed = _finite_number(value)
            if parsed is not None:
                return round(parsed / 1000.0, 3)
        return _finite_number(completion.get("duration_sec"))

    @staticmethod
    def _aggregate_compensation(repetitions: list[Any]) -> dict[str, Any]:
        types: list[str] = []
        for rep in repetitions:
            if not isinstance(rep, dict):
                continue
            compensation = rep.get("compensation") if isinstance(rep.get("compensation"), dict) else {}
            raw_types = compensation.get("types") if isinstance(compensation, dict) else []
            if isinstance(raw_types, list):
                for item in raw_types:
                    text = str(item)
                    if text and text not in types:
                        types.append(text)
        return {
            "detected": bool(types),
            "types": types,
            "labels": [COMPENSATION_LABELS.get(code, f"未知代偿类型：{code}") for code in types],
            "source": "authoritative_session_v12_per_rep_scores",
        }

    @staticmethod
    def _aggregate_feedback(repetitions: list[Any]) -> list[str]:
        result: list[str] = []
        for rep in repetitions:
            if not isinstance(rep, dict):
                continue
            feedback = rep.get("feedback")
            if not isinstance(feedback, list):
                continue
            for item in feedback:
                text = str(item).strip()
                if text and text not in result:
                    result.append(text)
        return result

    @staticmethod
    def _action_summaries_from_repetitions(repetitions: list[Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for rep in repetitions:
            if not isinstance(rep, dict):
                continue
            result.append(
                {
                    "rep_index": rep.get("rep_index"),
                    "valid": rep.get("valid"),
                    "final_score": rep.get("final_score"),
                    "subscores": rep.get("subscores") if isinstance(rep.get("subscores"), dict) else {},
                    "compensation": rep.get("compensation") if isinstance(rep.get("compensation"), dict) else {},
                    "feedback": rep.get("feedback") if isinstance(rep.get("feedback"), list) else [],
                    "invalid_reason": rep.get("invalid_reason"),
                }
            )
        return result

    @staticmethod
    def _rep_details_from_repetitions(repetitions: list[Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for rep in repetitions:
            if not isinstance(rep, dict):
                continue
            subscores = rep.get("subscores") if isinstance(rep.get("subscores"), dict) else {}
            compensation = rep.get("compensation") if isinstance(rep.get("compensation"), dict) else {}
            result.append(
                {
                    "rep_index": rep.get("rep_index"),
                    "valid": rep.get("valid"),
                    "final_score": rep.get("final_score"),
                    "ROM": subscores.get("ROM_score"),
                    "smoothness": subscores.get("smoothness_score"),
                    "coordination": subscores.get("coordination_score"),
                    "muscle_performance": subscores.get("muscle_score"),
                    "fatigue": {"level": "not_summarized", "trend": "not_summarized"},
                    "compensation": {
                        "risk": "detected" if compensation.get("detected") else "not_detected",
                        "status": "detected" if compensation.get("detected") else "not_detected",
                    },
                }
            )
        return result

    @staticmethod
    def _latest_lateral_formal_score_dir() -> Path | None:
        if not LATERAL_FORMAL_SCORE_ROOT.is_dir():
            return None
        candidates = [
            path
            for path in LATERAL_FORMAL_SCORE_ROOT.iterdir()
            if path.is_dir() and path.name.startswith(LATERAL_FORMAL_SCORE_DIR_PREFIX)
        ]
        return sorted(candidates, key=lambda path: path.name)[-1] if candidates else None

    @classmethod
    def _current_lateral_formal_score(
        cls, session_id: str, rep_index: int
    ) -> tuple[dict[str, Any] | None, Path | None]:
        root = cls._latest_lateral_formal_score_dir()
        if root is None or not session_id:
            return None, None
        score_path = root / session_id / "per_rep_scores" / f"rep_{rep_index:02d}_score.json"
        try:
            score_path.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            return None, None
        score = _read_json(score_path)
        return (score, score_path) if isinstance(score, dict) else (None, None)

    @staticmethod
    def _compensation_payload_from_score(
        score: dict[str, Any], source: str, source_path: Path | None = None
    ) -> dict[str, Any]:
        compensation = score.get("compensation") if isinstance(score.get("compensation"), dict) else {}
        raw_types = compensation.get("types", []) if isinstance(compensation, dict) else []
        types: list[str] = []
        if isinstance(raw_types, list):
            for item in raw_types:
                text = str(item)
                if text and text not in types:
                    types.append(text)
        detected = bool(compensation.get("detected")) if isinstance(compensation, dict) else bool(types)
        payload: dict[str, Any] = {
            "detected": detected,
            "types": types,
            "labels": [COMPENSATION_LABELS.get(code, f"未知代偿类型：{code}") for code in types],
            "source": source,
        }
        if source_path is not None:
            payload["source_path"] = str(source_path)
        return payload

    @staticmethod
    def _task_metadata_for_detail(session: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_name": session.get("task_name") or session.get("task") or session.get("original_task_name"),
            "original_task_name": session.get("original_task_name") or session.get("internal_score_task_name"),
            "task_registry_version": session.get("task_registry_version"),
            "task_registry_status": session.get("task_registry_status"),
            "unsupported_task_reason": session.get("unsupported_task_reason"),
            "motion_family": session.get("motion_family"),
            "object_type": session.get("object_type"),
            "score_profile": session.get("score_profile"),
            "twin_profile": session.get("twin_profile"),
            "task_level": session.get("task_level"),
        }

    def _read_repetitions(self, scoring_dir: Path, session_id: str = "") -> list[dict[str, Any]]:
        score_dir = scoring_dir / "per_rep_scores"
        rep_dir = scoring_dir / "repetitions"
        result: list[dict[str, Any]] = []
        for score_path in sorted(score_dir.glob("rep_*_score.json")):
            rep_index = int(score_path.stem.split("_")[1])
            score = _read_json(score_path) or {}
            metadata = _read_json(rep_dir / f"rep_{rep_index:02d}.metadata.json") or {}
            trajectory = _read_json(
                device_history_store.session_directory(session_id)
                / "doctor"
                / "trajectory"
                / f"rep_{rep_index:02d}_trajectory.json"
            )
            playback_available = bool(isinstance(trajectory, dict) and trajectory.get("playback_available") is True)
            playback_error = (
                trajectory.get("playback_error")
                if isinstance(trajectory, dict)
                else "trajectory_not_found"
            )
            current_score, current_score_path = self._current_lateral_formal_score(session_id, rep_index)
            compensation_score = current_score if current_score is not None else score
            compensation_source = (
                LATERAL_COMPENSATION_SOURCE_CURRENT
                if current_score is not None
                else LATERAL_COMPENSATION_SOURCE_HISTORICAL
            )
            compensation = self._compensation_payload_from_score(
                compensation_score,
                compensation_source,
                current_score_path,
            )
            result.append(
                {
                    "rep_index": rep_index,
                    "valid": bool(score.get("valid")),
                    "start_time_ms": metadata.get("start_time_ms"),
                    "end_time_ms": metadata.get("end_time_ms"),
                    "duration_ms": metadata.get("duration_ms"),
                    "final_score": score.get("final_score") if score.get("valid") else None,
                    "subscores": score.get("subscores") if isinstance(score.get("subscores"), dict) else {},
                    "compensation": compensation,
                    "feedback": score.get("feedback") if isinstance(score.get("feedback"), list) else [],
                    "playback_available": playback_available,
                    "playback_error": playback_error,
                    "invalid_reason": score.get("invalid_reason") or metadata.get("invalid_reason"),
                }
            )
        return result

    def _generate_trajectories(
        self,
        session_id: str,
        scoring_dir: Path,
        trajectories_dir: Path | None = None,
    ) -> None:
        trajectories_dir = trajectories_dir or (scoring_dir / "trajectories")
        trajectories_dir.mkdir(parents=True, exist_ok=True)
        for rep_csv in sorted((scoring_dir / "repetitions").glob("rep_*.csv")):
            rep_index = int(rep_csv.stem.split("_")[1])
            trajectory = self._trajectory_from_rep_csv(session_id, rep_index, rep_csv)
            _write_json_atomic(trajectories_dir / f"rep_{rep_index:02d}_trajectory.json", trajectory)

    def _trajectory_from_rep_csv(self, session_id: str, rep_index: int, rep_csv: Path) -> dict[str, Any]:
        try:
            with rep_csv.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                header = reader.fieldnames or []
                missing = [field for field in ("time_ms", "log_seq") if field not in header]
                if missing:
                    return self._trajectory_error(session_id, rep_index, f"missing_required_columns:{','.join(missing)}")
                orientation_format = self._orientation_format(header)
                if orientation_format == "missing":
                    return self._trajectory_error(session_id, rep_index, "missing_imu_orientation_columns")
                frames = []
                first_time = None
                last_time = None
                for line_number, row in enumerate(reader, start=2):
                    original_time = self._require_finite_row_value(row, "time_ms", line_number)
                    self._require_finite_row_value(row, "log_seq", line_number)
                    missing_pose = self._missing_pose_fields(row, orientation_format, line_number)
                    if missing_pose:
                        return self._trajectory_error(session_id, rep_index, missing_pose)
                    if first_time is None:
                        first_time = original_time
                    last_time = original_time
                    frames.append(self._trajectory_frame(row, original_time, orientation_format))
        except (OSError, UnicodeError, ValueError) as exc:
            return self._trajectory_error(session_id, rep_index, str(exc))
        if not frames:
            return self._trajectory_error(session_id, rep_index, "no_valid_trajectory_frames")
        return {
            "schema_version": "doctor_real_trajectory_v1",
            "session_id": session_id,
            "rep_index": rep_index,
            "sensor_mapping_version": EXPECTED_SENSOR_MAPPING_VERSION,
            "time_base": "original_timestamp",
            "playback_available": True,
            "playback_error": None,
            "frame_count": len(frames),
            "duration_ms": None if first_time is None or last_time is None else int(last_time - first_time),
            "orientation_format": orientation_format,
            "rotation_order": "xyz" if orientation_format == "euler" else None,
            "frames": frames,
        }

    @staticmethod
    def _require_finite_row_value(row: dict[str, str], field: str, line_number: int) -> float:
        value = _finite_number(row.get(field))
        if value is None:
            raise ValueError(f"invalid_trajectory_value:line_{line_number}:{field}")
        return value

    def _missing_pose_fields(self, row: dict[str, str], orientation_format: str, line_number: int) -> str | None:
        fields = ("qw", "qx", "qy", "qz") if orientation_format == "quaternion" else ("roll", "pitch", "yaw")
        for idx in range(1, 5):
            for field in fields:
                name = f"imu{idx}_{field}"
                if _finite_number(row.get(name)) is None:
                    return f"invalid_trajectory_pose:line_{line_number}:{name}"
        return None

    @staticmethod
    def _orientation_format(header: list[str]) -> str:
        for idx in range(1, 5):
            quat = {f"imu{idx}_qw", f"imu{idx}_qx", f"imu{idx}_qy", f"imu{idx}_qz"}
            if not quat.issubset(header):
                break
        else:
            return "quaternion"
        for idx in range(1, 5):
            euler = {f"imu{idx}_roll", f"imu{idx}_pitch", f"imu{idx}_yaw"}
            if not euler.issubset(header):
                return "missing"
        return "euler"

    def _trajectory_frame(self, row: dict[str, str], original_time: float, orientation_format: str) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "time_ms": original_time,
            "original_time_ms": original_time,
            "log_seq": int(self._require_finite_row_value(row, "log_seq", 0)),
        }
        for idx in range(1, 5):
            key = f"imu{idx}"
            if orientation_format == "quaternion":
                frame[key] = {
                    "sensor_id": IMU_MAP[key],
                    "qw": _finite_number(row.get(f"{key}_qw")),
                    "qx": _finite_number(row.get(f"{key}_qx")),
                    "qy": _finite_number(row.get(f"{key}_qy")),
                    "qz": _finite_number(row.get(f"{key}_qz")),
                }
            else:
                frame[key] = {
                    "sensor_id": IMU_MAP[key],
                    "roll": _finite_number(row.get(f"{key}_roll")),
                    "pitch": _finite_number(row.get(f"{key}_pitch")),
                    "yaw": _finite_number(row.get(f"{key}_yaw")),
                    "gx": _finite_number(row.get(f"{key}_gx")),
                    "gy": _finite_number(row.get(f"{key}_gy")),
                    "gz": _finite_number(row.get(f"{key}_gz")),
                }
        for idx in range(1, 5):
            frame[f"emg{idx}"] = {
                "channel": EMG_MAP[f"emg{idx}"],
                "latest": _finite_number(row.get(f"emg_latest_{idx}")),
                "rms": _finite_number(row.get(f"emg_rms_{idx}")),
                "mav": _finite_number(row.get(f"emg_mav_{idx}")),
                "peak": _finite_number(row.get(f"emg_peak_{idx}")),
            }
        return frame

    @staticmethod
    def _trajectory_error(session_id: str, rep_index: int, reason: str) -> dict[str, Any]:
        return {
            "schema_version": "doctor_real_trajectory_v1",
            "session_id": session_id,
            "rep_index": rep_index,
            "sensor_mapping_version": EXPECTED_SENSOR_MAPPING_VERSION,
            "time_base": "original_timestamp",
            "playback_available": False,
            "playback_error": reason,
            "frame_count": 0,
            "duration_ms": 0,
            "orientation_format": "unavailable",
            "frames": [],
        }


session_v12_scoring_service = SessionV12ScoringService()
