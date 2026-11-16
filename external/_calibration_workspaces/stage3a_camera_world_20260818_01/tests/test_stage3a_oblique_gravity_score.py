from __future__ import annotations

import contextlib
import inspect
import io
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np


WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE))
import stage3a_oblique_gravity_score as gravity
import stage3a_straightness_score as base


class GravityMathTests(unittest.TestCase):
    def test_gravity_vector_sign(self) -> None:
        self.assertTrue(np.allclose(gravity.gravity_from_R_camera_from_board(np.eye(3)), [0, -1, 0]))

    def test_gravity_yaw_invariance(self) -> None:
        audit = gravity.gravity_yaw_invariance_numeric()
        self.assertTrue(audit["pass"])
        self.assertLess(audit["max_numeric_error_deg"], 1e-9)

    def test_obliquity_proxy_independent_of_board_center(self) -> None:
        K, D, _ = base.nominal_camera_model()
        board, _ = base.board_and_detector()
        objects = np.asarray(board.getChessboardCorners(), dtype=np.float64).reshape(-1, 3)
        up = gravity.normalize(np.asarray([0.006, -0.9998, 0.017]))
        values = []
        for lateral in (-0.12, 0.0, 0.12):
            frame, _, _ = gravity.synthetic_frame(objects, up, 30, 0.8, lateral, 0, 1, K, D, K, D, "test")
            values.append(gravity.shape_metrics(frame, K, D)["obliquity_proxy"])
        self.assertLess(max(values) - min(values), 1e-4)


class GravityFixedFitTests(unittest.TestCase):
    def test_fit_layer_has_no_height_truth(self) -> None:
        source = inspect.getsource(gravity.gravity_fixed_fit)
        for forbidden in ("912", "1037", "CAMERA_HEIGHT", "BOARD_BOTTOM"):
            self.assertNotIn(forbidden, source)

    def test_gravity_fixed_per_pose_fit_recovers_synthetic_pose(self) -> None:
        K, D, _ = base.nominal_camera_model()
        board, _ = base.board_and_detector()
        objects = np.asarray(board.getChessboardCorners(), dtype=np.float64).reshape(-1, 3)
        up = gravity.normalize(np.asarray([0.006, -0.9998, 0.017]))
        frame, _, _ = gravity.synthetic_frame(objects, up, 3, 0.8, 0.04, 0.05, 3, K, D, K, D, "fit")
        fit = gravity.gravity_fixed_fit(objects, frame.image_points, K, D, up)
        self.assertTrue(fit["success"])
        self.assertLess(fit["reprojection_rms_px"], 0.2)
        self.assertFalse(fit["fit_uses_physical_height_truth"])

    def test_end_to_end_suppresses_height_slope(self) -> None:
        result = gravity.synthetic_end_to_end(trials=8)
        self.assertTrue(result["pass"])
        self.assertGreater(result["unconstrained_height_slope_mm_per_m"]["p95"], 30)
        self.assertLess(result["gravity_fixed_height_slope_mm_per_m"]["p95"], 10)


class GravityPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.K, cls.D, _ = base.nominal_camera_model()
        board, _ = base.board_and_detector()
        cls.objects = np.asarray(board.getChessboardCorners(), dtype=np.float64).reshape(-1, 3)
        cls.up = gravity.normalize(np.asarray([0.006, -0.9998, 0.017]))

    def score_synthetic(self, yaw: float, lateral: float = 0.0) -> dict:
        frames = []
        for index in range(11):
            frame, _, _ = gravity.synthetic_frame(
                self.objects, self.up, yaw, 0.8, lateral, 0.15, 100 + index,
                self.K, self.D, self.K, self.D, f"frame_{index}",
            )
            frames.append(frame)
        return gravity.aggregate_analysis(frames, self.K, self.D, len(frames), include_lopo=False)

    def test_frontal_is_rejected_and_oblique_is_good(self) -> None:
        self.assertEqual(self.score_synthetic(0)["decision"], "TOO_FRONTAL")
        self.assertEqual(self.score_synthetic(30)["decision"], "GOOD")

    def test_solver_gravity_agreement_in_good_region(self) -> None:
        result = self.score_synthetic(30)
        self.assertLess(result["solver"]["core_aggregate_disagreement_deg"], 0.75)
        self.assertIsNotNone(result["gravity_authority"])
        self.assertFalse(result["gravity_authority"]["full_camera_world_rotation_authority"])

    def test_single_preview_is_not_gravity_authority(self) -> None:
        frame, _, _ = gravity.synthetic_frame(
            self.objects, self.up, 30, 0.8, 0, 0.15, 11, self.K, self.D, self.K, self.D, "preview"
        )
        result = gravity.aggregate_analysis([frame], self.K, self.D, 1, include_lopo=False)
        self.assertEqual(result["decision"], "GOOD")
        self.assertEqual(result["gravity_status"], "PREVIEW_NOT_READY")
        self.assertIsNone(result["gravity_authority"])

    def test_ippe_branch_handling(self) -> None:
        frame, _, _ = gravity.synthetic_frame(
            self.objects, self.up, 30, 0.8, 0, 0.15, 9, self.K, self.D, self.K, self.D, "ippe"
        )
        row = gravity.frame_analysis(frame, self.K, self.D)
        self.assertEqual(row["ippe_branch"]["solution_count"], 2)
        self.assertGreater(row["ippe_branch"]["reprojection_gap_px"], 0)

    def test_user_output_has_no_degree_or_measurement_requirement(self) -> None:
        forbidden = ("degree", "angle", "ruler", "distance", "°", "量角", "测量")
        for yaw in (0, 30, 60):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                gravity.print_human(self.score_synthetic(yaw))
            output = stream.getvalue().lower()
            self.assertFalse(any(word in output for word in forbidden), output)

    def test_current_registry_and_frozen_hashes(self) -> None:
        self.assertEqual(base.sha256(base.STAGE2_BUNDLE), base.EXPECTED_STAGE2_SHA256)
        self.assertEqual(base.sha256(base.REGISTRY), "77219b55b4a1716b6bc51c643a84d5a1b20171e3565f323cb443b2245b9f7050")
        self.assertEqual(base.sha256(WORKSPACE / "stage3a_camera_world.py"), "8aa81cb4e6a0d176050232c3acf01fa91c682c96919c4e9a38fd661c3798b2ef")
        self.assertEqual(base.sha256(WORKSPACE / "stage3a_straightness_score.py"), "f5a1af5d8f9db6cb00101ca5a11cef182ac350ef17e66965e3caf14dc15a40af")

    def test_no_automatic_capture_or_registration_command(self) -> None:
        help_text = gravity.build_parser().format_help().lower()
        self.assertNotIn("capture", help_text)
        self.assertNotIn("register", help_text)

    def test_conditioning_evidence_wide_noise_robust_and_kd_stable(self) -> None:
        evidence = base.read_json(
            WORKSPACE / "diagnostics" / "oblique_gravity_20260818" / "CONDITIONING_STUDY.json"
        )
        plateau = evidence["accepted_internal_yaw_plateau_deg"]
        self.assertGreaterEqual(len(plateau), 5)
        frontal_p95 = evidence["per_internal_yaw"]["0"]["consensus_gravity_error_deg"]["p95"]
        plateau_p95 = max(
            evidence["per_internal_yaw"][str(value)]["consensus_gravity_error_deg"]["p95"]
            for value in plateau
        )
        self.assertGreater(frontal_p95, 2 * plateau_p95)
        for value in plateau:
            row = evidence["per_internal_yaw"][str(value)]
            self.assertLess(row["lopo_consensus_gravity_error_deg"]["p95"], 0.75)
            for noise_row in row["consensus_gravity_error_by_noise_px"].values():
                self.assertLess(noise_row["p95"], 0.5)


if __name__ == "__main__":
    unittest.main()
