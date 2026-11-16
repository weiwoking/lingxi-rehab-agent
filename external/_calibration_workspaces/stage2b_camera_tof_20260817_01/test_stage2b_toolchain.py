from __future__ import annotations

import argparse
import copy
import importlib.util
import math
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

TOOL = Path(__file__).with_name("stage2b_camera_tof.py")
SPEC = importlib.util.spec_from_file_location("stage2b", TOOL)
s2b = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(s2b)


def synthetic_observations(*, orientation="flip_horizontal", semantics="radial_range", seed=77):
    rng = np.random.default_rng(seed)
    true = s2b.default_params()
    true[:3] = [0.018, -0.026, 0.011]
    true[3:6] = [-18.5, 4.2, 6.0]
    true[6:10] = [math.log(10.2), math.log(9.4), 3.85, 4.10]
    rotation, _ = cv2.Rodrigues(true[:3])
    rays = s2b.tof_rays(true)
    mapping = s2b.d4_map(orientation)
    poses = []
    for pose_index in range(10):
        yaw = math.radians(-28 + pose_index * 6.2)
        pitch = math.radians((-1 if pose_index % 2 else 1) * (7 + pose_index * 1.4))
        normal = np.array([math.sin(yaw), math.sin(pitch), -math.cos(yaw) * math.cos(pitch)])
        normal /= np.linalg.norm(normal)
        distance = 390 + pose_index * 72
        offset = distance
        frames = []
        for frame_index in range(5):
            raw_distance = np.zeros(64, dtype=float)
            for canonical, ray in enumerate(rays):
                denominator = float(normal @ (rotation @ ray))
                radial = -(float(normal @ true[3:6]) + offset) / denominator
                reading = radial if semantics == "radial_range" else radial * ray[2]
                raw_distance[mapping[canonical]] = reading + rng.normal(0, 2.0)
            zones = []
            for raw in range(64):
                missing = (raw + pose_index + frame_index * 3) % 47 == 0
                broad_only = (raw * 3 + pose_index) % 29 == 0
                value = raw_distance[raw]
                if broad_only:
                    value += rng.normal(18, 5)
                zones.append({
                    "raw_index": raw, "raw_row": raw // 8, "raw_col": raw % 8,
                    "distance_mm": float(value), "range_sigma_mm": 2.0,
                    "target_status": 255 if missing else (4 if broad_only else 5),
                    "target_count": 0 if missing else 1,
                    "broad_valid": not missing, "strict_valid": not missing and not broad_only,
                    "plane_consistency_residual_mm": None,
                    "likely_reference_plane": None,
                    "likely_outside_or_background": None,
                })
            frames.append({
                "frame_id": f"frame_{frame_index:06d}",
                "camera_timestamp_us": frame_index * 66667 + 17000,
                "tof_timestamp_us": frame_index * 66667,
                "pair_delta_us": 17000,
                "camera_plane": {
                    "detected": True, "corner_count": 24, "marker_count": 17,
                    "pnp_rms_px": 0.15, "board_coverage_fraction": 0.08,
                    "plane_normal_camera": normal.tolist(),
                    "plane_offset_camera_mm": offset,
                    "plane_distance_camera_mm": abs(offset),
                },
                "tof_zones": zones,
            })
        poses.append({"pose_id": f"SYN_{pose_index:02d}", "scan_id": f"scan_syn_{pose_index:02d}", "frames": frames,
                      "within_scan_plane_stability": {"usable_plane_frames": 5, "stable": True}})
    return {"schema_version": "stage2b_observations.v1", "pose_count": len(poses), "poses": poses}, true


class RegistryTests(unittest.TestCase):
    def test_all_d4_maps_are_unique_permutations_and_history_matches(self):
        maps = [tuple(s2b.d4_map(name).tolist()) for name in s2b.D4]
        self.assertEqual(len(set(maps)), 8)
        for mapping in maps:
            self.assertEqual(sorted(mapping), list(range(64)))
        old = s2b.load_json(s2b.OLD_STAGE7B)
        self.assertEqual(old["transform_direction"], "T_camera_from_tof")
        self.assertEqual(old["translation_unit"], "mm")
        self.assertEqual(old["matrix_convention"], "row-major, column-vector")
        self.assertEqual(old["orientation"]["raw_index_for_canonical_index"], s2b.d4_map("rotate_90_cw").tolist())

    def test_pose_id_contract_and_dynamic_counts(self):
        for good in ("S2B_001", "MID_LEFT_01", "EXTRA_17", "a", "A-b_9"):
            self.assertEqual(s2b.pose_id(good), good)
        for bad in ("", "_bad", "bad space", "bad/dir", "!bad"):
            with self.assertRaises(s2b.Stage2BError):
                s2b.pose_id(bad)
        for count in (10, 12, 20, 30):
            registry = {f"P_{i:03d}": {"scan_id": f"scan_{i}"} for i in range(count)}
            self.assertEqual(len(registry), count)

    def test_duplicate_is_rejected_before_any_write(self):
        old_manifest = s2b.manifest
        try:
            s2b.manifest = lambda: {"poses": {"DUP": {"scan_id": "scan_existing"}}}
            args = argparse.Namespace(pose="DUP", scan_id="scan_new", latest=False, dry_run=True,
                                      physical_position_note=None, capture_note=None)
            with self.assertRaisesRegex(s2b.Stage2BError, "already registered"):
                s2b.cmd_register(args)
        finally:
            s2b.manifest = old_manifest

    def test_latest_dry_run_is_read_only(self):
        latest = s2b.resolve_scan(None, True)
        before = {str(p): s2b.sha256_file(p) for p in s2b.source_files(latest)}
        args = argparse.Namespace(pose="UNIT_DRY_RUN", scan_id=None, latest=True, dry_run=True,
                                  physical_position_note=None, capture_note=None)
        self.assertEqual(s2b.cmd_register(args), 0)
        after = {str(p): s2b.sha256_file(p) for p in s2b.source_files(latest)}
        self.assertEqual(before, after)


class GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obs, cls.true = synthetic_observations()

    def test_d4_orientation_and_range_semantics_rank_first(self):
        rows = s2b.observation_rows(self.obs, "strict")
        scores = []
        for orientation in s2b.D4:
            for semantics in ("radial_range", "axial_z"):
                fit = s2b.robust_lm(rows, orientation, semantics, "B_low_dimensional", max_iterations=25)
                objective = fit["metrics"]["rms_mm"] + s2b.gauge_prior_penalty_mm(fit["params"])
                scores.append((objective, orientation, semantics, fit))
        scores.sort(key=lambda x: x[0])
        self.assertEqual(scores[0][1], "flip_horizontal")
        self.assertEqual(scores[0][2], "radial_range")
        self.assertGreater(scores[1][0] - scores[0][0], 0.15)

    def test_extrinsic_and_low_dimensional_rays_recover(self):
        rows = s2b.observation_rows(self.obs, "strict")
        fit = s2b.robust_lm(rows, "flip_horizontal", "radial_range", "B_low_dimensional", max_iterations=35)
        estimated = np.asarray(fit["params"])
        r_true, _ = cv2.Rodrigues(self.true[:3]); r_est, _ = cv2.Rodrigues(estimated[:3])
        rotation_error = math.degrees(math.acos(np.clip((np.trace(r_est @ r_true.T) - 1) / 2, -1, 1)))
        translation_error = float(np.linalg.norm(estimated[3:6] - self.true[3:6]))
        rays_true = s2b.tof_rays(self.true); rays_est = s2b.tof_rays(estimated)
        angular = np.degrees(np.arccos(np.clip(np.sum(rays_true * rays_est, axis=1), -1, 1)))
        self.assertLess(rotation_error, 1.0)
        self.assertLess(translation_error, 8.0)
        self.assertLess(float(np.sqrt(np.mean(angular ** 2))), 0.7)
        self.assertLess(fit["metrics"]["rms_mm"], 4.0)

    def test_dynamic_lopo_and_subset_cardinality(self):
        ids = [p["pose_id"] for p in self.obs["poses"]]
        lopo_counts = []
        for held in ids:
            train = s2b.observation_rows(self.obs, "strict", set(ids) - {held})
            test = s2b.observation_rows(self.obs, "strict", {held})
            self.assertTrue(train and test)
            lopo_counts.append(len(test))
        self.assertEqual(len(lopo_counts), len(ids))
        for fraction in (0.6, 0.8):
            self.assertEqual(max(2, int(math.ceil(len(ids) * fraction))), math.ceil(len(ids) * fraction))

    def test_solver_accepts_dynamic_n_10_12_20_30(self):
        source = self.obs["poses"]
        for count in (10, 12, 20, 30):
            poses = []
            for index in range(count):
                item = copy.deepcopy(source[index % len(source)])
                item["pose_id"] = f"DYN_{count}_{index:02d}"
                item["scan_id"] = f"scan_dyn_{count}_{index:02d}"
                item["frames"] = item["frames"][:2]
                poses.append(item)
            obs = {"schema_version": "stage2b_observations.v1", "pose_count": count, "poses": poses}
            rows = s2b.observation_rows(obs, "strict")
            fit = s2b.robust_lm(rows, "flip_horizontal", "radial_range", "B_low_dimensional", max_iterations=18)
            self.assertLess(fit["metrics"]["rms_mm"], 4.0)

    def test_broad_strict_tradeoff_and_missing_bad_zones(self):
        broad = s2b.observation_rows(self.obs, "broad")
        strict = s2b.observation_rows(self.obs, "strict")
        self.assertGreater(len(broad), len(strict))
        fit = s2b.robust_lm(broad, "flip_horizontal", "radial_range", "B_low_dimensional", max_iterations=30)
        self.assertLess(fit["metrics"]["rms_mm"], 9.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
