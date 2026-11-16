import subprocess
import sys
from pathlib import Path

import pytest

from pc.rehab_training_data.migration import (
    execute_migration,
    plan_migration,
    validate_manifest_output_path,
)
from pc.rehab_training_data.validation import validate_records


FIXTURE = Path(__file__).parent / "fixtures" / "fake_session"


def test_fixture_copy_and_hash_verification(tmp_path: Path) -> None:
    target = tmp_path / "sessions" / "fake_session"
    plan = plan_migration(FIXTURE, target)

    assert len(plan.entries) == 5
    assert not target.exists()
    execute_migration(plan)

    assert validate_records(target, plan.records()).matches
    assert (target / "input" / "task.json").is_file()
    assert (target / "input" / "sensor.csv").is_file()
    assert (target / "scoring" / "full_result.json").is_file()
    assert (target / "doctor" / "trajectory" / "trajectory.json").is_file()
    assert (target / "doctor" / "report.json").is_file()


def test_dry_run_cli_writes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "fake_session"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pc.rehab_training_data.migration",
            "--source",
            str(FIXTURE),
            "--target",
            str(target),
            "--dry-run",
        ],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mode=DRY-RUN" in result.stdout
    assert "will_copy_files=5" in result.stdout
    assert "writes_performed=0" in result.stdout
    assert not target.exists()
    assert not target.parent.exists()


def test_execute_refuses_existing_target_without_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "existing"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("unchanged", encoding="utf-8")

    with pytest.raises(FileExistsError):
        execute_migration(plan_migration(FIXTURE, target))

    assert marker.read_text(encoding="utf-8") == "unchanged"


def test_non_session_artifacts_are_not_migration_candidates(tmp_path: Path) -> None:
    source = tmp_path / "fixture_002"
    source.mkdir()
    (source / "task.json").write_text("{}", encoding="utf-8")
    (source / "notes.txt").write_text("not session data", encoding="utf-8")
    logs = source / "logs"
    logs.mkdir()
    (logs / "all.log").write_text("excluded", encoding="utf-8")

    plan = plan_migration(source, tmp_path / "target")

    assert [entry.source_relative for entry in plan.entries] == ["task.json"]
    assert plan.skipped == ("notes.txt",)


def test_manifest_output_cannot_modify_source_or_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    plan = plan_migration(FIXTURE, target)

    with pytest.raises(ValueError, match="source"):
        validate_manifest_output_path(plan, FIXTURE / "manifest.json")
    with pytest.raises(ValueError, match="target"):
        validate_manifest_output_path(plan, target / "manifest.json")
