from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class PcRuntimeIncrementalReconcileC2BStaticContractTests(unittest.TestCase):
    def test_known_session_fast_path_precedes_task_json_read(self) -> None:
        source = read_text("backend/app/pc_local_runtime_service.py")
        function_source = source.split("def _reconcile_score_runtime_sessions", 1)[1].split("\n\n    def _adopt_score_job_session_summary", 1)[0]
        fast_index = function_source.index("cached_csv_path = self._cached_runtime_csv_path")
        task_read_index = function_source.index('self._read_runtime_json(input_dir / "task.json")')
        self.assertLess(fast_index, task_read_index)
        self.assertIn("self._runtime_signature_or_none(", function_source)
        self.assertIn("self._runtime_signatures.get(session_id) == cached_signature", function_source)

    def test_unchanged_immutable_canonical_session_skips_heavy_work(self) -> None:
        source = read_text("backend/app/pc_local_runtime_service.py")
        function_source = source.split("def _reconcile_score_runtime_sessions", 1)[1].split("\n\n    def _adopt_score_job_session_summary", 1)[0]
        unchanged_branch = function_source.split("self._runtime_signatures.get(session_id) == cached_signature", 1)[1].split('task = self._read_runtime_json', 1)[0]
        self.assertIn("self._is_immutable_canonical_completed_session(cached)", unchanged_branch)
        self.assertIn("continue", unchanged_branch)
        self.assertNotIn('self._read_runtime_json(\n                        resolved_session_dir / "job_status.json"', unchanged_branch)
        self.assertNotIn("_adopt_score_job_session_summary(", unchanged_branch)

    def test_nonterminal_v12_refresh_is_preserved_on_unchanged_fast_path(self) -> None:
        source = read_text("backend/app/pc_local_runtime_service.py")
        function_source = source.split("def _reconcile_score_runtime_sessions", 1)[1].split("\n\n    def _adopt_score_job_session_summary", 1)[0]
        unchanged_branch = function_source.split("self._runtime_signatures.get(session_id) == cached_signature", 1)[1].split('task = self._read_runtime_json', 1)[0]
        self.assertIn("self._should_refresh_session_v12_summary(cached)", unchanged_branch)
        self.assertIn("self._reconcile_session_v12_summary(cached)", unchanged_branch)
        refresh_source = source.split("def _should_refresh_session_v12_summary", 1)[1].split("\n\n    @classmethod", 1)[0]
        self.assertIn('{"", "not_started", "pending", "running", "failed"}', refresh_source)
        self.assertIn('summary.get("rescore_allowed") is True', refresh_source)

    def test_fast_signature_fields_cover_task_csv_and_score_artifacts(self) -> None:
        source = read_text("backend/app/pc_local_runtime_service.py")
        signature_source = source.split("def _runtime_signature", 1)[1].split("\n\n    def _runtime_signature_or_none", 1)[0]
        self.assertIn('"task.json"', signature_source)
        self.assertIn("resolver.job_status_path(session_id)", signature_source)
        self.assertIn('"full_result.json"', signature_source)
        self.assertIn('"report.json"', signature_source)
        self.assertIn('"dedicated_scoring"', signature_source)
        self.assertIn('"session_summary.json"', signature_source)
        self.assertIn('"session_manifest.json"', signature_source)
        self.assertIn("csv_stat.st_size", signature_source)
        self.assertIn("csv_stat.st_mtime_ns", signature_source)

    def test_cached_csv_file_is_reused_safely_without_task_json(self) -> None:
        source = read_text("backend/app/pc_local_runtime_service.py")
        helper_source = source.split("def _cached_runtime_csv_path", 1)[1].split("\n\n    @staticmethod\n    def _is_immutable", 1)[0]
        self.assertIn('cached.get("csv_file")', helper_source)
        self.assertIn("self._is_safe_runtime_csv_name(csv_name)", helper_source)
        self.assertIn("_score_path_resolver().session_input_path", helper_source)
        self.assertIn("csv_path.is_file()", helper_source)
        self.assertNotIn("task.json", helper_source)

    def test_new_or_changed_session_falls_back_to_full_processing(self) -> None:
        source = read_text("backend/app/pc_local_runtime_service.py")
        function_source = source.split("def _reconcile_score_runtime_sessions", 1)[1].split("\n\n    def _adopt_score_job_session_summary", 1)[0]
        self.assertIn('task = self._read_runtime_json(input_dir / "task.json")', function_source)
        self.assertIn("self._runtime_signatures[session_id] = signature", function_source)
        self.assertIn("job_status = self._read_runtime_json(\n                resolver.job_status_path(session_id)", function_source)

    def test_whole_reconcile_is_not_moved_to_thread_and_watch_interval_is_unchanged(self) -> None:
        source = read_text("backend/app/pc_local_runtime_service.py")
        self.assertIn("await asyncio.sleep(PC_SESSION_WATCH_INTERVAL_SECONDS)", source)
        self.assertIn("runtime_changed = await self._refresh_score_runtime_sessions()", source)
        self.assertIn("changed = self._reconcile_score_runtime_sessions()", source)
        self.assertNotIn("asyncio.to_thread(\n            self._reconcile_score_runtime_sessions", source)
        self.assertNotIn("ThreadPoolExecutor", source)

    def test_immutable_canonical_completed_definition_matches_score_job_adoption(self) -> None:
        source = read_text("backend/app/pc_local_runtime_service.py")
        helper_source = source.split("def _is_immutable_canonical_completed_session", 1)[1].split("\n\n    def _should_refresh_session_v12_summary", 1)[0]
        self.assertIn('summary.get("session_v12_status") == "completed"', helper_source)
        self.assertIn('"score_service_dedicated_job"', helper_source)
        self.assertIn('"score_service_runtime"', helper_source)

    def test_deleted_session_is_pruned_from_runtime_caches_and_read_apis(self) -> None:
        source = read_text("backend/app/pc_local_runtime_service.py")
        reconcile = source.split("def _reconcile_score_runtime_sessions", 1)[1].split(
            "\n\n    def _adopt_score_job_session_summary", 1
        )[0]
        prune = source.split("def _prune_deleted_runtime_sessions", 1)[1].split(
            "\n\n    def _adopt_score_job_session_summary", 1
        )[0]
        sessions_api = source.split("def sessions", 1)[1].split("\n\n    def session", 1)[0]
        session_api = source.split("def session(self, session_id", 1)[1].split(
            "\n\n    def report", 1
        )[0]
        self.assertIn("present_session_ids", reconcile)
        self.assertIn("self._prune_deleted_runtime_sessions(present_session_ids)", reconcile)
        self.assertIn("self._sessions.pop(session_id, None)", prune)
        self.assertIn("self._reports.pop(session_id, None)", prune)
        self.assertIn("device_history_store.session_directory(session_id).is_dir()", sessions_api)
        self.assertIn("device_history_store.session_directory(session_id).is_dir()", session_api)


if __name__ == "__main__":
    unittest.main()
