from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class AnatomyModelCacheC2EStaticContractTests(unittest.TestCase):
    def test_model_status_url_resolution_is_cached_and_retryable(self) -> None:
        source = read_text("frontend/src/twin/cachedAnatomyModel.ts")
        self.assertIn("let resolvedModelUrlPromise", source)
        self.assertIn('fetch("/api/model/status")', source)
        self.assertIn('"/models/rehab_upper_limb_rigged.glb"', source)
        self.assertIn('"/models/body.glb"', source)
        self.assertIn("resolvedModelUrlPromise = null", source)

    def test_draco_loader_is_shared_and_preloaded(self) -> None:
        source = read_text("frontend/src/twin/cachedAnatomyModel.ts")
        self.assertIn("let sharedDracoLoader", source)
        self.assertIn("new DRACOLoader()", source)
        self.assertIn('setDecoderPath("/draco/")', source)
        self.assertIn(".preload()", source)
        loader = read_text("frontend/src/twin/AnatomyModelLoader.ts")
        self.assertNotIn("new DRACOLoader", loader)

    def test_parsed_template_cache_is_keyed_by_url_and_dedups_inflight(self) -> None:
        source = read_text("frontend/src/twin/cachedAnatomyModel.ts")
        self.assertIn("const parsedTemplateCache = new Map<string, Promise<ParsedAnatomyTemplate>>()", source)
        self.assertIn("const cached = parsedTemplateCache.get(url)", source)
        self.assertIn("if (cached) return cached", source)
        self.assertIn("parsedTemplateCache.set(url, promise)", source)
        self.assertIn("parsedTemplateCache.delete(url)", source)

    def test_cache_hit_still_returns_distinct_scene_instance(self) -> None:
        source = read_text("frontend/src/twin/cachedAnatomyModel.ts")
        self.assertIn("instantiateCachedAnatomyModel", source)
        self.assertIn("cloneTemplateRoot(template.gltf.scene)", source)
        self.assertNotIn("scene.add(template.gltf.scene)", source)
        self.assertNotIn("return template.gltf.scene", source)

    def test_skeleton_safe_clone_path_is_used(self) -> None:
        source = read_text("frontend/src/twin/cachedAnatomyModel.ts")
        self.assertIn('three/examples/jsm/utils/SkeletonUtils.js', source)
        self.assertIn("SkeletonUtils.clone(root)", source)
        self.assertNotIn(".clone(true)", source)

    def test_instance_owns_disposable_geometry_materials_and_textures(self) -> None:
        source = read_text("frontend/src/twin/cachedAnatomyModel.ts")
        self.assertIn("mesh.geometry = mesh.geometry.clone()", source)
        self.assertIn("cloneMaterialLike(mesh.material)", source)
        self.assertIn("cloneMaterialWithTextures", source)
        self.assertIn("value instanceof THREE.Texture", source)
        self.assertIn("fields[key] = value.clone()", source)

    def test_anatomy_load_result_contract_remains_present(self) -> None:
        loader = read_text("frontend/src/twin/AnatomyModelLoader.ts")
        for field in (
            "root",
            "meshes",
            "bones",
            "originalMaterials",
            "meshBusinessIds",
            "nodeNames",
            "missingBusinessIds",
            "rightBoneMeshSets",
            "box",
            "sphere",
            "hasDraco",
        ):
            self.assertIn(field, loader)
        self.assertIn("instantiateCachedAnatomyModel(url, onProgress)", loader)

    def test_records_view_triggers_best_effort_prewarm_without_await(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("prewarmAnatomyModel", page)
        prewarm_effect = page.split("void prewarmAnatomyModel()", 1)[0].rsplit("useEffect(() =>", 1)[1]
        self.assertIn('view !== "records"', prewarm_effect)
        self.assertIn("records.some((record) => record.playable)", prewarm_effect)
        self.assertNotIn("await prewarmAnatomyModel", prewarm_effect)
        self.assertIn("void prewarmAnatomyModel()", page)

    def test_anatomy_scene_is_still_new_per_actual_and_disposed_per_exit(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        scene_effect = page.split("const scene = new AnatomyScene", 1)[0].rsplit("useEffect(() =>", 1)[1]
        self.assertIn('view !== "actual"', scene_effect)
        self.assertIn("actualSceneContainerRef.current", scene_effect)
        cleanup = page.split("const scene = new AnatomyScene", 1)[1].split("}, [view, selectedMotionFamily, selectedObjectType])", 1)[0]
        self.assertIn("scene.dispose()", cleanup)
        scene = read_text("frontend/src/twin/AnatomyScene.ts")
        self.assertIn("new THREE.WebGLRenderer", scene)
        self.assertNotIn("let globalAnatomyScene", scene)

    def test_c2c_first_rep_trajectory_logic_is_unchanged(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        actual_load_effect = page.split("fetchSessionResult(sessionId)", 1)[1].split("const selectedRepTrajectoryLoaded", 1)[0]
        self.assertIn("const firstRep = chooseInitialRep(validReps, requestedRep)", actual_load_effect)
        self.assertIn("fetchSessionTrajectory(sessionId, firstRep)", actual_load_effect)
        self.assertIn(".filter((rep) => rep.rep_index !== firstRep)", actual_load_effect)
        self.assertNotIn("await Promise.all", actual_load_effect)

    def test_trajectory_and_kinematics_files_are_not_part_of_model_cache_patch(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("composeHybridDrinkPose(activeFrame, activeNormalizedPhase, sessionNeutralFrame)", page)
        self.assertIn("composeHybridLateralTransferPose(activeFrame, activeNormalizedPhase, sessionNeutralFrame)", page)
        self.assertIn("fetchSessionTrajectory(sessionId, firstRep)", page)
        self.assertNotIn("sampleDrinkReferencePose(", page)


if __name__ == "__main__":
    unittest.main()
