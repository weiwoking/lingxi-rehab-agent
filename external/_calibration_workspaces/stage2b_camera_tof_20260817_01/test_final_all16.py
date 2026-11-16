from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

import numpy as np

import advanced_diagnostics as advanced
import final_all16_analysis as final
import stage2b_camera_tof as core


class FinalAll16Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.obs = core.load_json(core.OBS_PATH)
        cls.analysis = core.load_json(final.ANALYSIS_PATH)
        cls.candidate = core.load_json(final.CANDIDATE_PATH)
        cls.layers, cls.classification = final.rows_and_layers(cls.obs)

    def test_current_train_heldout_split_regression(self) -> None:
        train, held = core.split_poses(self.obs)
        self.assertEqual(held, {"S2B_004", "S2B_005", "S2B_008", "S2B_016"})
        self.assertEqual(len(final.select_pose(self.layers["broad"], train)), 7809)
        self.assertEqual(len(final.select_pose(self.layers["broad"], held)), 2665)
        self.assertEqual({r["pose_id"] for r in final.select_pose(self.layers["broad"], {"S2B_016"})}, {"S2B_016"})

    def test_final_refit_uses_exactly_all_16_poses(self) -> None:
        expected = [f"S2B_{i:03d}" for i in range(1, 17)]
        self.assertEqual(self.candidate["provenance"]["source_pose_ids"], expected)
        self.assertTrue(self.candidate["provenance"]["all_16_poses_used_for_final_fit"])
        self.assertEqual(self.candidate["provenance"]["final_fit_observation_count"], 9906)
        self.assertEqual({r["pose_id"] for r in self.layers["core"]}, set(expected))

    def test_core_filter_is_frozen_and_correct(self) -> None:
        metadata, core_keys = advanced.observation_metadata(self.obs)
        self.assertEqual(len(self.layers["core"]), len(core_keys))
        self.assertTrue(all(metadata[advanced.row_key(r)]["likely_reference_plane"] is True for r in self.layers["core"]))
        self.assertTrue(all(metadata[advanced.row_key(r)]["strict_valid"] for r in self.layers["strict_core"]))
        self.assertTrue(set(map(advanced.row_key, self.layers["strict_core"])).issubset(core_keys))
        self.assertIn("no reclassification", self.classification["classification"])

    def test_broad_core_metrics_not_mixed(self) -> None:
        chosen = self.analysis["variants"]["R3_core_square_centered"]
        self.assertEqual(chosen["all16_core_metrics"]["count"], 9906)
        self.assertEqual(chosen["all16_all_broad_metrics"]["count"], 10474)
        self.assertEqual(chosen["all16_outside_background_metrics"]["count"], 568)
        self.assertGreater(chosen["all16_all_broad_metrics"]["max_mm"], 1000)
        self.assertLess(chosen["all16_core_metrics"]["max_mm"], 30)

    def test_scaled_svd_finds_synthetic_known_null(self) -> None:
        x = np.linspace(-1, 1, 50)
        jac = np.column_stack((x, 2 * x, np.ones_like(x)))
        singular = np.linalg.svd(jac, compute_uv=False)
        rank = int(np.sum(singular > singular[0] * 1e-10))
        self.assertEqual(rank, 2)
        self.assertLess(singular[-1], singular[0] * 1e-12)

    def test_reduced_model_synthetic_recovery(self) -> None:
        truth = np.array([0.02, -0.015, 0.03, -12.0, 25.0, -3.0,
                          math.log(10.0), math.log(10.0), 4.0, 4.0])
        rays = core.tof_rays(truth); rotation, translation = core.params_to_transform(truth.tolist())
        rng = np.random.default_rng(20260818); rows = []
        for i in range(384):
            raw = i % 64; distance = 350.0 + 700.0 * ((i // 64) / 5.0)
            point = rotation @ (rays[raw] * (distance / rays[raw, 2])) + translation
            normal = rng.normal(size=3); normal[2] = abs(normal[2]) + 0.25; normal /= np.linalg.norm(normal)
            rows.append({"pose_id": f"P{i//64}", "scan_id": f"Q{i//64}", "frame_id": f"F{i}",
                         "raw_index": raw, "distance_mm": distance, "sigma_mm": 1.0,
                         "normal": normal, "offset": -float(normal @ point), "base_weight": 1.0})
        initial = truth.copy(); initial[:6] += np.array([.005, -.004, .003, 2, -2, 1])
        initial[6:8] += .02
        fit = final.robust_optimize(rows, "square_centered", initial, 30)
        p = np.asarray(fit["params"])
        self.assertAlmostEqual(p[6], p[7], places=12)
        self.assertEqual(p[8], 4.0); self.assertEqual(p[9], 4.0)
        self.assertLess(final.physical_metrics(p, rows)["rms_mm"], 0.2)

    def test_raw_mapping_is_deterministic(self) -> None:
        params = self.analysis["variants"]["R3_core_square_centered"]["all16_fit"]["params"]
        a = final.raw_mapping(params); b = final.raw_mapping(params)
        self.assertEqual(a, b); self.assertEqual(len(a), 64)
        self.assertEqual([x["raw_index"] for x in a], list(range(64)))
        stored = self.candidate["raw_zone_camera_mapping"]
        self.assertTrue(np.allclose([x["camera_frame_center_ray"] for x in a],
                                    [x["camera_frame_center_ray"] for x in stored], atol=0, rtol=0))

    def test_d4_gauge_reparameterization_preserves_mapping_authority(self) -> None:
        gauge = core.load_json(core.WORKSPACE / "advanced_diagnostics" / "gauge_equivalence_summary.json")
        pair = next(x for x in gauge["pairwise"] if x["orientation_a"] == "identity" and x["orientation_b"] == "rotate_90_cw")
        self.assertEqual(gauge["physical_D4_orientation"], "UNRESOLVED_GAUGE")
        self.assertEqual(gauge["camera_frame_raw_zone_ray_mapping"], "OBSERVABLE_AUTHORITY")
        self.assertLess(pair["angular_difference_deg"]["p95"], 0.01)
        self.assertLess(pair["by_distance_mm"]["600"]["pixel_displacement"]["p95"], 0.1)

    def test_candidate_stays_in_workspace_and_never_promotes(self) -> None:
        workspace = core.WORKSPACE.resolve()
        for path in (final.CANDIDATE_PATH, final.FR_PATH, final.ANALYSIS_PATH):
            self.assertTrue(path.resolve().is_relative_to(workspace))
        self.assertFalse(self.candidate["promotion"])
        self.assertEqual(self.candidate["status"], "CANDIDATE_NOT_PROMOTED")
        self.assertFalse(core.load_json(final.FR_PATH)["runtime_replaced"])

    def test_protected_hashes_unchanged(self) -> None:
        check = core.load_json(final.OUT / "protected_hash_check.final.json")
        self.assertTrue(check["unchanged"])
        self.assertEqual(check["before_file_count"], check["after_file_count"])
        self.assertEqual(final.sha256_file(core.CAMERA_CANDIDATE), core.CAMERA_CANDIDATE_SHA256)


if __name__ == "__main__":
    unittest.main(verbosity=2)
