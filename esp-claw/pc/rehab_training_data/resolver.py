"""Path resolver for the active unified PC training-data authority."""

from __future__ import annotations

from os import PathLike
from pathlib import Path

from .paths import get_training_root as resolve_training_root
from .session_identity import join_session_path, validate_session_id


class TrainingDataResolver:
    """Resolve unified-authority paths without touching the filesystem."""

    def __init__(
        self,
        *,
        training_root: str | PathLike[str] | None = None,
    ) -> None:
        self._training_root = resolve_training_root(training_root)

    def get_score_root(self) -> Path:
        return self._training_root

    def get_doctor_root(self) -> Path:
        return self._training_root

    def get_training_root(self) -> Path:
        return self._training_root

    def score_sessions_root(self) -> Path:
        return self._training_root / "sessions"

    def score_staging_root(self) -> Path:
        return self.upload_staging_root()

    def operations_root(self) -> Path:
        return self._training_root / "operations"

    def job_status_path(self, session_id: str) -> Path:
        return self.session_runtime_path(session_id) / "job_state.json"

    def staging_root(self) -> Path:
        return self.operations_root() / "staging"

    def upload_staging_root(self) -> Path:
        return self.staging_root() / "uploads"

    def scoring_staging_root(self) -> Path:
        return self.staging_root() / "scoring"

    def running_root(self) -> Path:
        return self.operations_root() / "running"

    def locks_root(self) -> Path:
        return self.operations_root() / "locks"

    def queue_root(self) -> Path:
        return self.operations_root() / "queue"

    def score_session_path(self, session_id: str) -> Path:
        return join_session_path(self.score_sessions_root(), session_id)

    def resolve_score_session(self, session_id: str) -> Path:
        return self.score_session_path(session_id)

    def score_staging_session_path(self, session_id: str) -> Path:
        return join_session_path(self.upload_staging_root(), session_id)

    def scoring_staging_session_path(self, session_id: str) -> Path:
        return join_session_path(self.scoring_staging_root(), session_id)

    def session_input_path(self, session_id: str) -> Path:
        return self.score_session_path(session_id) / "input"

    def session_input_file_path(self, session_id: str, file_name: str) -> Path:
        if (
            not isinstance(file_name, str)
            or not file_name
            or file_name in {".", ".."}
            or "/" in file_name
            or "\\" in file_name
            or Path(file_name).name != file_name
        ):
            raise ValueError("input file name must be one safe path component")
        return self.session_input_path(session_id) / file_name

    def session_scoring_path(self, session_id: str) -> Path:
        return self.score_session_path(session_id) / "scoring"

    def session_doctor_path(self, session_id: str) -> Path:
        return self.score_session_path(session_id) / "doctor"

    def session_runtime_path(self, session_id: str) -> Path:
        return self.score_session_path(session_id) / "runtime"

    def session_descriptor_path(self, session_id: str) -> Path:
        return self.score_session_path(session_id) / "session.json"

    def session_runtime_summary_path(self, session_id: str) -> Path:
        return self.session_runtime_path(session_id) / "doctor_state.json"

    def session_doctor_state_path(self, session_id: str) -> Path:
        return self.session_doctor_path(session_id) / "session_v12_scoring.json"

    def session_trajectory_path(self, session_id: str) -> Path:
        return self.session_doctor_path(session_id) / "trajectory"

    def session_metadata_path(self, session_id: str) -> Path:
        return self.score_session_path(session_id)

    def sync_root(self) -> Path:
        return self.queue_root()

    def sync_queue_path(self) -> Path:
        return self.sync_root() / "cloud_sync.json"

    def doctor_patient_path(self, patient_id: str) -> Path:
        validate_session_id(patient_id)
        return self._training_root

    def doctor_sessions_root(self, patient_id: str) -> Path:
        self.doctor_patient_path(patient_id)
        return self.score_sessions_root()

    def doctor_session_path(self, patient_id: str, session_id: str) -> Path:
        return join_session_path(self.doctor_sessions_root(patient_id), session_id)

    def resolve_doctor_session(self, patient_id: str, session_id: str) -> Path:
        return self.doctor_session_path(patient_id, session_id)

_DEFAULT_RESOLVER = TrainingDataResolver()


def get_score_root() -> Path:
    return _DEFAULT_RESOLVER.get_score_root()


def get_doctor_root() -> Path:
    return _DEFAULT_RESOLVER.get_doctor_root()


def get_training_root() -> Path:
    return _DEFAULT_RESOLVER.get_training_root()


def resolve_score_session(session_id: str) -> Path:
    return _DEFAULT_RESOLVER.resolve_score_session(session_id)


def resolve_doctor_session(patient_id: str, session_id: str) -> Path:
    return _DEFAULT_RESOLVER.resolve_doctor_session(patient_id, session_id)
