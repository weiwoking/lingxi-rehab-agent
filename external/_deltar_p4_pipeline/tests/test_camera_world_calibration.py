from __future__ import annotations

import math
import unittest
from pathlib import Path

import cv2
import numpy as np

from deltar_p4_pipeline.camera_calibration import (
    CharucoObservation,
    board_pose_to_camera_plane,
    solve_board_pose,
)
from deltar_p4_pipeline.camera_world_calibration import (
    EXPECTED_BOARD_BACK_NORMAL_WORLD,
    R_WORLD_FROM_BOARD,
    R_WORLD_FROM_CAMERA_LEVEL,
    FrameObservation,
    _tof_pose_diagnostics,
    board_coordinate_contract,
    make_transform,
    orientation_diagnostics,
    robust_rotation_average,
    rotation_distance_deg,
    rotation_from_diagnostics,
    rotation_world_from_camera_from_board_pose,
)
from deltar_p4_pipeline.camera_world_contracts import (
    CAMERA_HEIGHT_MM,
    FROZEN_CAMERA_TOF_PROFILE,
    load_frozen_camera_tof_profile,
    load_strict_json,
)
from deltar_p4_pipeline.charuco_board import charuco_board_from_manifest


ROOT = Path(__file__).resolve().parents[1]
BOARD_MANIFEST = ROOT / "board_assets" / "board_manifest.json"
CAMERA_PROFILE = Path(
    r"C:\calibration\result_20260719_172703\calibration_profile.v2.json"
)


def _small_rotation(x_deg: float, y_deg: float, z_deg: float) -> np.ndarray:
    rvec = np.radians([x_deg, y_deg, z_deg])
    return cv2.Rodrigues(rvec)[0]


def _actual_board_probe_inputs() -> tuple[
    np.ndarray, np.ndarray, np.ndarray
]:
    manifest = load_strict_json(BOARD_MANIFEST)
    board = charuco_board_from_manifest(manifest)
    object_points = np.asarray(
        board.getChessboardCorners(), dtype=np.float32
    ).reshape(-1, 3)
    profile = load_strict_json(CAMERA_PROFILE)
    intrinsics = profile["camera"]["intrinsics"]
    matrix = np.asarray(
        [
            [intrinsics["fx"], 0.0, intrinsics["cx"]],
            [0.0, intrinsics["fy"], intrinsics["cy"]],
            [0.0, 0.0, 1.0],
        ]
    )
    distortion = intrinsics["distortion"]
    coefficients = np.asarray(
        [
            distortion[key]
            for key in ("k1", "k2", "p1", "p2", "k3")
        ]
    )
    return object_points, matrix, coefficients


def _solve_actual_board_synthetic_pose(
    expected_rotation: np.ndarray, name: str
) -> tuple[np.ndarray, np.ndarray]:
    object_points, matrix, distortion = _actual_board_probe_inputs()
    translation = np.asarray([0.0, 0.0, 0.8])
    rvec, _ = cv2.Rodrigues(expected_rotation)
    projected, _ = cv2.projectPoints(
        object_points, rvec, translation, matrix, distortion
    )
    image_points = projected.reshape(-1, 2).astype(np.float32)
    observation = CharucoObservation(
        filename=name,
        sha256="synthetic",
        width=640,
        height=360,
        image_points=image_points,
        object_points=object_points,
        marker_count=len(object_points),
        corner_count=len(object_points),
        coverage=0.0,
        sharpness=0.0,
        centroid=(
            float(np.mean(image_points[:, 0])),
            float(np.mean(image_points[:, 1])),
        ),
        board_angle_deg=0.0,
    )
    solved_rotation, solved_translation, _ = solve_board_pose(
        observation, matrix, distortion
    )
    return solved_rotation, solved_translation


