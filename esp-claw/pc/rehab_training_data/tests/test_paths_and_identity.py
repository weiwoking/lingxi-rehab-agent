from pathlib import Path

import pytest

from pc.rehab_training_data.paths import (
    DEFAULT_TRAINING_ROOT,
    get_doctor_path,
    get_input_path,
    get_score_path,
    get_session_root,
    get_training_root,
)
from pc.rehab_training_data.session_identity import InvalidSessionId, validate_session_id


def test_default_root_and_resolvers_have_no_filesystem_side_effect(tmp_path: Path) -> None:
    root = tmp_path / "not-created"
    session_id = "20260814_TASK009"

    assert get_training_root() == DEFAULT_TRAINING_ROOT
    assert get_session_root(session_id, root) == root / "sessions" / session_id
    assert get_input_path(session_id, root) == root / "sessions" / session_id / "input"
    assert get_score_path(session_id, root) == root / "sessions" / session_id / "scoring"
    assert get_doctor_path(session_id, root) == root / "sessions" / session_id / "doctor"
    assert not root.exists()


@pytest.mark.parametrize(
    "invalid",
    ["", ".", "..", "../escape", r"folder\\session", "session/name", "CON", "bad:name", " space"],
)
def test_invalid_session_ids_are_rejected(invalid: str) -> None:
    with pytest.raises(InvalidSessionId):
        validate_session_id(invalid)


def test_existing_session_id_is_preserved_exactly() -> None:
    assert validate_session_id("20260814_TASK009") == "20260814_TASK009"
