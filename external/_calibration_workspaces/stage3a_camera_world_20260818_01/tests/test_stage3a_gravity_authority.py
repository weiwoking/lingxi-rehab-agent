from __future__ import annotations

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE))
import stage3a_gravity_authority as authority
import stage3a_oblique_gravity_score as oblique
import stage3a_straightness_score as base


def fake_inspection(decision: str) -> dict:
    return {
        "schema_version": "stage3a_w04g_inspection.v1",
        "scan_id": "scan_test",
        "scan_path": "test",
        "frame_directory_count": 11,
        "valid_frame_count": 11,
        "good_frame_fraction": 1.0 if decision == "GOOD" else 0.0,
        "scan_score": {"decision": decision},
        "per_frame": [],
    }


class GravityAuthorityRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "observations").mkdir(parents=True)
        existing = {
            "schema_version": "stage3a_pose_registry.v1",
            "poses": {
                "W01": {"scan_path": str(self.root / "old1"), "scan_id": "old1"},
                "W02": {"scan_path": str(self.root / "old2"), "scan_id": "old2"},
                "W03": {"scan_path": str(self.root / "old3"), "scan_id": "old3"},
            },
        }
        authority.atomic_write_json(self.root / "observations" / "registry.json", existing)
        self.scan = self.root / "validated" / "scan_test"
        (self.scan / "frames").mkdir(parents=True)
        (self.scan / "scan.json").write_text("{}\n", encoding="utf-8")
        for index in range(11):
            frame = self.scan / "frames" / f"frame_{index:06d}"
            frame.mkdir()
            (frame / "frame.json").write_text("{}\n", encoding="utf-8")
            (frame / "tof.json").write_text("{}\n", encoding="utf-8")
            (frame / "image.rgb565").write_bytes(b"test")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def tool(self, decision: str) -> authority.GravityAuthorityTool:
        return authority.GravityAuthorityTool(
            workspace=self.root,
            stage2_bundle=base.STAGE2_BUNDLE,
            validated_scan_root=self.scan.parent,
            scan_inspector=lambda _: fake_inspection(decision),
        )

    def test_duplicate_w04g_refused(self) -> None:
        tool = self.tool("GOOD")
        tool.register_w04g(self.scan)
        with self.assertRaises(authority.GravityAuthorityError):
            tool.register_w04g(self.scan)

    def test_non_good_scan_cannot_extract_gravity(self) -> None:
        tool = self.tool("TOO_FRONTAL")
        result = tool.register_w04g(self.scan)
        self.assertFalse(result["gravity_eligible"])
        with self.assertRaises(authority.GravityAuthorityError):
            tool.extract_gravity()

    def test_summary_does_not_register_or_select_latest(self) -> None:
        tool = self.tool("GOOD")
        result = tool.summary()
        self.assertEqual(result["status"], "READY_FOR_ONE_W04G_CAPTURE")
        self.assertFalse(tool.registry_path.exists())

    def _prepare_export_state(self, validation_status: str) -> authority.GravityAuthorityTool:
        tool = self.tool("GOOD")
        tool.register_w04g(self.scan)
        extraction_path = tool.diagnostics / "extract.json"
        validation_path = tool.diagnostics / "validation.json"
        extraction = {
            "status": "GRAVITY_READY_FOR_CROSS_DISTANCE_VALIDATION",
            "gravity_authority": {"u_up_camera": [0.0, -1.0, 0.0], "uncertainty_deg": 0.25},
        }
        validation = {
            "status": validation_status,
            "candidate_export_allowed": validation_status == "READY_FOR_STAGE3B_Z_VALIDATION",
            "per_pose": {},
            "residual_vs_distance_slope_mm_per_m": 1.0,
        }
        authority.atomic_write_json(extraction_path, extraction)
        authority.atomic_write_json(validation_path, validation)
        registry = tool.load_registry()
        registry["w04g"]["extraction"] = {
            "path": str(extraction_path), "sha256": base.sha256(extraction_path),
            "status": "GRAVITY_READY_FOR_CROSS_DISTANCE_VALIDATION",
        }
        registry["w04g"]["cross_distance_validation"] = {
            "path": str(validation_path), "sha256": base.sha256(validation_path), "status": validation_status,
        }
        authority.atomic_write_json(tool.registry_path, registry)
        return tool

    def test_failure_case_keeps_candidate_absent(self) -> None:
        tool = self._prepare_export_state("GRAVITY_ROUTE_FAILED")
        with self.assertRaises(authority.GravityAuthorityError):
            tool.export_gravity_candidate()
        self.assertFalse(tool.candidate_path.exists())

    def test_success_candidate_schema_and_yaw_boundary(self) -> None:
        tool = self._prepare_export_state("READY_FOR_STAGE3B_Z_VALIDATION")
        candidate = tool.export_gravity_candidate()
        self.assertTrue(tool.candidate_path.exists())
        self.assertEqual(candidate["gravity_status"], "READY_FOR_STAGE3B_Z_VALIDATION")
        self.assertEqual(candidate["yaw_status"], "SEPARATE_LOW_CONFIDENCE")
        self.assertFalse(candidate["gravity_authority"]["full_world_rotation_authority"])
        self.assertFalse(candidate["promotion"])


