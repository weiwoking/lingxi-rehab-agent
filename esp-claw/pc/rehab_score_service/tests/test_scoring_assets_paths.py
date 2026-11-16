from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from scoring_assets import SCORING_ASSETS_ROOT, scoring_asset_path


def test_default_root_is_separate_from_repo_and_training_authority() -> None:
    service_root = Path(__file__).resolve().parents[1]
    assert SCORING_ASSETS_ROOT == Path(r"C:\ec\_rehab_scoring_assets")
    assert not SCORING_ASSETS_ROOT.is_relative_to(service_root)
    assert SCORING_ASSETS_ROOT != Path(r"C:\ec\_rehab_training_data")


def test_resolver_rejects_traversal() -> None:
    with pytest.raises(ValueError, match="escapes"):
        scoring_asset_path("..", "outside.json")


def test_environment_override_has_no_directory_creation_side_effect() -> None:
    service_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temp:
        configured = Path(temp) / "not-created" / "assets"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(service_root)
        environment["REHAB_SCORING_ASSETS_ROOT"] = str(configured)
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                "from scoring_assets import SCORING_ASSETS_ROOT; print(SCORING_ASSETS_ROOT)",
            ],
            cwd=temp,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert Path(result.stdout.strip()) == configured
        assert not configured.exists()
