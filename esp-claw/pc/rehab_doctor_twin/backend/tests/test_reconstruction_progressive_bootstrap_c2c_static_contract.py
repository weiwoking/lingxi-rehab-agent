from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def effect_containing(source: str, anchor: str, end_marker: str) -> str:
    anchor_index = source.index(anchor)
    effect_start = source.rfind("useEffect(() => {", 0, anchor_index)
    effect_end = source.index(end_marker, anchor_index)
    if effect_start < 0 or effect_end <= anchor_index:
        raise AssertionError(f"Could not isolate useEffect for anchor: {anchor}")
    return source[effect_start:effect_end]


class ReconstructionProgressiveBootstrapC2CStaticContractTests(unittest.TestCase):
    def test_actual_bootstrap_no_longer_awaits_all_rep_trajectories(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        actual_load_effect = source.split("fetchSessionResult(sessionId)", 1)[1].split("const selectedRepTrajectoryLoaded", 1)[0]
        self.assertNotIn("await Promise.all", actual_load_effect)
        self.assertNotIn("Promise.all(validReps.map", actual_load_effect)
        self.assertIn("const firstRep = chooseInitialRep(validReps, requestedRep)", actual_load_effect)
        self.assertIn("fetchSessionTrajectory(sessionId, firstRep)", actual_load_effect)

    def test_remaining_reps_are_background_prefetch_not_first_ready_gate(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        actual_load_effect = source.split("fetchSessionResult(sessionId)", 1)[1].split("const selectedRepTrajectoryLoaded", 1)[0]
        first_ready_index = actual_load_effect.index('setActualState("ready")')
        background_index = actual_load_effect.index(".filter((rep) => rep.rep_index !== firstRep)")
        self.assertLess(first_ready_index, background_index)
        self.assertIn("Background prefetch must not fail the visible first repetition.", actual_load_effect)
        self.assertNotIn("await Promise.allSettled", actual_load_effect)

    def test_scene_creation_is_not_blocked_by_offline_plan_or_trajectories(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        scene_effect = source.split("const scene = new AnatomyScene", 1)[0].rsplit("useEffect(() =>", 1)[1]
        self.assertIn('view !== "actual"', scene_effect)
        self.assertIn("actualSceneContainerRef.current", scene_effect)
        self.assertIn("selectedMotionFamily", scene_effect)
        self.assertIn("selectedObjectType", scene_effect)
        self.assertNotIn("offlinePlan", scene_effect)
        self.assertNotIn("trajectories", scene_effect)
        self.assertNotIn("sessionDetail", scene_effect)

    def test_current_rep_lazy_load_is_present(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        selected_rep_effect = source.split("const selectedRepTrajectoryLoaded", 1)[1].split("useEffect(() => {\n    if (actualScope !== \"all\"", 1)[0]
        self.assertIn('actualScope !== "rep"', selected_rep_effect)
        self.assertIn("fetchSessionTrajectory(sessionId, actualRepIndex)", selected_rep_effect)
        self.assertIn("selectedRepTrajectoryLoaded", selected_rep_effect)
        self.assertIn("trajectory_unavailable", selected_rep_effect)

    def test_rep_picker_selects_without_starting_unloaded_playback(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        select_function = source.split("function selectActualRep", 1)[1].split("\n\n  function seekActual", 1)[0]
        self.assertIn('resetActualPlayback("rep", repIndex)', select_function)
        self.assertIn("trajectories.get(repIndex)", select_function)
        self.assertIn('setActualState("loading")', select_function)
        self.assertNotIn('setActualState("playing")', select_function)
        self.assertNotIn("setActualPlaying(true)", select_function)

    def test_existing_detail_and_trajectory_cache_are_reused(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        api = read_text("frontend/src/services/sessionResultsApi.ts")
        self.assertIn("fetchSessionResult(sessionId)", page)
        self.assertIn("fetchSessionTrajectory(sessionId, firstRep)", page)
        self.assertIn("const detailCache = new Map", api)
        self.assertIn("const detailInflight = new Map", api)
        self.assertIn("const trajectoryCache = new Map", api)
        self.assertIn("const trajectoryInflight = new Map", api)
        self.assertIn("if (inflight) return inflight", api)

    def test_async_stale_state_guards_remain_present(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        actual_load_effect = effect_containing(
            source,
            "fetchSessionResult(sessionId)",
            "const selectedRepTrajectoryLoaded",
        )
        selected_rep_effect = effect_containing(
            source,
            "fetchSessionTrajectory(sessionId, actualRepIndex)",
            'useEffect(() => {\n    if (actualScope !== "all"',
        )
        self.assertIn("let cancelled = false", actual_load_effect)
        self.assertIn("if (cancelled) return", actual_load_effect)
        self.assertIn("cancelled = true", actual_load_effect)
        self.assertIn("let cancelled = false", selected_rep_effect)
        self.assertIn("if (cancelled) return", selected_rep_effect)
        self.assertIn("cancelled = true", selected_rep_effect)

    def test_zero_rep_behavior_and_object_routing_are_preserved(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("if (!validReps.length)", source)
        self.assertIn("该Session没有可用于动作还原的有效动作轨迹。", source)
        self.assertIn("scene.setPresentationObjectMode(selectedObjectType)", source)
        self.assertIn("objectTypeForSession", source)
        self.assertIn("ball", source)
        self.assertIn("cup", source)

    def test_kinematic_pose_functions_are_not_changed_or_replaced(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("composeHybridDrinkPose(activeFrame, activeNormalizedPhase, sessionNeutralFrame)", source)
        self.assertIn("composeHybridLateralTransferPose(activeFrame, activeNormalizedPhase, sessionNeutralFrame)", source)
        self.assertNotIn("sampleDrinkFixedPose(", source)
        self.assertNotIn("sampleLateralTransferFixedPose(", source)


if __name__ == "__main__":
    unittest.main()
