from __future__ import annotations

import math
import unittest

import cv2
import numpy as np

from deltar_p4_pipeline.camera_calibration import (
    board_pose_m_to_camera_plane_mm,
    project_board_polygon,
)
from deltar_p4_pipeline.camera_tof_calibration import (
    CameraTofError,
    aggregate_raw_zones,
    fit_dominant_plane,
    inverse_transform,
    mechanical_diagnostics,
    nominal_zone_rays,
    points_from_distances,
    residual_and_jacobian,
    solve_point_to_plane,
    transform_plane_camera_to_tof,
    transform_points,
)
from deltar_p4_pipeline.zone_orientation import (
    D4_STRATEGIES,
    canonical_index_for_raw_index,
    canonical_values_from_raw,
    raw_index_for_canonical_index,
    raw_values_from_canonical,
)


def synthetic_point_planes(
    *, noise_mm: float = 0.0, outliers: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(17)
    true_rvec = np.asarray([0.035, -0.022, 0.014])
    true_rotation, _ = cv2.Rodrigues(true_rvec)
    true_translation = np.asarray([-20.0, -3.5, 6.5])
    normals = [
        np.asarray([0.0, 0.0, 1.0]),
        np.asarray([0.22, 0.03, 0.975]),
        np.asarray([-0.20, 0.08, 0.976]),
        np.asarray([0.02, 0.25, 0.968]),
        np.asarray([0.08, -0.23, 0.970]),
        np.asarray([0.18, -0.18, 0.967]),
    ]
    all_points = []
    all_normals = []
    all_offsets = []
    for pose_index, normal in enumerate(normals):
        normal = normal / np.linalg.norm(normal)
        distance = 360.0 + pose_index * 45.0
        offset_camera = -distance
        normal_tof, offset_tof = transform_plane_camera_to_tof(
            normal, offset_camera, true_rotation, true_translation,
        )
        xy = rng.uniform(-140.0, 140.0, size=(48, 2))
        z = -(xy @ normal_tof[:2] + offset_tof) / normal_tof[2]
        points = np.column_stack((xy, z))
        if noise_mm:
            points += rng.normal(0.0, noise_mm, size=points.shape)
        if outliers:
            points[:3, 2] += rng.choice([-1.0, 1.0], 3) * 35.0
        all_points.append(points)
        all_normals.append(np.repeat(normal[None, :], len(points), axis=0))
        all_offsets.append(np.full(len(points), offset_camera))
    return (
        np.concatenate(all_points), np.concatenate(all_normals),
        np.concatenate(all_offsets), true_rotation, true_translation,
    )


class D4Tests(unittest.TestCase):
    def test_all_eight_are_unique_bijections_and_invertible(self) -> None:
        maps = [raw_index_for_canonical_index(name) for name in D4_STRATEGIES]
        self.assertEqual(len(maps), 8)
        self.assertEqual(len({tuple(item.tolist()) for item in maps}), 8)
        source = np.arange(64)
        for name, mapping in zip(D4_STRATEGIES, maps):
            self.assertEqual(sorted(mapping.tolist()), list(range(64)))
            canonical = canonical_values_from_raw(source, name)
            np.testing.assert_array_equal(raw_values_from_canonical(canonical, name), source)
            inverse = canonical_index_for_raw_index(name)
            np.testing.assert_array_equal(inverse[mapping], np.arange(64))

    def test_corner_contract(self) -> None:
        expected = {
            "identity": [0, 7, 56, 63],
            "rotate_90_cw": [56, 0, 63, 7],
            "rotate_180": [63, 56, 7, 0],
            "rotate_270_cw": [7, 63, 0, 56],
            "flip_horizontal": [7, 0, 63, 56],
            "flip_vertical": [56, 63, 0, 7],
            "transpose_main_diagonal": [0, 56, 7, 63],
            "transpose_anti_diagonal": [63, 7, 56, 0],
        }
        corners = [0, 7, 56, 63]
        for name, values in expected.items():
            self.assertEqual(raw_index_for_canonical_index(name)[corners].tolist(), values)


class RayAndPlaneTests(unittest.TestCase):
    def test_ray_symmetry_and_unit_length(self) -> None:
        rays = nominal_zone_rays()
        self.assertEqual(rays.shape, (64, 3))
        np.testing.assert_allclose(np.linalg.norm(rays, axis=1), 1.0, atol=1e-12)
        self.assertTrue(np.all(rays[:, 2] > 0))
        grid = rays.reshape(8, 8, 3)
        np.testing.assert_allclose(grid[:, :, 0], -np.fliplr(grid[:, :, 0]))
        np.testing.assert_allclose(grid[:, :, 1], -np.flipud(grid[:, :, 1]))

    def test_axial_and_radial_construction(self) -> None:
        rays = nominal_zone_rays()
        distances = np.full(64, 500.0)
        axial = points_from_distances(rays, distances, "axial_z")
        radial = points_from_distances(rays, distances, "radial_range")
        np.testing.assert_allclose(axial[:, 2], 500.0)
        np.testing.assert_allclose(np.linalg.norm(radial, axis=1), 500.0)
        self.assertGreater(np.linalg.norm(axial[0]), 500.0)

    def test_board_pose_to_plane_and_transform_roundtrip(self) -> None:
        rotation, _ = cv2.Rodrigues(np.asarray([0.1, -0.2, 0.03]))
        translation_m = np.asarray([0.010, 0.020, 0.500])
        normal, offset = board_pose_m_to_camera_plane_mm(
            rotation, translation_m
        )
        self.assertAlmostEqual(np.linalg.norm(normal), 1.0, places=12)
        self.assertAlmostEqual(
            float(normal @ (translation_m * 1000.0) + offset), 0.0, places=9
        )
        test_rotation, _ = cv2.Rodrigues(np.asarray([0.03, 0.01, -0.02]))
        test_translation = np.asarray([20.0, -4.0, 6.0])
        points = np.asarray([[1.0, 2.0, 300.0], [-4.0, 8.0, 600.0]])
        inverse_r, inverse_t = inverse_transform(test_rotation, test_translation)
        recovered = transform_points(inverse_r, inverse_t, transform_points(test_rotation, test_translation, points))
        np.testing.assert_allclose(recovered, points, atol=1e-10)
        plane_normal = np.asarray([0.1, -0.2, 0.974])
        plane_normal /= np.linalg.norm(plane_normal)
        plane_offset = -500.0
        tof_normal, tof_offset = transform_plane_camera_to_tof(
            plane_normal, plane_offset, test_rotation, test_translation,
        )
        xy = np.asarray([[10.0, 20.0], [-80.0, 35.0], [60.0, -40.0]])
        z = -(xy @ tof_normal[:2] + tof_offset) / tof_normal[2]
        points_tof = np.column_stack((xy, z))
        points_camera = transform_points(test_rotation, test_translation, points_tof)
        np.testing.assert_allclose(points_camera @ plane_normal + plane_offset, 0.0, atol=1e-9)

    def test_board_pose_metres_produces_camera_plane_millimetres(self) -> None:
        normal, offset = board_pose_m_to_camera_plane_mm(
            np.eye(3), np.asarray([0.0, 0.0, 0.5])
        )
        np.testing.assert_array_equal(normal, [0.0, 0.0, 1.0])
        self.assertEqual(offset, -500.0)

        # Global plane sign normalization must not change the output unit.
        flipped = np.diag([1.0, -1.0, -1.0])
        normalized_normal, normalized_offset = board_pose_m_to_camera_plane_mm(
            flipped, np.asarray([0.0, 0.0, 0.5])
        )
        np.testing.assert_array_equal(normalized_normal, [0.0, 0.0, 1.0])
        self.assertEqual(normalized_offset, -500.0)

    def test_board_polygon_uses_same_metre_unit_as_solvepnp_tvec(self) -> None:
        matrix = np.asarray(
            [[800.0, 0.0, 320.0], [0.0, 800.0, 180.0], [0.0, 0.0, 1.0]]
        )
        distortion = np.zeros(5)
        rotation = np.eye(3)
        translation_m = np.asarray([0.0, 0.0, 0.5])
        actual = project_board_polygon(
            rotation, translation_m, matrix, distortion, 175.0, 125.0
        )
        corners_m = np.asarray(
            [
                [0.0, 0.0, 0.0],
                [0.175, 0.0, 0.0],
                [0.175, 0.125, 0.0],
                [0.0, 0.125, 0.0],
            ]
        )
        expected, _ = cv2.projectPoints(
            corners_m, np.zeros(3), translation_m, matrix, distortion
        )
        self.assertTrue(np.isfinite(actual).all())
        self.assertLess(float(np.max(np.abs(actual))), 1000.0)
        np.testing.assert_allclose(actual, expected.reshape(4, 2), atol=1e-12)

    def test_real_scale_plane_offsets_are_not_fractional_millimetres(self) -> None:
        offsets = [
            abs(board_pose_m_to_camera_plane_mm(np.eye(3), [0, 0, z])[1])
            for z in (0.35, 0.59)
        ]
        self.assertEqual(offsets, [350.0, 590.0])
        self.assertEqual(np.ptp(offsets), 240.0)

    def test_mechanical_sign_uses_camera_x_not_observer_x(self) -> None:
        negative = mechanical_diagnostics(np.eye(3), [-16.0, 0.0, 0.0])
        positive = mechanical_diagnostics(np.eye(3), [16.0, 0.0, 0.0])
        self.assertTrue(negative["tx_sign_matches_expected"])
        self.assertEqual(negative["tx_error_from_expected_mm"], 4.0)
        self.assertFalse(positive["tx_sign_matches_expected"])

    def test_dominant_plane_rejects_outliers(self) -> None:
        rng = np.random.default_rng(3)
        xy = rng.uniform(-100, 100, (40, 2))
        points = np.column_stack((xy, 500 + 0.1 * xy[:, 0] - 0.05 * xy[:, 1]))
        points[:4, 2] += 80
        normal, offset, mask, diagnostics = fit_dominant_plane(points, np.arange(40))
        self.assertGreaterEqual(np.count_nonzero(mask), 35)
        self.assertLess(np.count_nonzero(mask), 40)
        self.assertEqual(diagnostics["plane_inlier_count"], int(np.count_nonzero(mask)))
        self.assertLess(np.median(np.abs(points[mask] @ normal + offset)), 1e-8)


class AggregationTests(unittest.TestCase):
    @staticmethod
    def frame(status: int = 5, count: int = 1, distance: int = 500) -> dict[str, list[int]]:
        return {
            "distance_mm": [distance] * 64, "range_sigma_mm": [4] * 64,
            "target_status": [status] * 64, "nb_target_detected": [count] * 64,
        }

    def test_status_rejections_and_median_mad(self) -> None:
        frames = [self.frame(distance=500), self.frame(distance=510), self.frame(distance=490)]
        frames[0]["target_status"][0] = 255
        frames[1]["target_status"][1] = 10
        frames[2]["nb_target_detected"][2] = 0
        median, mad, _, valid, reasons = aggregate_raw_zones(frames)
        self.assertEqual(median[3], 500)
        self.assertEqual(mad[3], 10)
        self.assertTrue(valid[0])
        self.assertIn("status_255", reasons[0])
        self.assertIn("status_10", reasons[1])
        self.assertIn("target_count_0", reasons[2])

    def test_status_6_is_opt_in(self) -> None:
        frame = self.frame(status=6)
        self.assertFalse(aggregate_raw_zones([frame])[3].any())
        self.assertTrue(aggregate_raw_zones([frame], include_status_6=True)[3].all())


class SolverTests(unittest.TestCase):
    def test_analytic_jacobian_matches_finite_difference(self) -> None:
        points, normals, offsets, rotation, translation = synthetic_point_planes()
        residual, jacobian = residual_and_jacobian(rotation, translation, points[:30], normals[:30], offsets[:30])
        numeric = np.zeros_like(jacobian)
        epsilon = 1e-7
        for column in range(6):
            delta = np.zeros(6)
            delta[column] = epsilon
            delta_rotation, _ = cv2.Rodrigues(delta[:3])
            trial_residual, _ = residual_and_jacobian(
                delta_rotation @ rotation, translation + delta[3:],
                points[:30], normals[:30], offsets[:30],
            )
            numeric[:, column] = (trial_residual - residual) / epsilon
        np.testing.assert_allclose(jacobian, numeric, rtol=2e-5, atol=2e-5)

    def test_noiseless_recovery(self) -> None:
        points, normals, offsets, true_rotation, true_translation = synthetic_point_planes()
        solved = solve_point_to_plane(points, normals, offsets)
        error_rotation, _ = cv2.Rodrigues(solved.rotation @ true_rotation.T)
        self.assertLess(math.degrees(np.linalg.norm(error_rotation)), 0.1)
        self.assertLess(np.linalg.norm(solved.translation - true_translation), 0.5)

    def test_noisy_outlier_recovery(self) -> None:
        points, normals, offsets, true_rotation, true_translation = synthetic_point_planes(noise_mm=0.5, outliers=True)
        solved = solve_point_to_plane(points, normals, offsets)
        error_rotation, _ = cv2.Rodrigues(solved.rotation @ true_rotation.T)
        self.assertLess(math.degrees(np.linalg.norm(error_rotation)), 1.0)
        self.assertLess(np.linalg.norm(solved.translation - true_translation), 5.0)

    def test_parallel_planes_are_unobservable(self) -> None:
        rng = np.random.default_rng(5)
        points = rng.normal(size=(80, 3)) * 50
        normals = np.repeat([[0.0, 0.0, 1.0]], len(points), axis=0)
        offsets = -np.repeat([400.0, 500.0], 40)
        with self.assertRaises(CameraTofError) as context:
            solve_point_to_plane(points, normals, offsets)
        self.assertEqual(context.exception.code, "E_CAMERA_TOF_UNOBSERVABLE")

    def test_single_plane_is_unobservable(self) -> None:
        rng = np.random.default_rng(9)
        xy = rng.uniform(-100, 100, (64, 2))
        points = np.column_stack((xy, np.full(64, 500.0)))
        normals = np.repeat([[0.0, 0.0, 1.0]], len(points), axis=0)
        offsets = np.full(len(points), -500.0)
        with self.assertRaises(CameraTofError) as context:
            solve_point_to_plane(points, normals, offsets)
        self.assertEqual(context.exception.code, "E_CAMERA_TOF_UNOBSERVABLE")


if __name__ == "__main__":
    unittest.main()
