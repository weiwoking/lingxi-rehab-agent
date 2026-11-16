"""Validation rules for stable, path-safe training session identifiers."""

from __future__ import annotations

import re
from pathlib import Path


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class InvalidSessionId(ValueError):
    """Raised when a session identifier cannot safely be used as one path part."""


def validate_session_id(session_id: str) -> str:
    """Return *session_id* unchanged when it is stable and path-safe.

    Existing identifiers such as ``20260814_TASK009`` are deliberately accepted.
    Normalization is not performed because changing case or punctuation could
    silently create a second identity for an existing session.
    """

    if not isinstance(session_id, str):
        raise InvalidSessionId("session_id must be a string")
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise InvalidSessionId(
            "session_id must be 1-128 ASCII letters, digits, '.', '_' or '-', "
            "and must start with a letter or digit"
        )
    if session_id in {".", ".."} or session_id.endswith((".", " ")):
        raise InvalidSessionId("session_id is not a safe path component")
    windows_stem = session_id.split(".", 1)[0].upper()
    if windows_stem in _WINDOWS_RESERVED_NAMES:
        raise InvalidSessionId("session_id is a reserved Windows path name")
    return session_id


def join_session_path(session_root: Path, session_id: str) -> Path:
    """Join a validated session ID below *session_root* without touching disk."""

    valid_id = validate_session_id(session_id)
    base = Path(session_root)
    candidate = base / valid_id
    if candidate.parent != base:
        # This should be unreachable after validation and remains a defense in depth.
        raise InvalidSessionId("session_id escaped the session root")
    return candidate
