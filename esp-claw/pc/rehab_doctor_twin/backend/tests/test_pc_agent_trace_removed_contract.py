from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class PcAgentTraceRemovedContractTest(unittest.TestCase):
    def test_doctor_navigation_and_routes_have_no_agent_trace_surface(self) -> None:
        app = read_text("frontend/src/App.tsx")
        navigation = read_text("frontend/src/layouts/PatientWorkspaceLayout.tsx")
        self.assertNotIn("AgentTracePage", app)
        self.assertNotIn('path="agent"', app)
        self.assertIn('path="*" element={<Navigate to="overview" replace />}', app)
        self.assertNotIn("Agent 轨迹", navigation)
        for label in ("总览", "数据日志", "动作还原", "Session记录", "训练报告"):
            self.assertIn(label, navigation)

    def test_frontend_trace_page_client_types_and_styles_are_removed(self) -> None:
        for relative in (
            "frontend/src/pages/AgentTracePage.tsx",
            "frontend/src/services/agentTraceApi.ts",
            "frontend/src/components/AgentDecisionTrace.tsx",
            "frontend/src/components/AgentEventTimeline.tsx",
            "frontend/src/adapters/ClinicalEventAdapter.ts",
        ):
            self.assertFalse((ROOT / relative).exists(), relative)
        combined = "\n".join(
            read_text(relative)
            for relative in (
                "frontend/src/contracts/clinicalWorkspace.ts",
                "frontend/src/services/ClinicalWorkspaceDataSourceContract.ts",
                "frontend/src/services/HttpClinicalWorkspaceDataSource.ts",
                "frontend/src/services/DemoClinicalWorkspaceDataSource.ts",
                "frontend/src/styles.css",
            )
        )
        for token in ("AgentTrace", "agent-trace", ".agent-page", "reasoning_content"):
            self.assertNotIn(token, combined)

    def test_backend_trace_routes_relay_and_persistence_are_removed(self) -> None:
        main = read_text("backend/app/main.py")
        config = read_text("backend/app/config.py")
        persistence = read_text("backend/app/device_history_store.py")
        pc_runtime = read_text("backend/app/pc_local_runtime_service.py")
        self.assertNotIn("/api/agent/latest-task-decision-trace", main)
        self.assertNotIn('/sessions/{session_id}/trace', main)
        self.assertNotIn("DEEPSEEK_RELAY", config)
        self.assertNotIn("agent_trace", persistence)
        self.assertNotIn("agent_trace", pc_runtime)
        self.assertFalse((ROOT / "backend/app/deepseek_relay_trace_service.py").exists())

    def test_generic_runtime_events_remain_available_to_logs_and_websocket(self) -> None:
        runtime = read_text("backend/app/real_runtime_service.py")
        contracts = read_text("frontend/src/contracts/realtimeMessages.ts")
        self.assertIn("_runtime_events", runtime)
        self.assertIn('self._logs[session_id]["events"]', runtime)
        self.assertIn('RealtimeMessageBase<"rehab.event", RehabEvent>', contracts)


if __name__ == "__main__":
    unittest.main()
