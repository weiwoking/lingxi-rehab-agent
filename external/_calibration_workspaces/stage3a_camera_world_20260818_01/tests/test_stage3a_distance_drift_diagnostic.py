from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np


WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE))

import stage3a_distance_drift_diagnostic as diagnostic  # noqa: E402


class DistanceDriftDiagnosticTests(unittest.TestCase):
    def test_board_axes_and_active_top_bottom_contract(self) -> None:
        self.assertTrue(np.allclose(diagnostic.R0.T @ diagnostic.R0, np.eye(3)))
        self.assertAlmostEqual(float(np.linalg.det(diagnostic.R0)), 1.0, places=12)
        top = diagnostic.R0 @ np.asarray([0.0, 0.0, 0.0]) + np.asarray([0.0, 0.0, 1037.0])
        bottom = diagnostic.R0 @ np.asarray([0.0, 125.0, 0.0]) + np.asarray([0.0, 0.0, 1037.0])
        self.assertAlmostEqual(float(top[2]), 1037.0)
        self.assertAlmostEqual(float(bottom[2]), 912.0)

    def test_height_inputs_shift_offset_not_distance_slope(self) -> None:
        distances = [600.0, 800.0, 1000.0]
        residuals = [30.0, 40.0, 50.0]
        nominal = diagnostic.slope(distances, residuals, 1000.0)
        for camera_delta in (-10.0, 0.0, 10.0):
            for board_delta in (-5.0, 0.0, 5.0):
                shifted = [value + camera_delta - board_delta for value in residuals]
                self.assertAlmostEqual(diagnostic.slope(distances, shifted, 1000.0), nominal, places=12)

    def test_synthetic_planar_intrinsic_mismatch_creates_low_error_distance_drift(self) -> None:
        board, _ = diagnostic.board_and_detector()
        object_points = np.asarray(board.getChessboardCorners(), dtype=np.float64)
        true_parameters = {
            "fx": 868.6,
            "fy": 867.5,
            "cx": 301.6,
            "cy": 178.9,
            "k1": 0.0,
            "k2": 0.0,
            "p1": 0.0,
            "p2": 0.0,
            "k3": 0.0,
        }
        K_true, D_true = diagnostic.matrices(true_parameters)
        solve_parameters = dict(true_parameters)
        solve_parameters["fx"] -= 10.0
        K_solve, D_solve = diagnostic.matrices(solve_parameters)
        R_wc_true = diagnostic.R_wc_from_gravity(
            np.asarray([math.radians(-2.0), math.radians(0.3)])
        )
        t_wc = np.asarray([0.0, 0.0, 0.972])
        distances_m = [0.6, 0.8, 1.0]
        x_m = [-0.10, -0.05, -0.04]
        yaw_deg = [3.0, 0.5, 0.0]
        solutions = []
        rotations = []
        reprojection_rms = []
        for distance, x, yaw in zip(distances_m, x_m, yaw_deg):
            R_wb = diagnostic.Rz(math.radians(yaw)) @ diagnostic.R0
            t_wb = np.asarray([x, distance, 1.037])
            R_cb_true = R_wc_true.T @ R_wb
            t_cb_true = R_wc_true.T @ (t_wb - t_wc)
            rvec_true, _ = cv2.Rodrigues(R_cb_true)
            image_points = diagnostic.project(
                object_points, rvec_true, t_cb_true, K_true, D_true
            )
            ok, rvec, tvec = cv2.solvePnP(
                object_points,
                image_points,
                K_solve,
                D_solve,
                flags=cv2.SOLVEPNP_ITERATIVE,
            )
            self.assertTrue(ok)
            R_cb, _ = cv2.Rodrigues(rvec)
            solution = {"R_cb": R_cb, "tvec_m": tvec.reshape(3)}
            solutions.append(solution)
            rotations.append(diagnostic.R0 @ R_cb.T)
            reprojection = diagnostic.project(object_points, rvec, tvec, K_solve, D_solve)
            reprojection_rms.append(
                float(np.sqrt(np.mean(np.sum((reprojection - image_points) ** 2, axis=1))))
            )
        aggregate = diagnostic.chordal_mean(rotations)
        residuals = [
            diagnostic.active_height_with_rotation(item, aggregate, 972.0) - 912.0
            for item in solutions
        ]
        drift = diagnostic.slope([value * 1000.0 for value in distances_m], residuals, 1000.0)
        self.assertGreater(abs(drift), 50.0)
        # The wrong-intrinsic solution still looks visually excellent even
        # though its recovered height has a severe distance slope.
        self.assertLess(max(reprojection_rms), 0.5)


if __name__ == "__main__":
    unittest.main()
