from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class DoctorDetailPerformanceC1StaticContractTests(unittest.TestCase):
    def test_backend_completed_detail_cache_is_bounded_and_completed_only(self) -> None:
        source = read_text("backend/app/session_v12_scoring_service.py")
        self.assertIn("MAX_SESSION_DETAIL_CACHE = 64", source)
        self.assertIn("OrderedDict[tuple[str, tuple[Any, ...]], dict[str, Any]]", source)
        self.assertIn('if detail.get("scoring_status") == "completed"', source)
        self.assertIn("_store_completed_detail(session_id, fingerprint, detail)", source)
        self.assertIn("while len(self._detail_cache) > MAX_SESSION_DETAIL_CACHE", source)
        self.assertNotIn("raw_csv_dataframe", source)

    def test_backend_detail_cache_uses_file_fingerprint_invalidation(self) -> None:
        source = read_text("backend/app/session_v12_scoring_service.py")
        self.assertIn("def _session_detail_fingerprint", source)
        self.assertIn("session.json", source)
        self.assertIn("session_summary.json", source)
        self.assertIn("score_result.json", source)
        self.assertIn("session_v12_source_manifest.json", source)
        self.assertIn("rep_*_score.json", source)
        self.assertIn("rep_*.metadata.json", source)
        self.assertIn("rep_*_trajectory.json", source)
        self.assertIn("stat.st_mtime_ns", source)
        self.assertIn("stat.st_size", source)

    def test_sha_cache_is_bounded_and_keyed_by_path_size_mtime(self) -> None:
        source = read_text("backend/app/session_v12_scoring_service.py")
        self.assertIn("MAX_SHA256_CACHE = 128", source)
        self.assertIn("_SHA256_CACHE", source)
        self.assertIn("key = (str(resolved), int(stat.st_size), int(stat.st_mtime_ns))", source)
        self.assertIn("_sha256_file_uncached(resolved)", source)
        self.assertIn("while len(_SHA256_CACHE) > MAX_SHA256_CACHE", source)

    def test_score_summary_mode_does_not_fetch_trajectory(self) -> None:
        source = read_text("frontend/src/pages/SessionResultsPage.tsx")
        effect = source.split('if (mode !== "sensors") return;', 1)[1].split("fetchSessionTrajectory", 1)[0]
        self.assertIn("setTrajectory(null)", effect)
        self.assertIn("setPlaying(false)", effect)
        self.assertNotIn("fetchSessionTrajectory", effect)

    def test_sensor_mode_fetches_trajectory_lazily(self) -> None:
        source = read_text("frontend/src/pages/SessionResultsPage.tsx")
        self.assertIn('if (mode !== "sensors") return;', source)
        self.assertIn("fetchSessionTrajectory(sessionId, selectedRep.rep_index)", source)
        self.assertIn("[mode, selectedRep?.rep_index, sessionId]", source)

    def test_frontend_detail_and_trajectory_cache_and_dedup_are_memory_only(self) -> None:
        source = read_text("frontend/src/services/sessionResultsApi.ts")
        self.assertIn("const detailCache = new Map", source)
        self.assertIn("const detailInflight = new Map", source)
        self.assertIn("const trajectoryCache = new Map", source)
        self.assertIn("const trajectoryInflight = new Map", source)
        self.assertIn("DETAIL_CACHE_MAX_ENTRIES = 64", source)
        self.assertIn("TRAJECTORY_CACHE_MAX_ENTRIES = 32", source)
        self.assertNotIn("localStorage", source)
        self.assertNotIn("indexedDB", source)

    def test_frontend_detail_cache_is_immutable_completed_only(self) -> None:
        source = read_text("frontend/src/services/sessionResultsApi.ts")
        self.assertIn("rescore_allowed?: boolean", source)
        self.assertIn("function isImmutableCompletedDetail", source)
        self.assertIn('value.scoring_status === "completed" && value.rescore_allowed === false', source)
        self.assertIn("if (isImmutableCompletedDetail(value))", source)
        self.assertNotIn("function isCompletedDetail", source)

    def test_rescore_success_invalidates_detail_cache_defensively(self) -> None:
        source = read_text("frontend/src/services/sessionResultsApi.ts")
        rescore_block = source.split("export async function requestSessionRescore", 1)[1]
        self.assertIn("detailCache.delete(sessionId)", rescore_block)
        self.assertLess(rescore_block.index("detailCache.delete(sessionId)"), rescore_block.index("return ((payload"))

    def test_prefetch_is_bounded_detail_only_and_best_effort(self) -> None:
        records = read_text("frontend/src/pages/SessionRecordsPage.tsx")
        api = read_text("frontend/src/services/sessionResultsApi.ts")
        self.assertIn("PREFETCH_RECENT_COMPLETED_LIMIT = 3", records)
        self.assertIn("prefetchSessionResult(session.session_id)", records)
        self.assertIn("onMouseEnter", records)
        self.assertIn("onFocus", records)
        self.assertIn("void fetchSessionResult(sessionId).catch", api)
        self.assertNotIn("prefetchSessionTrajectory", records + api)

    def test_phase_b_and_reconcile_files_are_not_part_of_c1_scope(self) -> None:
        page = read_text("frontend/src/pages/SessionResultsPage.tsx")
        service = read_text("backend/app/session_v12_scoring_service.py")
        self.assertIn("fetchSessionResult", page)
        self.assertIn("def session_detail", service)
        self.assertNotIn("score_job_worker", page + service)
        self.assertNotIn("_refresh_score_runtime_sessions", service)


if __name__ == "__main__":
    unittest.main()
