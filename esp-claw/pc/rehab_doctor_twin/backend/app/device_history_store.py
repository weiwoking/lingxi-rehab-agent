from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import PATIENT_ID, TRAINING_DATA_RESOLVER

SESSION_FIELDS = (
    "patient_id",
    "session_id",
    "scenario_id",
    "task",
    "task_id",
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
    "target_reps",
    "completed_reps",
    "status",
    "started_at_ms",
    "ended_at_ms",
    "started_at",
    "completed_at",
    "duration_ms",
    "duration_seconds",
    "rows_written",
    "final_score",
    "report_ready",
    "source_mode",
    "source_origin",
    "input_row_count",
    "valid_frame_count",
    "invalid_frame_count",
    "scoring_status",
    "score_valid",
    "log_drop_count",
    "valid_row_count",
    "invalid_row_count",
    "duplicate_row_count",
    "score_status",
    "internal_score_task_name",
    "source_ip",
    "report_path",
    "score_result_path",
    "score_job_result_path",
    "error",
    "csv_file",
    "csv_relative_path",
    "end_reason",
    "formal",
    "record_kind",
    "task_finalized",
    "training_status",
    "score_status",
    "score_job_id",
    "auto_score_submission_state",
    "auto_score_last_error",
    "auto_score_retry_count",
    "auto_score_next_retry_at_ms",
    "session_v12_status",
    "session_v12_error",
    "session_v12_baseline_version",
    "final_score",
    "score_error_code",
    "score_error_message",
    "created_at",
    "updated_at",
    "start_log_seq",
    "end_log_seq",
)


class DeviceHistoryStore:
    def __init__(self) -> None:
        self._sessions_dir = TRAINING_DATA_RESOLVER.doctor_sessions_root(PATIENT_ID)

    def load(
        self,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        sessions: dict[str, dict[str, Any]] = {}
        reports: dict[str, dict[str, Any]] = {}
        if not self._sessions_dir.is_dir():
            return sessions, reports

        for session_dir in self._sessions_dir.iterdir():
            session_id = session_dir.name
            if (
                not session_dir.is_dir()
                or session_id.startswith("SESSION_DEMO_")
                or not self._valid_session_id(session_id)
            ):
                continue
            session = self._read_json(session_dir / "session.json")
            if not self._is_device_session(session, session_id):
                continue
            sessions[session_id] = self._session_summary(session)

            report = self._read_json(session_dir / "doctor" / "report.json")
            if self._is_device_report(report, session_id):
                reports[session_id] = deepcopy(report)

        return sessions, reports

    def save_session(self, summary: dict[str, Any]) -> None:
        session_id = str(summary.get("session_id") or "")
        if not self._is_device_session(summary, session_id):
            return
        self._write_json(
            TRAINING_DATA_RESOLVER.session_descriptor_path(session_id),
            self._session_summary(summary),
        )

    def save_report(self, session_id: str, report: dict[str, Any]) -> None:
        if not self._valid_session_id(session_id):
            return
        if not self._is_device_report(report, session_id):
            return
        self._write_json(
            self._session_dir(session_id) / "doctor" / "report.json",
            report,
        )

    def save_runtime_summary(self, session_id: str, summary: dict[str, Any]) -> None:
        self._write_json(
            TRAINING_DATA_RESOLVER.session_runtime_summary_path(session_id),
            summary,
        )

    def session_directory(self, session_id: str) -> Path:
        return self._session_dir(session_id)

    def _session_dir(self, session_id: str) -> Path:
        if not self._valid_session_id(session_id):
            raise ValueError("Invalid device session_id.")
        return TRAINING_DATA_RESOLVER.resolve_doctor_session(PATIENT_ID, session_id)

    @staticmethod
    def _valid_session_id(session_id: str) -> bool:
        return (
            0 < len(session_id) <= 128
            and not session_id.startswith("SESSION_DEMO_")
            and session_id.replace("-", "").replace("_", "").isalnum()
        )

    @staticmethod
    def _session_summary(data: dict[str, Any]) -> dict[str, Any]:
        return {field: deepcopy(data.get(field)) for field in SESSION_FIELDS}

    @classmethod
    def _is_device_session(
        cls, data: dict[str, Any] | None, session_id: str
    ) -> bool:
        return bool(
            isinstance(data, dict)
            and cls._valid_session_id(session_id)
            and data.get("source_mode") in {"device", "pc_local"}
            and data.get("patient_id") == PATIENT_ID
            and data.get("session_id") == session_id
        )

    @staticmethod
    def _is_device_report(
        data: dict[str, Any] | None, session_id: str
    ) -> bool:
        if not isinstance(data, dict):
            return False
        patient = data.get("patient")
        versions = data.get("versions")
        return bool(
            isinstance(patient, dict)
            and patient.get("patient_id") == PATIENT_ID
            and patient.get("session_id") == session_id
            and isinstance(versions, dict)
            and versions.get("source_mode") == "device"
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

device_history_store = DeviceHistoryStore()
