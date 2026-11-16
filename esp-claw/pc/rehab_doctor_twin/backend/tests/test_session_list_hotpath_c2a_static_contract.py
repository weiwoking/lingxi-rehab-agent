from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class SessionListHotPathC2AStaticContractTests(unittest.TestCase):
    def test_runtime_session_v12_status_is_reused_before_disk_fallback(self) -> None:
        source = read_text("backend/app/main.py")
        function_source = source.split("def _with_session_v12_status", 1)[1].split("\n\nasync def snapshot_message", 1)[0]
        reuse_index = function_source.index("if existing_status in SESSION_V12_LIST_STATUS_VALUES")
        fallback_index = function_source.index("session_v12_scoring_service.status(session_id)")
        self.assertLess(reuse_index, fallback_index)
        self.assertIn('value["session_v12_status"] = existing_status', function_source)
        self.assertIn('value["session_v12_error"] = value.get("session_v12_error")', function_source)
        self.assertIn('value["session_v12_baseline_version"] = value.get("session_v12_baseline_version")', function_source)

    def test_missing_runtime_session_v12_status_still_falls_back_to_status_file(self) -> None:
        source = read_text("backend/app/main.py")
        function_source = source.split("def _with_session_v12_status", 1)[1].split("\n\nasync def snapshot_message", 1)[0]
        self.assertIn("session_id = str(value.get(\"session_id\") or \"\")", function_source)
        self.assertIn("status = session_v12_scoring_service.status(session_id)", function_source)
        self.assertIn('value["session_v12_status"] = status.get("status")', function_source)
        self.assertIn('value["session_v12_error"] = status.get("error")', function_source)
        self.assertIn('value["session_v12_baseline_version"] = status.get("baseline_version")', function_source)

    def test_patient_sessions_runtime_branch_uses_nonblocking_cloud_enrichment(self) -> None:
        source = read_text("backend/app/main.py")
        endpoint_source = source.split("async def patient_sessions", 1)[1].split('@app.get("/api/sessions")', 1)[0]
        self.assertIn("runtime.sessions()", endpoint_source)
        self.assertIn("cloud_sync_service.enrich_sessions", endpoint_source)
        self.assertIn("refresh_discovery=False", endpoint_source)

    def test_cloud_sync_enrich_can_skip_synchronous_discovery_but_keeps_fields(self) -> None:
        source = read_text("backend/app/cloud_sync_service.py")
        function_source = source.split("def enrich_sessions", 1)[1].split("\n\n    def check_cloud", 1)[0]
        self.assertIn("refresh_discovery: bool = True", function_source)
        self.assertIn("self.refresh_session_records(sessions)", function_source)
        self.assertIn("if refresh_discovery:", function_source)
        self.assertIn("self._discover_sessions()", function_source)
        self.assertIn('item["cloud_sync"] = self.session_status(session_id, refresh=False)', function_source)

    def test_cloud_background_worker_still_discovers_sessions_off_request_path(self) -> None:
        source = read_text("backend/app/cloud_sync_service.py")
        worker_source = source.split("async def _worker", 1)[1].split("\n\n    def _discover_sessions", 1)[0]
        self.assertIn("await asyncio.to_thread(self._discover_sessions)", worker_source)
        self.assertIn("WORKER_INTERVAL_SECONDS = 2.0", source)

    def test_session_records_and_reconstruction_endpoints_are_unchanged(self) -> None:
        reconstruction = read_text("frontend/src/pages/ReconstructionPage.tsx")
        patient_api = read_text("frontend/src/services/patientApi.ts")
        records = read_text("frontend/src/pages/SessionRecordsPage.tsx")
        session_api = read_text("frontend/src/services/sessionResultsApi.ts")
        self.assertIn("fetchSessions(PATIENT_ID)", reconstruction)
        self.assertIn("/api/patients/${encodeURIComponent(patientId)}/sessions", patient_api)
        self.assertIn("fetchDoctorSessions()", records)
        self.assertIn('fetch("/api/sessions")', session_api)


if __name__ == "__main__":
    unittest.main()