class CameraWorldAxesTests(unittest.TestCase):
    def test_actual_board_helper_object_points_match_world_mapping(self) -> None:
        manifest = load_strict_json(BOARD_MANIFEST)
        board = charuco_board_from_manifest(manifest)
        points = np.asarray(
            board.getChessboardCorners(), dtype=np.float32
        ).reshape(-1, 3)
        next_row = int(manifest["squares_x"]) - 1
        delta_right = points[1] - points[0]
        delta_down = points[next_row] - points[0]
        np.testing.assert_allclose(
            delta_right, [manifest["square_length_mm"] / 1000.0, 0.0, 0.0]
        )
        np.testing.assert_allclose(
            delta_down, [0.0, manifest["square_length_mm"] / 1000.0, 0.0]
        )
        np.testing.assert_allclose(
            np.cross(delta_right, delta_down)
            / np.linalg.norm(np.cross(delta_right, delta_down)),
            [0.0, 0.0, 1.0],
        )
        np.testing.assert_array_equal(R_WORLD_FROM_BOARD @ [1, 0, 0], [1, 0, 0])
        np.testing.assert_array_equal(R_WORLD_FROM_BOARD @ [0, 1, 0], [0, 0, -1])
        np.testing.assert_array_equal(R_WORLD_FROM_BOARD @ [0, 0, 1], [0, 1, 0])

    def test_actual_helper_front_pose_passes_with_correct_world_direction(
        self,
    ) -> None:
        rotation, translation = _solve_actual_board_synthetic_pose(
            np.eye(3), "printed_front"
        )
        self.assertGreater(float(translation[2]), 0.0)
        plane_normal, _ = board_pose_to_camera_plane(rotation, translation)
        np.testing.assert_allclose(plane_normal, [0.0, 0.0, 1.0], atol=1e-5)
        world_rotation = rotation_world_from_camera_from_board_pose(rotation)
        self.assertLess(
            rotation_distance_deg(
                world_rotation, R_WORLD_FROM_CAMERA_LEVEL
            ),
            0.001,
        )
        angles = orientation_diagnostics(world_rotation)
        self.assertLess(abs(angles["roll_deg"]), 0.001)
        self.assertLess(abs(angles["pitch_deg"]), 0.001)
        self.assertLess(abs(angles["yaw_deg"]), 0.001)

    def test_actual_helper_back_facing_pose_cannot_silently_match_front(
        self,
    ) -> None:
        front_rotation, _ = _solve_actual_board_synthetic_pose(
            np.eye(3), "printed_front"
        )
        back_rotation, back_translation = _solve_actual_board_synthetic_pose(
            np.diag([1.0, -1.0, -1.0]), "back_facing"
        )
        self.assertGreater(float(back_translation[2]), 0.0)
        front_world = rotation_world_from_camera_from_board_pose(
            front_rotation
        )
        back_world = rotation_world_from_camera_from_board_pose(back_rotation)
        self.assertGreater(
            rotation_distance_deg(front_world, back_world), 179.0
        )
        back_angles = orientation_diagnostics(back_world)
        self.assertTrue(
            abs(back_angles["roll_deg"]) > 5.0
            or abs(back_angles["pitch_deg"]) > 5.0
            or abs(back_angles["yaw_deg"]) > 5.0
        )

    def test_ideal_level_camera_contract(self) -> None:
        rotation = rotation_world_from_camera_from_board_pose(np.eye(3))
        np.testing.assert_allclose(rotation, R_WORLD_FROM_CAMERA_LEVEL)
        np.testing.assert_allclose(rotation @ [1, 0, 0], [1, 0, 0])
        np.testing.assert_allclose(rotation @ [0, 1, 0], [0, 0, -1])
        np.testing.assert_allclose(rotation @ [0, 0, 1], [0, 1, 0])
        diagnostics = orientation_diagnostics(rotation)
        self.assertAlmostEqual(diagnostics["roll_deg"], 0.0, places=10)
        self.assertAlmostEqual(diagnostics["pitch_deg"], 0.0, places=10)
        self.assertAlmostEqual(diagnostics["yaw_deg"], 0.0, places=10)
        transform = make_transform(rotation, [0, 0, CAMERA_HEIGHT_MM])
        np.testing.assert_array_equal(transform[:3, 3], [0, 0, 969])

    def test_board_to_world_mapping_and_normal_are_deterministic(self) -> None:
        contract = board_coordinate_contract()
        np.testing.assert_array_equal(R_WORLD_FROM_BOARD @ [1, 0, 0], [1, 0, 0])
        np.testing.assert_array_equal(R_WORLD_FROM_BOARD @ [0, 1, 0], [0, 0, -1])
        np.testing.assert_array_equal(R_WORLD_FROM_BOARD @ [0, 0, 1], [0, 1, 0])
        np.testing.assert_array_equal(
            R_WORLD_FROM_BOARD @ [0, 0, -1], [0, -1, 0]
        )
        self.assertIn("tvec.z>0", contract["front_back_resolution"])

    def test_transform_composition_and_inverse_roundtrip(self) -> None:
        stage7b = load_frozen_camera_tof_profile(FROZEN_CAMERA_TOF_PROFILE)
        world_camera = make_transform(
            R_WORLD_FROM_CAMERA_LEVEL, [0, 0, CAMERA_HEIGHT_MM]
        )
        camera_tof = np.asarray(stage7b["T_camera_from_tof"], dtype=np.float64)
        world_tof = world_camera @ camera_tof
        np.testing.assert_allclose(
            world_tof, world_camera @ camera_tof, atol=1e-12
        )
        np.testing.assert_allclose(
            world_tof @ np.linalg.inv(world_tof), np.eye(4), atol=1e-12
        )


