from __future__ import annotations

import copy
import math
import unittest

import cv2
import numpy as np

import advanced_diagnostics as advanced
import stage2b_camera_tof as core
from test_stage2b_toolchain import synthetic_observations


class GaugeEquivalenceTests(unittest.TestCase):
    def solutions(self):
        params_a = core.default_params(); params_a[3:6] = [-18.0, 4.0, 6.0]
        base_a = {"orientation": "identity", "range_semantics": "axial_z",
                  "model": "B_low_dimensional", "params": params_a.tolist()}
        base_b = {"orientation": "rotate_90_cw", "range_semantics": "axial_z",
                  "model": "B_low_dimensional", "params": params_a.tolist()}
        raw_a, _ = advanced.raw_rays(base_a); raw_b, _ = advanced.raw_rays(base_b)
        u, _, vt = np.linalg.svd(raw_b.T @ raw_a)
        row_rotation = u @ vt
        if np.linalg.det(row_rotation) < 0:
            u[:, -1] *= -1; row_rotation = u @ vt
        rotation_b = row_rotation.T
        rvec, _ = cv2.Rodrigues(rotation_b)
        params_b = params_a.copy(); params_b[:3] = rvec.reshape(3)
        base_b["params"] = params_b.tolist()
        return base_a, base_b

    def test_synthetic_pure_d4_reparameterization_is_camera_equivalent(self):
        a, b = self.solutions(); comparison = advanced.compare_mapping(a, b)
        self.assertLess(comparison["angular_difference_deg"]["max"], 1e-5)
        for distance in advanced.DISTANCES:
            self.assertLess(comparison["by_distance_mm"][str(distance)]["pixel_displacement"]["max"], 1e-4)

    def test_intentionally_non_equivalent_ray_model_is_detected(self):
        a, b = self.solutions(); b = copy.deepcopy(b); b["params"][6] += math.log(1.18)
        comparison = advanced.compare_mapping(a, b)
        self.assertGreater(comparison["angular_difference_deg"]["p95"], .2)
        self.assertGreater(comparison["by_distance_mm"]["600"]["pixel_displacement"]["p95"], 1.0)


class SemanticsAndProvenanceTests(unittest.TestCase):
    def semantics_case(self, semantics: str):
        obs, true = synthetic_observations(orientation="flip_horizontal", semantics=semantics,
                                           seed=83 if semantics == "axial_z" else 89)
        selected = {"orientation": "flip_horizontal", "range_semantics": semantics,
                    "model": "B_low_dimensional", "params": true.tolist()}
        doc, _, _, _ = advanced.semantics_diagnostics(obs, selected, iterations=6,
                                                       subset_trials=3, seed=20260817)
        return doc

    def test_known_axial_synthetic_wins_most_lopo_and_subsets(self):
        doc = self.semantics_case("axial_z")
        self.assertGreater(doc["lopo_summary"]["axial_z"], doc["lopo_summary"]["radial_range"])
        self.assertGreaterEqual(doc["subset_summary"]["axial_z"], 2)

    def test_known_radial_synthetic_wins_most_lopo_and_subsets(self):
        doc = self.semantics_case("radial_range")
        self.assertGreater(doc["lopo_summary"]["radial_range"], doc["lopo_summary"]["axial_z"])
        self.assertGreaterEqual(doc["subset_summary"]["radial_range"], 2)

    def test_provenance_core_and_broad_layers_do_not_mix(self):
        obs, true = synthetic_observations(orientation="identity", semantics="axial_z", seed=91)
        for pose in obs["poses"]:
            for frame in pose["frames"]:
                for zone in frame["tof_zones"]:
                    if zone["broad_valid"]:
                        zone["likely_reference_plane"] = True
                        zone["likely_outside_or_background"] = False
        victim = next(zone for zone in obs["poses"][0]["frames"][0]["tof_zones"] if zone["broad_valid"])
        victim["distance_mm"] += 600.0; victim["likely_reference_plane"] = False
        victim["likely_outside_or_background"] = True
        solution = {"orientation": "identity", "range_semantics": "axial_z",
                    "model": "B_low_dimensional", "params": true.tolist()}
        doc, rows, _ = advanced.provenance_diagnostics(obs, solution)
        layers = doc["metric_layers"]
        self.assertEqual(layers["all_broad_robustness_metrics"]["count"], len(rows))
        self.assertEqual(layers["core_reference_plane_metrics"]["count"], len(rows) - 1)
        self.assertEqual(layers["outside_background"]["count"], 1)
        self.assertFalse(doc["observation_usage_audit"]["runtime_broad_policy_modified"])


class DynamicAndReadOnlyTests(unittest.TestCase):
    def test_subset_generation_has_no_n15_n16_n20_hardcode(self):
        for count in (15, 16, 20):
            ids = [f"P_{i:02d}" for i in range(count)]
            subsets = advanced.subset_pose_sets(ids, .8, 7, 20260817)
            self.assertEqual(len(subsets), 7)
            self.assertEqual(len({tuple(sorted(value)) for value in subsets}), 7)
            self.assertTrue(all(len(value) == math.ceil(.8 * count) for value in subsets))

    def test_protected_input_hash_scope_is_stable(self):
        before = advanced.protected_hashes(); after = advanced.protected_hashes()
        self.assertEqual(before, after)
        self.assertIn("workspace_manifest.json", before)
        self.assertIn("observations/observations.stage2b.json", before)

    def test_coverage_uses_truthful_angle_and_timing_units(self):
        obs = core.get_observations(); coverage = core.coverage_document(obs)
        self.assertIn("rms_deg", coverage["plane_angle_deg"]["yaw"])
        self.assertIn("median_deg", coverage["plane_angle_deg"]["combined_tilt"])
        self.assertIn("rms_us", coverage["pair_delta_us"])
        self.assertIn("p95_us", coverage["pair_delta_us"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