class GravityAuthorityMathTests(unittest.TestCase):
    def test_gravity_sign_and_yaw_invariance(self) -> None:
        self.assertTrue(np.allclose(oblique.gravity_from_R_camera_from_board(np.eye(3)), [0, -1, 0]))
        self.assertTrue(oblique.gravity_yaw_invariance_numeric()["pass"])

    def test_robust_vector_aggregation_rejects_outlier_and_normalizes(self) -> None:
        vectors = [np.asarray([0.001 * value, -1.0, 0.0]) for value in (-2, -1, 0, 1, 2)]
        vectors.append(np.asarray([1.0, 0.0, 0.0]))
        result = authority.robust_unit_vector(vectors, minimum_inliers=3)
        self.assertAlmostEqual(float(np.linalg.norm(result["vector"])), 1.0, places=12)
        self.assertIn(5, result["outlier_indices"])
        self.assertLess(oblique.angular_distance_deg(result["vector"], [0, -1, 0]), 0.1)

    def test_solver_disagreement_and_branch_catastrophe_fail_gate(self) -> None:
        self.assertTrue(authority.gravity_extraction_ready(11, 11, 0.5, 0.2, 0))
        self.assertFalse(authority.gravity_extraction_ready(11, 11, 0.5, 1.0, 0))
        self.assertFalse(authority.gravity_extraction_ready(11, 11, 0.5, 0.2, 1))

    def test_ippe_lowest_reprojection_branch_selection(self) -> None:
        K, D, _ = base.nominal_camera_model()
        board, _ = base.board_and_detector()
        objects = np.asarray(board.getChessboardCorners(), dtype=np.float64).reshape(-1, 3)
        up = oblique.normalize(np.asarray([0.006, -0.9998, 0.017]))
        frame, _, _ = oblique.synthetic_frame(objects, up, 30, 0.8, 0, 0.15, 99, K, D, K, D, "ippe")
        solutions = oblique.solve_frame(frame, K, D)["IPPE"]
        self.assertEqual(len(solutions), 2)
        self.assertLessEqual(solutions[0]["rms_px"], solutions[1]["rms_px"])
        self.assertIs(oblique.selected_solution(solutions), solutions[0])

    def test_fit_layer_does_not_access_height_truth(self) -> None:
        source = inspect.getsource(oblique.gravity_fixed_fit)
        for forbidden in ("972", "912", "1037", "CAMERA_HEIGHT", "BOARD_BOTTOM"):
            self.assertNotIn(forbidden, source)

    def test_synthetic_near_mid_far_slope_suppression(self) -> None:
        result = oblique.synthetic_end_to_end(trials=8)
        self.assertTrue(result["pass"])
        self.assertGreater(result["unconstrained_height_slope_mm_per_m"]["p95"], 40)
        self.assertLess(result["gravity_fixed_height_slope_mm_per_m"]["p95"], 10)


class GravityAuthorityBoundaryTests(unittest.TestCase):
    def test_existing_registry_and_stage2_exact(self) -> None:
        before = base.sha256(base.REGISTRY)
        self.assertEqual(before, authority.EXPECTED_EXISTING_REGISTRY_SHA256)
        self.assertEqual(base.sha256(base.STAGE2_BUNDLE), authority.EXPECTED_STAGE2_SHA256)
        self.assertEqual(base.sha256(base.REGISTRY), before)

    def test_protected_hashes_unchanged(self) -> None:
        checks = {
            WORKSPACE / "stage3a_camera_world.py": authority.EXPECTED_MAIN_TOOL_SHA256,
            WORKSPACE / "stage3a_straightness_score.py": authority.EXPECTED_STRAIGHTNESS_SCORER_SHA256,
            WORKSPACE / "stage3a_oblique_gravity_score.py": authority.EXPECTED_OBLIQUE_SCORER_SHA256,
            Path(r"C:\ec\_deltar_p4_pipeline\src\deltar_p4_pipeline\camera_world_calibration.py"): "6b6726082a97b7a071f3cd994d1ead80c2ccfee9b9f235bea847b1ccdacaadd0",
            Path(r"C:\calibration\stage7c_world_result_robustfix_20260724_044035\camera_world_pose.v1.json"): "5c730171f850ff3e05ab777eecca9849714aa20376c6d1f001da2b378ad24619",
        }
        for path, expected in checks.items():
            self.assertEqual(base.sha256(path), expected)

    def test_no_automatic_capture_or_registration(self) -> None:
        parser = authority.build_parser()
        commands = set(parser._subparsers._group_actions[0].choices)
        self.assertNotIn("capture", commands)
        self.assertIn("register-w04g", commands)
        source = inspect.getsource(authority.GravityAuthorityTool.summary)
        self.assertNotIn("register_w04g", source)
        self.assertNotIn("resolve_latest", source)


if __name__ == "__main__":
    unittest.main()
