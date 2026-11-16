from __future__ import annotations

import importlib.util
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("stage2c", ROOT / "stage2c_camera_tof_validate.py")
assert SPEC and SPEC.loader
s2c = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(s2c)


def rotation_y(degrees: float) -> np.ndarray:
    angle = math.radians(degrees)
    return np.array([[math.cos(angle), 0, math.sin(angle)], [0, 1, 0], [-math.sin(angle), 0, math.cos(angle)]])


def synthetic_measurements(normal: np.ndarray, offset: float, noise: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rays, _, rotation, translation = s2c.frozen_geometry()
    dummy = np.zeros(64)
    _, _, expected = s2c.geometry_residuals(normal, offset, dummy, rays, rotation, translation)
    measured = expected.copy() if noise is None else expected + noise
    return rays, rotation, translation, measured


def synthetic_observations(pose_count: int, noise_sigma: float = 0.0, outside: bool = False) -> dict:
    rng = np.random.default_rng(20260818)
    rays, _, rotation, translation = s2c.frozen_geometry()
    poses = []
    all_abs = []
    for pose_index in range(pose_count):
        frames = []
        yaw = -16 + 32 * pose_index / max(pose_count - 1, 1)
        normal = rotation_y(yaw) @ np.array([0.0, 0.0, -1.0])
        distance = 450 + (pose_index % 3) * 220
        offset = distance
        for frame_index in range(11):
            _, _, expected = s2c.geometry_residuals(normal, offset, np.zeros(64), rays, rotation, translation)
            measured = expected + rng.normal(0, noise_sigma, 64)
            if outside: measured[::9] += 300
            axial, point_plane, expected_again = s2c.geometry_residuals(normal, offset, measured, rays, rotation, translation)
            zones = []
            for index in range(64):
                zones.append({"raw_index": index, "raw_row": index // 8, "raw_col": index % 8,
                              "distance_mm": float(measured[index]), "range_sigma_mm": 3.0, "target_status": 5,
                              "target_count": 1, "broad_valid": True, "strict_valid": True,
                              "axial_depth_residual_mm": float(axial[index]),
                              "point_to_plane_residual_mm": float(point_plane[index]),
                              "expected_axial_z_mm": float(expected_again[index]),
                              "likely_reference_plane": None, "likely_outside_or_background": None})
                all_abs.append(abs(float(point_plane[index])))
            plane = {"detected": True, "plane_normal_camera": normal.tolist(), "plane_offset_camera_mm": offset,
                     "plane_distance_camera_mm": distance, "yaw_like_deg": yaw, "pitch_like_deg": 0.0,
                     "combined_tilt_deg": abs(yaw), "board_center_px": [180 + pose_index * 15, 180],
                     "board_coverage_fraction": .08, "pnp_rms_px": .1}
            frames.append({"frame_id": f"frame_{frame_index:06d}", "pair_delta_us": 10000 + frame_index,
                           "camera_plane": plane, "tof_zones": zones})
        poses.append({"pose_id": f"SYN_{pose_index:03d}", "scan_id": f"scan_synthetic_{pose_index:03d}", "frames": frames,
                      "within_scan_plane_stability": {"usable_plane_frames": 11, "normal_max_deviation_deg": 0,
                                                       "offset_range_mm": 0, "stable": True}})
    absolute = np.asarray(all_abs); median = float(np.median(absolute)); mad = float(np.median(np.abs(absolute - median)))
    threshold = max(10.0, median + 4.5 * 1.4826 * mad)
    for pose in poses:
        for frame in pose["frames"]:
            for zone in frame["tof_zones"]:
                residual = abs(zone["point_to_plane_residual_mm"])
                zone["likely_reference_plane"] = residual <= threshold
                zone["likely_outside_or_background"] = residual > threshold
    return {"schema_version": "stage2c_observations.v1", "pose_count": pose_count, "poses": poses,
            "plane_consistency_rule": {"threshold_mm": threshold, "classification_only": True,
                                       "untrimmed_layers_always_reported": True}}


class Stage2CTests(unittest.TestCase):
    def test_perfect_frozen_geometry(self) -> None:
        for normal, offset in ((np.array([0, 0, -1.0]), 600.0),
                               (rotation_y(17) @ np.array([0, 0, -1.0]), 820.0),
                               (rotation_y(-22) @ np.array([0, 0, -1.0]), 460.0)):
            rays, rotation, translation, measured = synthetic_measurements(normal, offset)
            axial, point_plane, _ = s2c.geometry_residuals(normal, offset, measured, rays, rotation, translation)
            self.assertLess(float(np.max(np.abs(axial))), 1e-9)
            self.assertLess(float(np.max(np.abs(point_plane))), 1e-9)

    def test_measurement_noise(self) -> None:
        rng = np.random.default_rng(7); noise = rng.normal(0, 4.0, 4096)
        self.assertGreater(float(np.sqrt(np.mean(noise ** 2))), 3.5)
        self.assertLess(float(np.sqrt(np.mean(noise ** 2))), 4.5)
        normal = rotation_y(12) @ np.array([0, 0, -1.0])
        residuals = []
        for chunk in noise.reshape(-1, 64):
            rays, rotation, translation, measured = synthetic_measurements(normal, 650, chunk)
            axial, _, _ = s2c.geometry_residuals(normal, 650, measured, rays, rotation, translation)
            residuals.extend(axial)
        self.assertAlmostEqual(float(np.sqrt(np.mean(np.asarray(residuals) ** 2))), 4.0, delta=.5)

    def test_perturbed_frozen_candidate_detected(self) -> None:
        normal = rotation_y(20) @ np.array([0, 0, -1.0]); rays, rotation, translation, measured = synthetic_measurements(normal, 700)
        perturbed_rotation = rotation_y(2) @ rotation; perturbed_translation = translation + np.array([15.0, 0, 0])
        _, residual, _ = s2c.geometry_residuals(normal, 700, measured, rays, perturbed_rotation, perturbed_translation)
        self.assertGreater(float(np.sqrt(np.mean(residual ** 2))), 3.0)
        left, right = float(np.mean(residual[:32])), float(np.mean(residual[32:]))
        self.assertGreater(max(abs(left), abs(right)), 2.0)

    def test_outside_background_keeps_core(self) -> None:
        observations = synthetic_observations(3, noise_sigma=3.0, outside=True)
        records = s2c.observation_records(observations)
        broad = [r for r in records if r["broad_valid"]]; core = [r for r in records if r["likely_reference_plane"]]
        self.assertEqual(len(broad), 3 * 11 * 64)
        self.assertLess(len(core), len(broad))
        self.assertGreater(s2c.metric(r["point_to_plane_residual_mm"] for r in broad)["max_abs_mm"], 200)
        self.assertLess(s2c.metric(r["point_to_plane_residual_mm"] for r in core)["p95_abs_mm"], 10)

    def test_dynamic_n_has_no_maximum(self) -> None:
        for count in (3, 6, 8, 12):
            observations = synthetic_observations(count)
            self.assertEqual(len(s2c.pose_summary(observations, s2c.observation_records(observations))), count)
        registry = {}
        for index in range(37): registry[s2c.valid_pose_id(f"EXTRA_VALIDATION_{index:02d}")] = {}
        self.assertEqual(len(registry), 37)

    def test_no_refit_sentinel(self) -> None:
        choices = set(s2c.parser()._subparsers._group_actions[0].choices)
        self.assertEqual(choices, {"register", "status", "inspect", "coverage", "validate", "summary", "suggest-next"})
        observations = synthetic_observations(1)
        s2c.ensure_layout()
        def forbidden(*args, **kwargs):
            raise AssertionError("parameter estimator was called")
        with mock.patch.object(cv2, "calibrateCamera", forbidden), \
             mock.patch.object(cv2, "calibrateCameraExtended", forbidden), \
             mock.patch.object(s2c, "create_plots"):
            document = s2c.validate_document(observations, persist=False)
        self.assertTrue(document["no_pose_or_zone_deleted"])

    def test_authorities_and_math_contract(self) -> None:
        s2c.validate_candidate_contracts()
        rays, camera_rays, rotation, translation = s2c.frozen_geometry()
        self.assertEqual(rays.shape, (64, 3)); self.assertEqual(camera_rays.shape, (64, 3))
        self.assertEqual(rotation.shape, (3, 3)); self.assertEqual(translation.shape, (3,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
