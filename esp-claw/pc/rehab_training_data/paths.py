"""Side-effect-free path resolver for the future unified training data root."""

from __future__ import annotations

import os
from os import PathLike
from pathlib import Path

from .session_identity import join_session_path


DEFAULT_TRAINING_ROOT = Path(__file__).resolve().parents[3] / "external" / "_rehab_training_data"
TRAINING_ROOT_ENV = "REHAB_TRAINING_DATA_ROOT"


def get_training_root(root: str | PathLike[str] | None = None) -> Path:
    """Resolve the configured root without creating or modifying it.

    An explicit argument wins, followed by ``REHAB_TRAINING_DATA_ROOT``.  The
    environment variable is only consumed by callers of this new package; no
    current runtime is connected to it.
    """

    configured = root if root is not None else os.environ.get(TRAINING_ROOT_ENV)
    return Path(configured) if configured is not None else DEFAULT_TRAINING_ROOT


def get_session_root(
    session_id: str, root: str | PathLike[str] | None = None
) -> Path:
    return join_session_path(get_training_root(root) / "sessions", session_id)


def get_input_path(
    session_id: str, root: str | PathLike[str] | None = None
) -> Path:
    return get_session_root(session_id, root) / "input"


def get_score_path(
    session_id: str, root: str | PathLike[str] | None = None
) -> Path:
    return get_session_root(session_id, root) / "scoring"


def get_doctor_path(
    session_id: str, root: str | PathLike[str] | None = None
) -> Path:
    return get_session_root(session_id, root) / "doctor"
