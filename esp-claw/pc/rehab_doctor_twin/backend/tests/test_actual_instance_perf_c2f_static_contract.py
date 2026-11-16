from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class ActualInstancePerfC2FStaticContractTests(unittest.TestCase):
    def test_perf_trace_outputs_single_summary_block(self) -> None:
        source = read_text("frontend/src/twin/actualPerfTrace.ts")
        self.assertIn("[REHAB_ACTUAL_PERF]", source)
        self.assertIn("console.log", source)
        self.assertIn("activeTrace.emitted = true", source)
        self.assertNotIn("console.table", source)

    def test_click_to_scene_timestamps_are_recorded(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        trace = read_text("frontend/src/twin/actualPerfTrace.ts")
        self.assertIn("startActualPerfTrace", page)
        self.assertIn("markActualPerf(\"AnatomyScene_construct_start\")", page)
        self.assertIn("markActualPerf(\"AnatomyScene_construct_end\")", page)
        self.assertIn("markActualPerf(\"actual_ready_timestamp\")", page)
        self.assertIn("pre_scene_delay_ms", trace)
        self.assertIn("post_scene_delay_ms", trace)
        self.assertIn("total_ms", trace)

    def test_cache_state_is_logged_without_changing_cache_policy(self) -> None:
        cache = read_text("frontend/src/twin/cachedAnatomyModel.ts")
        trace = read_text("frontend/src/twin/actualPerfTrace.ts")
        self.assertIn("setActualPerfCacheState", cache)
        self.assertIn("modelStatusCacheHit", cache)
        self.assertIn("modelTemplateCacheHit", cache)
        self.assertIn("modelTemplateInflight", cache)
        self.assertIn("model_status_cache_hit", trace)
        self.assertIn("model_template_cache_hit", trace)
        self.assertIn("model_template_inflight", trace)

    def test_material_double_clone_is_present_and_both_stages_are_instrumented(self) -> None:
        cache = read_text("frontend/src/twin/cachedAnatomyModel.ts")
        loader = read_text("frontend/src/twin/AnatomyModelLoader.ts")
        trace = read_text("frontend/src/twin/actualPerfTrace.ts")
        self.assertIn("material_clone_stage1_ms", cache)
        self.assertIn("material_clone_stage2_ms", loader)
        self.assertIn("material_clone_stage1_ms", trace)
        self.assertIn("material_clone_stage2_ms", trace)
        self.assertIn("mesh.material = cloned", loader)

    def test_geometry_clone_is_measured_with_counts(self) -> None:
        cache = read_text("frontend/src/twin/cachedAnatomyModel.ts")
        trace = read_text("frontend/src/twin/actualPerfTrace.ts")
        self.assertIn("mesh.geometry = mesh.geometry.clone()", cache)
        self.assertIn("geometry_clone_ms", cache)
        self.assertIn("geometry_clone_count", cache)
        self.assertIn("geometry_count", trace)

    def test_scene_constructor_and_ready_phases_are_instrumented(self) -> None:
        scene = read_text("frontend/src/twin/AnatomyScene.ts")
        trace = read_text("frontend/src/twin/actualPerfTrace.ts")
        for phase in (
            "renderer_scene_init_ms",
            "camera_controls_setup_ms",
            "scene_static_setup_ms",
            "animation_setup_ms",
            "scene_add_ms",
            "rig_init_ms",
            "highlighter_init_ms",
            "object_model_setup_ms",
        ):
            self.assertIn(phase, scene)
            self.assertIn(phase, trace)
        self.assertIn("finishActualPerfTrace(\"actual_scene_ready\")", scene)

    def test_no_page_switch_or_c2c_first_rep_strategy_change(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        actual_load_effect = page.split("fetchSessionResult(sessionId)", 1)[1].split("const selectedRepTrajectoryLoaded", 1)[0]
        self.assertIn("const firstRep = chooseInitialRep(validReps, requestedRep)", actual_load_effect)
        self.assertIn("fetchSessionTrajectory(sessionId, firstRep)", actual_load_effect)
        self.assertIn(".filter((rep) => rep.rep_index !== firstRep)", actual_load_effect)
        self.assertNotIn("await Promise.all", actual_load_effect)
        self.assertNotIn("setTimeout(() => setView", page)

    def test_kinematics_trajectory_and_object_routing_are_not_replaced(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("composeHybridDrinkPose(activeFrame, activeNormalizedPhase, sessionNeutralFrame)", page)
        self.assertIn("composeHybridLateralTransferPose(activeFrame, activeNormalizedPhase, sessionNeutralFrame)", page)
        self.assertIn("scene.setPresentationObjectMode(selectedObjectType)", page)
        self.assertIn("fetchSessionTrajectory(sessionId, firstRep)", page)
        self.assertNotIn("worker", page.lower())


if __name__ == "__main__":
    unittest.main()
