from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools import drink_water_session_scoring_v1 as orchestrator


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "drink_water_session_scoring_v1.py"


def winerror5() -> PermissionError:
    exc = PermissionError("access denied")
    exc.winerror = 5
    return exc


def permission_error_without_winerror5() -> PermissionError:
    exc = PermissionError("different permission error")
    exc.winerror = 32
    return exc


class FakeDirectory:
    def __init__(self, side_effect=None):
        self.rename = Mock(side_effect=side_effect)


class DrinkWaterSessionAtomicCommitContractTest(unittest.TestCase):
    def test_rename_success_first_try_does_not_sleep(self) -> None:
        source = FakeDirectory()
        with patch.object(orchestrator.time, "sleep") as sleep:
            orchestrator.rename_directory_with_retry(source, Path("target"))
        self.assertEqual(source.rename.call_count, 1)
        sleep.assert_not_called()

    def test_winerror5_first_failure_then_success_retries(self) -> None:
        source = FakeDirectory(side_effect=[winerror5(), None])
        with patch.object(orchestrator.time, "sleep") as sleep:
            orchestrator.rename_directory_with_retry(source, Path("target"))
        self.assertEqual(source.rename.call_count, 2)
        sleep.assert_called_once_with(0.10)

    def test_repeated_winerror5_raises_last_error_after_finite_retries(self) -> None:
        source = FakeDirectory(side_effect=[winerror5()] * 6)
        with patch.object(orchestrator.time, "sleep") as sleep:
            with self.assertRaises(PermissionError):
                orchestrator.rename_directory_with_retry(source, Path("target"))
        self.assertEqual(source.rename.call_count, 6)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.10, 0.20, 0.40, 0.80, 1.00])

    def test_non_winerror5_permission_error_does_not_retry(self) -> None:
        source = FakeDirectory(side_effect=permission_error_without_winerror5())
        with patch.object(orchestrator.time, "sleep") as sleep:
            with self.assertRaises(PermissionError):
                orchestrator.rename_directory_with_retry(source, Path("target"))
        self.assertEqual(source.rename.call_count, 1)
        sleep.assert_not_called()

    def test_other_rename_exception_does_not_retry(self) -> None:
        source = FakeDirectory(side_effect=FileNotFoundError("missing"))
        with patch.object(orchestrator.time, "sleep") as sleep:
            with self.assertRaises(FileNotFoundError):
                orchestrator.rename_directory_with_retry(source, Path("target"))
        self.assertEqual(source.rename.call_count, 1)
        sleep.assert_not_called()

    def test_retry_helper_only_repeats_rename_not_scoring_logic(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        helper_body = source.split("def rename_directory_with_retry", 1)[1].split("def sha256_file", 1)[0]
        self.assertIn("source.rename(target)", helper_body)
        self.assertNotIn("score_trial_json", helper_body)
        self.assertNotIn("build_trial_from_raw_log", helper_body)
        self.assertNotIn("segment_file", helper_body)

    def test_session_dir_missing_commit_uses_retry_helper(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("else:\n            rename_directory_with_retry(tmp_dir, session_dir)", source)

    def test_overwrite_rollback_commit_uses_retry_helper(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("rename_directory_with_retry(session_dir, rollback_dir)", source)
        self.assertIn("rename_directory_with_retry(tmp_dir, session_dir)", source)

    def test_tmp_commit_failure_restores_rollback_with_retry_helper(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("if not session_dir.exists() and rollback_dir.exists():\n                    rename_directory_with_retry(rollback_dir, session_dir)", source)


if __name__ == "__main__":
    unittest.main()
