from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class ActualHighlighterMappingC2GStaticContractTests(unittest.TestCase):
    def test_highlighter_parent_and_child_timers_exist(self) -> None:
        scene = read_text("frontend/src/twin/AnatomyScene.ts")
        trace = read_text("frontend/src/twin/actualPerfTrace.ts")
        self.assertIn('measureActualPerfSync("highlighter_init_ms"', scene)
        self.assertIn('measureActualPerfSync("capture_material_states_ms"', scene)
        self.assertIn('measureActualPerfSync("highlighter_bind_ms"', scene)
        self.assertIn("capture_material_states_ms", trace)
        self.assertIn("highlighter_bind_ms", trace)

    def test_highlighter_call_order_is_preserved(self) -> None:
        scene = read_text("frontend/src/twin/AnatomyScene.ts")
        block = scene.split('measureActualPerfSync("highlighter_init_ms"', 1)[1].split('measureActualPerfSync("rig_init_ms"', 1)[0]
        self.assertLess(block.index("this.captureMaterialStates(result.meshes)"), block.index("this.highlighter.bind(result.meshes)"))

    def test_highlighter_bind_substage_timers_and_counters_exist(self) -> None:
        highlighter = read_text("frontend/src/twin/MuscleHighlighter.ts")
        trace = read_text("frontend/src/twin/actualPerfTrace.ts")
        for token in (
            "highlighter_bind_scan_ms",
            "highlighter_bind_mapping_ms",
            "highlighter_mesh_count",
            "highlighter_manifest_lookup_count",
            "highlighter_find_count",
            "highlighter_material_state_count",
            "highlighter_group_count",
        ):
            self.assertIn(token, highlighter + trace)

    def test_highlighter_bind_uses_one_pass_business_id_index(self) -> None:
        highlighter = read_text("frontend/src/twin/MuscleHighlighter.ts")
        self.assertIn("this.meshByBusinessId.clear()", highlighter)
        self.assertIn("const meshesByBusinessId = new Map<string, THREE.Mesh[]>()", highlighter)
        self.assertIn("for (const mesh of meshes)", highlighter)
        self.assertIn("meshesByBusinessId.set(businessId, [mesh])", highlighter)
        self.assertIn("const matched = meshesByBusinessId.get(item.businessId) ?? []", highlighter)
        self.assertIn("for (const item of anatomyManifest)", highlighter)
        self.assertIn("this.meshByBusinessId.set(item.businessId, matched)", highlighter)
        self.assertNotIn("meshes.filter", highlighter)
        self.assertNotIn("return matched", highlighter)

    def test_highlighter_bind_preserves_nullish_fallback_and_manifest_order(self) -> None:
        highlighter = read_text("frontend/src/twin/MuscleHighlighter.ts")
        self.assertIn("let businessId = explicitBusinessId", highlighter)
        self.assertIn("if (businessId === undefined || businessId === null)", highlighter)
        self.assertIn("const mapped = manifestItemForObjectName(mesh)", highlighter)
        self.assertIn("businessId = mapped?.businessId", highlighter)
        self.assertIn("for (const item of anatomyManifest)", highlighter)
        self.assertLess(highlighter.index("for (const mesh of meshes)"), highlighter.index("for (const item of anatomyManifest)"))

    def test_manifest_parent_and_substage_timers_exist(self) -> None:
        loader = read_text("frontend/src/twin/AnatomyModelLoader.ts")
        trace = read_text("frontend/src/twin/actualPerfTrace.ts")
        for token in (
            "manifest_mapping_ms",
            "manifest_spatial_mapping_pass_ms",
            "manifest_node_name_mapping_pass_ms",
            "manifest_right_bone_mesh_set_ms",
            "manifest_lookup_ms",
            "manifest_spatial_bounds_ms",
            "manifest_side_match_ms",
        ):
            self.assertIn(token, loader + trace)

    def test_manifest_work_counters_exist(self) -> None:
        loader = read_text("frontend/src/twin/AnatomyModelLoader.ts")
        trace = read_text("frontend/src/twin/actualPerfTrace.ts")
        for token in (
            "manifest_entry_count",
            "manifest_mesh_count",
            "manifest_name_lookup_count",
            "manifest_spatial_lookup_count",
            "manifest_find_scan_count",
            "manifest_bounds_count",
            "manifest_side_match_count",
        ):
            self.assertIn(token, loader + trace)

    def test_no_per_mesh_console_logging_added(self) -> None:
        for relative in (
            "frontend/src/twin/AnatomyScene.ts",
            "frontend/src/twin/AnatomyModelLoader.ts",
            "frontend/src/twin/MuscleHighlighter.ts",
        ):
            source = read_text(relative)
            self.assertNotIn("console.table", source)
            self.assertNotIn("for (const mesh of meshes) console", source)
            self.assertNotIn("meshes.forEach((mesh) => console", source)

    def test_model_cache_source_keeps_existing_cache_contract(self) -> None:
        cache = read_text("frontend/src/twin/cachedAnatomyModel.ts")
        self.assertIn("const parsedTemplateCache = new Map", cache)
        self.assertIn("parsedTemplateCache.get(url)", cache)
        self.assertIn("parsedTemplateCache.set(url, promise)", cache)
        self.assertIn("parsedTemplateCache.delete(url)", cache)
        self.assertIn("parsedTemplateState", cache)
        self.assertIn("modelTemplateCacheHit", cache)
        self.assertIn("modelTemplateInflight", cache)
        self.assertIn("SkeletonUtils.clone(root)", cache)
        self.assertIn("setActualPerfCacheState", cache)
        self.assertNotIn("highlighter_bind_ms", cache)

    def test_c2c_first_rep_trajectory_logic_is_unchanged(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        actual_load_effect = page.split("fetchSessionResult(sessionId)", 1)[1].split("const selectedRepTrajectoryLoaded", 1)[0]
        self.assertIn("const firstRep = chooseInitialRep(validReps, requestedRep)", actual_load_effect)
        self.assertIn("fetchSessionTrajectory(sessionId, firstRep)", actual_load_effect)
        self.assertIn(".filter((rep) => rep.rep_index !== firstRep)", actual_load_effect)
        self.assertNotIn("await Promise.all", actual_load_effect)

    def test_perf_summary_remains_single_aggregate_log(self) -> None:
        trace = read_text("frontend/src/twin/actualPerfTrace.ts")
        self.assertIn("[REHAB_ACTUAL_PERF]", trace)
        self.assertEqual(trace.count('console.log("[REHAB_ACTUAL_PERF]"'), 1)


if __name__ == "__main__":
    unittest.main()
