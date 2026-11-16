from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class DoctorSessionPageStaticContractTest(unittest.TestCase):
    def test_doctor_sessions_and_reports_use_different_components(self) -> None:
        source = read_text("frontend/src/App.tsx")
        self.assertIn('path="sessions" element={<SessionRecordsPage />}', source)
        self.assertIn('path="reports" element={<SessionHistoryPage />}', source)
        self.assertIn('path="sessions/:sessionId" element={<SessionResultsPage />}', source)

    def test_session_records_page_is_dedicated_component(self) -> None:
        source = read_text("frontend/src/pages/SessionRecordsPage.tsx")
        self.assertIn("Session 记录", source)
        self.assertIn("fetchDoctorSessions", source)
        self.assertNotIn("fetchSessionReport", source)

    def test_session_results_page_does_not_initialize_three_scene(self) -> None:
        source = read_text("frontend/src/pages/SessionResultsPage.tsx")
        self.assertNotIn("AnatomyScene", source)
        self.assertNotIn("new AnatomyScene", source)
        self.assertNotIn("session-real-twin-stage", source)

    def test_session_results_has_inline_score_and_sensor_tabs(self) -> None:
        source = read_text("frontend/src/pages/SessionResultsPage.tsx")
        self.assertIn('"score"', source)
        self.assertIn('"sensors"', source)
        self.assertIn("评分摘要", source)
        self.assertIn("传感器曲线", source)

    def test_session_sensor_curves_do_not_use_live_store_or_websocket(self) -> None:
        source = read_text("frontend/src/pages/SessionResultsPage.tsx")
        self.assertIn("fetchSessionTrajectory", source)
        self.assertNotIn("useRehabStore", source)
        self.assertNotIn("RehabWebSocketClient", source)

    def test_session_sensor_area_uses_eight_card_series(self) -> None:
        source = read_text("frontend/src/pages/SessionResultsPage.tsx")
        self.assertIn("session-sensor-card-grid", source)
        self.assertIn("props.series.imu.map", source)
        self.assertIn("props.series.emg.map", source)

    def test_doctor_session_pages_use_light_workspace_theme(self) -> None:
        source = read_text("frontend/src/styles.css")
        self.assertIn(".doctor-session-page", source)
        self.assertIn("Doctor Session white contrast final guard.", source)
        self.assertIn("background: #f4f7f8", source)
        self.assertIn("background: var(--page-bg)", source)
    def test_doctor_session_final_contrast_guard_uses_no_dark_panel_backgrounds(self) -> None:
        source = read_text("frontend/src/styles.css")
        guard = source.split("Doctor Session white contrast final guard.", 1)[1]
        self.assertNotIn("#071c23", guard)
        self.assertNotIn("#061a21", guard)
        self.assertNotIn("#0a1920", guard)
        self.assertNotIn("rgba(255,255,255", guard)

    def test_doctor_session_score_and_feedback_use_readable_dark_text(self) -> None:
        source = read_text("frontend/src/styles.css")
        self.assertIn(".doctor-session-page .session-selected-score strong", source)
        self.assertIn("color: #0f766e", source)
        self.assertIn(".doctor-session-page .session-feedback-box p", source)
        self.assertIn("color: #334155", source)

    def test_patient_page_styles_are_not_scoped_to_doctor_session_theme(self) -> None:
        source = read_text("frontend/src/styles.css")
        guard = source.split("Doctor Session white contrast final guard.", 1)[1]
        self.assertNotIn(".patient-training-page", guard)
        self.assertNotIn(".patient-report-center-page", guard)
        self.assertNotIn(".patient-sensor-grid", guard)

    def test_history_trajectory_series_is_progressively_clipped(self) -> None:
        source = read_text("frontend/src/twin/actualTrajectory.ts")
        self.assertIn("MAX_HISTORY_VISIBLE_POINTS = 120", source)
        self.assertIn("point.timeMs <= cursorMs", source)
        self.assertIn(".slice(-MAX_HISTORY_VISIBLE_POINTS)", source)
        self.assertIn("function playbackWindow(points", source)
        self.assertIn("points: playbackWindow(allPoints, cursorMs)", source)
        self.assertIn("function makeImuSeries", source)
        self.assertIn("function makeEmgSeries", source)

    def test_session_sensor_cards_show_played_and_total_samples(self) -> None:
        source = read_text("frontend/src/pages/SessionResultsPage.tsx")
        self.assertIn("已播放", source)
        self.assertIn("row.totalPoints", source)
        self.assertIn("当前值", source)

    def test_patient_live_page_is_not_rewired(self) -> None:
        source = read_text("frontend/src/pages/LiveMonitorPage.tsx")
        self.assertIn("useRehabStore", source)
        self.assertIn("sensor-chart-grid patient-sensor-grid", source)

    def test_patient_report_page_has_rep_score_picker_without_print_action(self) -> None:
        source = read_text("frontend/src/pages/PatientReportsPage.tsx")
        self.assertIn("逐次评分", source)
        self.assertIn("patient-rep-picker", source)
        self.assertIn("本次总分", source)
        self.assertNotIn("打印报告", source)

    def test_patient_rep_detail_contains_subscores_and_compensation_labels(self) -> None:
        source = read_text("frontend/src/pages/PatientReportsPage.tsx")
        self.assertIn("speed_score", source)
        self.assertIn("ROM_score", source)
        self.assertIn("smoothness_score", source)
        self.assertIn("coordination_score", source)
        self.assertIn("muscle_score", source)
        self.assertIn("躯干前倾", source)
        self.assertIn("躯干旋转", source)
        self.assertIn("手部抖动", source)

    def test_reconstruction_page_goes_directly_to_actual_and_keeps_internal_prior(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        play_selected = source.split("function playSelectedRecord", 1)[1].split("function returnToRecords", 1)[0]
        self.assertIn('view: "actual"', play_selected)
        self.assertNotIn('view: "fixed"', play_selected)
        self.assertNotIn('{view === "fixed"', source)
        self.assertIn("composeHybridDrinkPose", source)
        self.assertIn("composeHybridLateralTransferPose", source)

    def test_reconstruction_page_actual_is_selected_session_offline_csv_replay(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn('export type ReconstructionView = "records" | "actual"', source)
        self.assertIn("患者动作还原", source)
        self.assertIn("已保存训练CSV", source)
        self.assertIn("fetchSessionResult", source)
        self.assertIn("fetchSessionTrajectory", source)
        self.assertIn("resolveSessionNeutralFrame", source)
        self.assertIn("composeHybridDrinkPose", source)
        self.assertNotIn("runtimeSnapshot", source)
        self.assertNotIn("SESSION_IDLE", source)


if __name__ == "__main__":
    unittest.main()
