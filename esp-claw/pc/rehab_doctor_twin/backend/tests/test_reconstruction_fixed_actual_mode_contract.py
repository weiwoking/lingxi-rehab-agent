from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class ReconstructionOfflineCsvModeContractTest(unittest.TestCase):
    def test_record_action_reconstruction_goes_directly_to_actual(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        play_selected = source.split("function playSelectedRecord", 1)[1].split("function returnToRecords", 1)[0]
        self.assertIn('view: "actual"', play_selected)
        self.assertIn('setView("actual")', play_selected)
        self.assertNotIn('view: "fixed"', play_selected)

    def test_fixed_ui_is_removed_but_hybrid_kinematic_priors_remain(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        drink = read_text("frontend/src/twin/HybridDrinkPose.ts")
        lateral = read_text("frontend/src/twin/HybridLateralTransferPose.ts")
        self.assertNotIn('{view === "fixed"', source)
        self.assertNotIn("data-driven-reconstruction-entry", source)
        self.assertIn("composeHybridDrinkPose(activeFrame", source)
        self.assertIn("composeHybridLateralTransferPose(activeFrame", source)
        self.assertIn("sampleDrinkReferencePose(normalizedPhase)", drink)
        self.assertIn("sampleLateralTransferFixedPose(normalizedPhase)", lateral)

    def test_actual_runtime_terms_are_removed_from_page(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        for text in ("runtimeSnapshot", "useRehabStore", "wsStatus", "SESSION_IDLE", "last_valid_frame", "ACTUAL_FRAME_STALE_MS"):
            self.assertNotIn(text, source)

    def test_actual_session_id_is_written_to_url(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn('setParams({ session_id: record.sessionId, view: "actual" }', source)
        self.assertIn('setParams({ session_id: selectedRecord.sessionId, view: "actual", rep: String(plan.rep.rep_index) }', source)

    def test_legacy_fixed_url_is_normalized_to_actual(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn('requestedView === "fixed"', source)
        self.assertIn('view: "actual"', source)
        self.assertNotIn('{view === "fixed"', source)

    def test_actual_back_returns_to_records(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        return_to_records = source.split("function returnToRecords", 1)[1].split("function resetActualPlayback", 1)[0]
        self.assertIn('setView("records")', return_to_records)
        self.assertIn("返回训练记录", source)

    def test_actual_load_clears_previous_session_cache(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        load_effect = source.split("fetchSessionResult(sessionId)", 1)[0]
        self.assertIn("setSessionDetail(null)", load_effect)
        self.assertIn("setTrajectories(new Map())", load_effect)
        self.assertIn('resetActualPlayback("all", 1)', load_effect)

    def test_actual_loads_each_valid_rep_trajectory(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("const validReps = detail.repetitions.filter((rep) => rep.valid)", source)
        self.assertIn("validReps.map(async (rep)", source)
        self.assertIn("fetchSessionTrajectory(sessionId, rep.rep_index)", source)

    def test_actual_does_not_fallback_to_demo_when_trajectory_missing(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn('setActualState("error")', source)
        self.assertIn("trajectory_not_found", source)
        actual_branch = source.split('{view === "actual"', 1)[1]
        self.assertNotIn("startFixedPlayback", actual_branch)

    def test_three_reps_render_buttons_from_plan(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("offlinePlan?.plans.map((plan)", source)
        self.assertIn("第{plan.rep.rep_index}次", source)
        self.assertNotIn("Array.from({ length: DEMO_REPETITION_COUNT }, (_, index) =>", source.split('{view === "actual"', 1)[1])

    def test_single_rep_playback_uses_selected_rep_plan_but_session_neutral(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn('startActualPlayback("rep", plan.rep.rep_index)', source)
        self.assertIn("singleRepPlaybackPlan", source)
        self.assertIn("const sessionNeutralFrame = offlinePlan ? resolveSessionNeutralFrame(offlinePlan) : null", source)

    def test_whole_group_playback_uses_all_valid_rep_plans(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn('startActualPlayback("all")', source)
        self.assertIn('actualScope === "all" ? "全部动作"', source)

    def test_playback_speed_only_changes_clock(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("value + (now - previous) * actualSpeed", source)
        self.assertNotIn("duration_ms * actualSpeed", source)

    def test_no_fake_frames_are_generated(self) -> None:
        source = read_text("frontend/src/twin/actualTrajectory.ts")
        self.assertIn("selected = item.frame", source)
        self.assertNotIn("newFrame", source)
        self.assertNotIn("interpolate", source)

    def test_emg_missing_stays_null_not_zero(self) -> None:
        source = read_text("frontend/src/twin/actualTrajectory.ts")
        self.assertIn("activation: value === null ? null", source)
        self.assertIn("activation: value === null ? null", source)

    def test_compensation_labels_are_per_rep(self) -> None:
        source = read_text("frontend/src/twin/actualTrajectory.ts")
        self.assertIn("COMPENSATION_LABELS", source)
        self.assertIn("rep.compensation?.detected", source)

    def test_actual_uses_session_saved_csv_copy_not_device_text(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("<dt>数据来源</dt><dd>已保存训练CSV", source)
        self.assertNotIn("Waiting for device", source)
        self.assertNotIn("real-time UDP", source)

    def test_presentation_cup_visibility_is_explicit(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        scene = read_text("frontend/src/twin/AnatomyScene.ts")
        self.assertIn("scene.setPresentationCupVisible(true)", page)
        self.assertIn("setPresentationCupVisible(visible: boolean)", scene)

    def test_reconstruction_user_visible_copy_is_chinese(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        for text in ("动作还原", "训练记录", "水杯拿取与饮水训练（二级）", "患者实际动作"):
            self.assertIn(text, source)
        for english in ("Action Reconstruction", "Training Records", "Drink water", "Fixed demo 5 reps", "返回标准示范"):
            self.assertNotIn(english, source)


if __name__ == "__main__":
    unittest.main()