class CameraWorldRotationTests(unittest.TestCase):
    def test_rotation_average_no_noise(self) -> None:
        value = rotation_from_diagnostics(1.0, -2.0, 3.0)
        result = robust_rotation_average([value.copy() for _ in range(15)])
        np.testing.assert_allclose(result.rotation, value, atol=1e-12)
        self.assertEqual(int(np.count_nonzero(result.inlier_mask)), 15)
        self.assertLess(float(np.max(result.residual_deg)), 1e-8)
        self.assertAlmostEqual(float(np.linalg.det(result.rotation)), 1.0, places=12)

    def test_rotation_average_light_noise(self) -> None:
        base = rotation_from_diagnostics(1.0, -1.0, 2.0)
        rotations = [
            base @ _small_rotation(0.02 * index, -0.01 * index, 0.005 * index)
            for index in range(-3, 4)
        ]
        result = robust_rotation_average(rotations)
        self.assertLess(rotation_distance_deg(base, result.rotation), 0.1)
        np.testing.assert_allclose(
            result.rotation.T @ result.rotation, np.eye(3), atol=1e-12
        )
        self.assertAlmostEqual(float(np.linalg.det(result.rotation)), 1.0, places=12)

    def test_rotation_outlier_is_rejected(self) -> None:
        base = rotation_from_diagnostics(0.2, -0.1, 0.3)
        rotations = [base @ _small_rotation(0.01 * i, 0, 0) for i in range(14)]
        rotations.append(rotation_from_diagnostics(20.0, 0.0, 0.0))
        result = robust_rotation_average(rotations)
        self.assertEqual(int(np.count_nonzero(result.inlier_mask)), 14)
        self.assertFalse(bool(result.inlier_mask[-1]))
        self.assertLess(rotation_distance_deg(base, result.rotation), 0.2)

    def test_euler_diagnostics_positive_and_negative_signs(self) -> None:
        for expected in (
            (2.0, 3.0, 4.0),
            (-2.0, -3.0, -4.0),
            (1.5, -2.5, 3.5),
        ):
            restored = orientation_diagnostics(
                rotation_from_diagnostics(*expected)
            )
            self.assertAlmostEqual(restored["roll_deg"], expected[0], places=9)
            self.assertAlmostEqual(restored["pitch_deg"], expected[1], places=9)
            self.assertAlmostEqual(restored["yaw_deg"], expected[2], places=9)


class CameraWorldTofSyntheticTests(unittest.TestCase):
    def test_vertical_plane_from_stage7b_nominal_rays(self) -> None:
        profile = load_frozen_camera_tof_profile(FROZEN_CAMERA_TOF_PROFILE)
        rays = np.asarray(profile["zone_rays"], dtype=np.float64)
        transform_ct = np.asarray(profile["T_camera_from_tof"], dtype=np.float64)
        # Under the ideal Camera mapping, +Y_world equals +Z_camera. Choose
        # axial-z distances so every transformed point lies on y_world=900 mm.
        coefficient = (
            transform_ct[:3, :3] @ (rays / rays[:, 2:3]).T
        ).T[:, 2]
        canonical_distance = (
            900.0 - transform_ct[2, 3]
        ) / coefficient
        raw_map = np.asarray(
            profile["orientation"]["raw_index_for_canonical_index"], dtype=int
        )
        raw_distance = np.zeros(64, dtype=np.float64)
        raw_distance[raw_map] = canonical_distance
        tof = {
            "target_status": [5] * 64,
            "nb_target_detected": [1] * 64,
            "distance_mm": raw_distance.tolist(),
            "range_sigma_mm": [1.0] * 64,
        }
        frames = [
            FrameObservation(
                pose_name="near",
                role="world_front_near",
                frame_name=f"frame_{index:06d}",
                scan_dir=FROZEN_CAMERA_TOF_PROFILE.parent,
                rotation_world_from_camera=R_WORLD_FROM_CAMERA_LEVEL.copy(),
                rotation_camera_from_board=np.eye(3),
                translation_camera_from_board_m=np.asarray([0.0, 0.0, 0.9]),
                camera_diagnostics={},
                board_polygon=np.zeros((4, 2)),
                image_bgr=np.zeros((360, 640, 3), dtype=np.uint8),
                tof=tof,
                source_paths=(),
            )
            for index in range(5)
        ]
        diagnostics, overlay = _tof_pose_diagnostics(frames, profile)
        self.assertEqual(diagnostics["status"], "PASS")
        self.assertGreaterEqual(diagnostics["usable_overlap_zone_count"], 16)
        self.assertLess(diagnostics["normal_angular_error_deg"], 1e-8)
        self.assertLess(diagnostics["plane_rms_mm"], 1e-8)
        self.assertIsNotNone(overlay)
        np.testing.assert_allclose(
            diagnostics["plane_normal_world"],
            EXPECTED_BOARD_BACK_NORMAL_WORLD,
            atol=1e-10,
        )


if __name__ == "__main__":
    unittest.main()
