from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

import stage3a_camera_world as s3


OLD_TOOL_SHA256 = "18f281a4525ab57f95445448aefbbe24e7bbb4760a8a1119e0b9911e6684d4de"


def axis_angle(axis: list[float], degrees: float) -> np.ndarray:
    vector = np.asarray(axis, dtype=np.float64)
    vector /= np.linalg.norm(vector)
    rotation, _ = cv2.Rodrigues(vector * np.deg2rad(degrees))
    return rotation


def synthetic_camera_rotation() -> np.ndarray:
    return s3.project_to_rotation(
        axis_angle([0.0, 0.0, 1.0], 2.0)
        @ axis_angle([1.0, 0.0, 0.0], -3.0)
        @ s3.R_WORLD_FROM_BOARD
    )


def fixed_board_manifest() -> dict:
    value = s3.default_fixed_board_height()
    value["configured_at"] = "synthetic"
    return value


def synthetic_pose_inputs(include_w04: bool = True) -> tuple[np.ndarray, dict, dict]:
    truth = synthetic_camera_rotation()
    camera_world = np.asarray([0.0, 0.0, 972.0])
    R_cb = truth.T @ s3.R_WORLD_FROM_BOARD
    registry = s3.default_registry()
    reports = {}
    definitions = [("W01", 600.0), ("W02", 800.0), ("W03", 1000.0)]
    if include_w04:
        definitions.append(("W04", 805.0))
    for index, (pose_id, distance) in enumerate(definitions):
        registry["poses"][pose_id] = {
            "pose_id": pose_id,
            "scan_id": f"scan_synthetic_{pose_id}",
            "scan_path": f"C:\\synthetic\\scan_{pose_id}",
            "source_hashes": {f"synthetic_{pose_id}": "0" * 64},
            "board_mode": s3.BOARD_MODE,
            "board_moved_between_poses": False,
            "capture_mode": s3.CAPTURE_MODE,
            "distance_role": s3.POSE_DISTANCE_ROLES[pose_id],
            "vehicle_alignment_delta_mm": 2.0,
        }
        board_origin_world = np.asarray([5.0 * index, distance, 912.0 + 125.0])
        t_cb = truth.T @ (board_origin_world - camera_world)
        pose_rotation = truth
        if pose_id == "W04":
            pose_rotation = axis_angle([0, 0, 1], 0.10) @ truth
        frames = []
        for frame_index in range(11):
            frames.append(
                {
                    "frame_id": f"frame_{frame_index:06d}",
                    "R_camera_from_board": R_cb.tolist(),
                    "tvec_camera_from_board_mm": t_cb.tolist(),
                    "estimated_board_plane_distance_mm": distance,
                }
            )
        reports[pose_id] = {
            "pose_id": pose_id,
            "distance_role": s3.POSE_DISTANCE_ROLES[pose_id],
            "R_world_from_camera_pose_mean": pose_rotation.tolist(),
            "camera_valid_frame_count": 11,
            "reprojection": {
                "frame_rms_median_px": 0.12,
                "frame_rms_p95_px": 0.15,
                "frame_p95_max_px": 0.25,
            },
            "distance_and_image_coverage": {
                "estimated_board_plane_distance_mm": {"median": distance, "p05": distance, "p95": distance},
                "board_center_px_median": [320.0, 180.0],
                "board_coverage_fraction_median": 0.08,
            },
            "frames": frames,
        }
    return truth, reports, registry


def solve_with_synthetic(reports: dict, registry: dict) -> dict:
    authorities = {
        "stage2_bundle": {"path": str(s3.STAGE2_BUNDLE), "sha256": s3.STAGE2_SHA256},
        "old_stage7c_stale_reference": {
            "path": str(s3.OLD_STAGE7C),
            "sha256": s3.OLD_STAGE7C_SHA256,
            "classification": "STALE_PRE_REMOUNT_REFERENCE_ONLY",
            "numeric_authority": False,
        },
    }
    camera_provenance = {
        "stage2_bundle_sha256": s3.STAGE2_SHA256,
        "K": np.eye(3).tolist(),
        "D_brown5_k1_k2_p1_p2_k3": [0] * 5,
    }
    camera_height = {
        "camera_height_mm": 972.0,
        "camera_height_uncertainty_mm": 10.0,
        "measurement_method": "manual_tape_visual",
    }
    with mock.patch.object(s3, "verify_frozen_authorities", return_value=authorities), mock.patch.object(
        s3, "camera_model", return_value=(np.eye(3), np.zeros(5), camera_provenance)
    ), mock.patch.object(s3, "load_height", return_value=camera_height), mock.patch.object(
        s3, "load_fixed_board_height", return_value=fixed_board_manifest()
    ), mock.patch.object(s3, "load_registry", return_value=registry), mock.patch.object(
        s3, "process_pose", side_effect=lambda pose_id, binding, write: reports[pose_id]
    ):
        return s3.solve_registered(write=False)


class FrozenAuthorityTests(unittest.TestCase):
    def test_stage2_bundle_sha_exact_match(self) -> None:
        self.assertEqual(s3.sha256_file(s3.STAGE2_BUNDLE), s3.STAGE2_SHA256)

    def test_old_stage7c_is_not_numeric_authority(self) -> None:
        old = s3.verify_frozen_authorities()["old_stage7c_stale_reference"]
        self.assertEqual(old["classification"], "STALE_PRE_REMOUNT_REFERENCE_ONLY")
        self.assertFalse(old["numeric_authority"])

    def test_camera_K_D_come_from_stage2(self) -> None:
        K, D, provenance = s3.camera_model()
        self.assertEqual(K.shape, (3, 3))
        self.assertEqual(D.shape, (5,))
        self.assertAlmostEqual(K[0, 0], 868.6083708422947)
        self.assertEqual(provenance["stage2_bundle_sha256"], s3.STAGE2_SHA256)

    def test_board_schema_exact(self) -> None:
        self.assertEqual(s3.BOARD["dictionary"], "DICT_5X5_100")
        self.assertEqual((s3.BOARD["squares_x"], s3.BOARD["squares_y"]), (7, 5))
        self.assertEqual((s3.BOARD["active_width_mm"], s3.BOARD["active_height_mm"]), (175.0, 125.0))


class FixedBoardConfigurationTests(unittest.TestCase):
    def test_default_fixed_board_contract(self) -> None:
        value = s3.default_fixed_board_height()
        self.assertEqual(value["board_mode"], "FIXED_VERTICAL_BOARD")
        self.assertFalse(value["board_moved_between_poses"])
        self.assertEqual(value["fixed_board_active_bottom_height_mm"], 912.0)
        self.assertEqual(value["fixed_board_height_uncertainty_mm"], 5.0)

    def test_fixed_board_height_can_be_configured_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixed_board_height.json"
            args = argparse.Namespace(
                active_bottom_mm=912.0,
                uncertainty_mm=5.0,
                measurement_method="manual_tape_visual",
                raw_observation_mm=911.5,
            )
            with mock.patch.object(s3, "FIXED_BOARD_HEIGHT_PATH", path), contextlib.redirect_stdout(io.StringIO()):
                s3.cmd_set_board_height(args)
                value = s3.load_fixed_board_height()
            self.assertTrue(value["frozen"])
            self.assertEqual(value["fixed_board_active_bottom_height_mm"], 912.0)

    def test_fixed_board_height_freezes_after_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixed_board_height.json"
            s3.write_json(path, fixed_board_manifest())
            args = argparse.Namespace(
                active_bottom_mm=913.0,
                uncertainty_mm=5.0,
                measurement_method="manual_tape_visual",
                raw_observation_mm=911.5,
            )
            with mock.patch.object(s3, "FIXED_BOARD_HEIGHT_PATH", path):
                with self.assertRaisesRegex(s3.Stage3AError, "already frozen"):
                    s3.cmd_set_board_height(args)

    def test_register_cannot_silently_change_board_height(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixed_path = root / "fixed.json"
            registry_path = root / "registry.json"
            s3.write_json(fixed_path, fixed_board_manifest())
            s3.write_json(registry_path, s3.default_registry())
            args = argparse.Namespace(
                pose="W01", scan_id="scan_fake", latest=False,
                board_active_bottom_height_mm=911.0, board_height_uncertainty_mm=None,
                vehicle_alignment_delta_mm=2.0, capture_note=None, dry_run=False,
            )
            with mock.patch.object(s3, "FIXED_BOARD_HEIGHT_PATH", fixed_path), mock.patch.object(
                s3, "REGISTRY_PATH", registry_path
            ):
                with self.assertRaisesRegex(s3.Stage3AError, "mismatches frozen"):
                    s3.cmd_register(args)

    def test_register_uses_frozen_height_without_repeating_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixed_path = root / "fixed.json"
            registry_path = root / "observations" / "registry.json"
            s3.write_json(fixed_path, fixed_board_manifest())
            s3.write_json(registry_path, s3.default_registry())
            args = argparse.Namespace(
                pose="W01", scan_id="scan_fake", latest=False,
                board_active_bottom_height_mm=None, board_height_uncertainty_mm=None,
                vehicle_alignment_delta_mm=2.0, capture_note=None, dry_run=False,
            )
            with mock.patch.object(s3, "FIXED_BOARD_HEIGHT_PATH", fixed_path), mock.patch.object(
                s3, "REGISTRY_PATH", registry_path
            ), mock.patch.object(s3, "WORKSPACE", root), mock.patch.object(
                s3, "resolve_scan", return_value=root / "scan_fake"
            ), mock.patch.object(s3, "scan_hashes", return_value={"fake": "0" * 64}), contextlib.redirect_stdout(io.StringIO()):
                s3.cmd_register(args)
            binding = s3.read_json(registry_path)["poses"]["W01"]
            self.assertEqual(binding["fixed_board_active_bottom_height_mm"], 912.0)
            self.assertEqual(binding["distance_role"], "near")
            self.assertFalse(binding["board_moved_between_poses"])


class TransformContractTests(unittest.TestCase):
    def test_world_sign_and_column_vector_direction(self) -> None:
        R = synthetic_camera_rotation()
        self.assertTrue(s3.sign_checks(R, 972.0)["all_pass"])
        T = s3.make_transform(R, [0.0, 0.0, 972.0])
        point = np.asarray([12.0, -7.0, 800.0])
        np.testing.assert_allclose((T @ np.r_[point, 1.0])[:3], R @ point + [0.0, 0.0, 972.0])

    def test_rotation_is_proper_SO3(self) -> None:
        R = synthetic_camera_rotation()
        np.testing.assert_allclose(R.T @ R, np.eye(3), atol=1e-10)
        self.assertAlmostEqual(float(np.linalg.det(R)), 1.0, places=10)

    def test_camera_translation_is_measured_height(self) -> None:
        T = s3.make_transform(synthetic_camera_rotation(), [0.0, 0.0, 972.0])
        np.testing.assert_allclose(T[:3, 3], [0.0, 0.0, 972.0])

    def test_board_normal_sign_contract(self) -> None:
        np.testing.assert_allclose(s3.R_WORLD_FROM_BOARD @ [1, 0, 0], [1, 0, 0])
        np.testing.assert_allclose(s3.R_WORLD_FROM_BOARD @ [0, 1, 0], [0, 0, -1])
        np.testing.assert_allclose(s3.R_WORLD_FROM_BOARD @ [0, 0, 1], [0, 1, 0])


class RegistryAndSuggestionTests(unittest.TestCase):
    def test_dynamic_registry_has_no_pose_limit(self) -> None:
        registry = s3.default_registry()
        for index in range(30):
            registry["poses"][f"W{index:02d}"] = {"distance_role": "supplemental_distance"}
        self.assertEqual(len(registry["poses"]), 30)

    def test_duplicate_pose_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry_path = root / "registry.json"
            fixed_path = root / "fixed.json"
            registry = s3.default_registry()
            registry["poses"]["W01"] = {"scan_id": "scan_existing"}
            s3.write_json(registry_path, registry)
            s3.write_json(fixed_path, fixed_board_manifest())
            args = argparse.Namespace(
                pose="W01", scan_id="scan_new", latest=False,
                board_active_bottom_height_mm=None, board_height_uncertainty_mm=None,
                vehicle_alignment_delta_mm=None, capture_note=None, dry_run=False,
            )
            with mock.patch.object(s3, "REGISTRY_PATH", registry_path), mock.patch.object(
                s3, "FIXED_BOARD_HEIGHT_PATH", fixed_path
            ):
                with self.assertRaisesRegex(s3.Stage3AError, "duplicate pose ID refused"):
                    s3.cmd_register(args)

    def test_eleven_frames_are_one_physical_pose_vote(self) -> None:
        base = synthetic_camera_rotation()
        pose_a = [axis_angle([0, 0, 1], 4.0) @ base for _ in range(11)]
        pose_b = [axis_angle([0, 0, 1], -2.0) @ base for _ in range(11)]
        pose_c = [axis_angle([0, 0, 1], -2.0) @ base for _ in range(11)]
        equal_pose = s3.chordal_mean([s3.chordal_mean(values) for values in (pose_a, pose_b, pose_c)])
        incorrectly_repeated = s3.chordal_mean(pose_a + [s3.chordal_mean(pose_b), s3.chordal_mean(pose_c)])
        self.assertGreater(s3.rotation_distance_deg(equal_pose, incorrectly_repeated), 2.0)

    def test_suggest_sequence_is_vehicle_near_mid_far(self) -> None:
        registry = s3.default_registry()
        instructions = [s3.suggest(registry)["instruction"]]
        registry["poses"]["W01"] = {"distance_role": "near"}
        instructions.append(s3.suggest(registry)["instruction"])
        registry["poses"]["W02"] = {"distance_role": "mid"}
        instructions.append(s3.suggest(registry)["instruction"])
        joined = " ".join(instructions).lower()
        self.assertIn("w01 near", joined)
        self.assertIn("w02 mid", joined)
        self.assertIn("w03 far", joined)
        self.assertIn("charuco remains fixed", joined)
        self.assertNotIn("move board", joined)
        self.assertNotIn("middle height", joined)

    def test_stable_suggest_makes_W04_optional_vehicle_holdout(self) -> None:
        _, _, registry = synthetic_pose_inputs(include_w04=False)
        with mock.patch.object(s3, "solve_registered", return_value={
            "decision": "EVIDENCE_READY_FOR_OWNER_JUDGMENT"
        }):
            result = s3.suggest(registry)
        self.assertEqual(result["decision"], "EVIDENCE_READY_FOR_OWNER_JUDGMENT")
        self.assertIn("W04", result["instruction"])
        self.assertIn("vehicle", result["instruction"].lower())
        self.assertIn("ChArUco remains fixed", result["instruction"])

    def test_capture_guide_has_only_fixed_board_vehicle_distance_mode(self) -> None:
        guide = (s3.WORKSPACE / "CAPTURE_GUIDE_STAGE3A.md").read_text(encoding="utf-8")
        self.assertIn("FIXED_VERTICAL_BOARD", guide)
        self.assertIn("VEHICLE_MULTI_DISTANCE", guide)
        self.assertIn("W01 near", guide)
        self.assertIn("W02 mid", guide)
        self.assertIn("W03 far", guide)
        for forbidden in ("W01：低高度", "W02：中高度", "W03：高高度", "把板拆下", "<W01_MM>"):
            self.assertNotIn(forbidden, guide)


class FixedBoardMultiDistanceSyntheticTests(unittest.TestCase):
    def test_near_mid_far_recover_same_world_orientation(self) -> None:
        truth, reports, _ = synthetic_pose_inputs()
        rotations = s3.pose_rotations({pose_id: reports[pose_id] for pose_id in s3.PRIMARY_POSE_IDS})
        aggregate = s3.chordal_mean(rotations.values())
        self.assertLess(s3.rotation_distance_deg(aggregate, truth), 1e-5)

    def test_fixed_known_height_is_consistent_across_distance(self) -> None:
        truth, reports, _ = synthetic_pose_inputs()
        primary = {pose_id: reports[pose_id] for pose_id in s3.PRIMARY_POSE_IDS}
        result = s3.fixed_height_residuals(truth, primary, fixed_board_manifest(), 972.0)
        self.assertLess(result["overall_active_bottom_residual_mm"]["max_abs"], 1e-8)
        self.assertEqual(result["fixed_board_active_bottom_height_mm"], 912.0)

    def test_residual_vs_distance_diagnostic_exists(self) -> None:
        truth, reports, _ = synthetic_pose_inputs()
        primary = {pose_id: reports[pose_id] for pose_id in s3.PRIMARY_POSE_IDS}
        result = s3.fixed_height_residuals(truth, primary, fixed_board_manifest(), 972.0)
        self.assertIn("residual_vs_distance_slope_mm_per_m", result)
        self.assertTrue(result["distance_trend_exists"])

    def test_orientation_vs_distance_has_spread_and_trends(self) -> None:
        truth, reports, _ = synthetic_pose_inputs()
        primary = {pose_id: reports[pose_id] for pose_id in s3.PRIMARY_POSE_IDS}
        rotations = s3.pose_rotations(primary)
        result = s3.orientation_vs_distance(rotations, primary, truth)
        self.assertEqual(set(result["per_pose"]), set(s3.PRIMARY_POSE_IDS))
        self.assertIn("rpy_peak_to_peak_deg", result)
        self.assertIn("geodesic_residual_vs_distance_slope_deg_per_m", result)

    def test_physical_pose_lopo_recovery(self) -> None:
        truth = synthetic_camera_rotation()
        rotations = {
            "W01": axis_angle([1, 0, 0], 0.08) @ truth,
            "W02": axis_angle([0, 1, 0], -0.07) @ truth,
            "W03": axis_angle([0, 0, 1], 0.05) @ truth,
        }
        lopo = s3.lopo_diagnostics(rotations, s3.chordal_mean(rotations.values()))
        self.assertTrue(lopo["available"])
        self.assertLess(lopo["max_solution_delta_from_full_deg"], 0.1)

    def test_W04_is_holdout_and_does_not_enter_primary_solution(self) -> None:
        truth, reports, registry = synthetic_pose_inputs(include_w04=True)
        solution = solve_with_synthetic(reports, registry)
        self.assertEqual(solution["primary_solve_pose_ids"], ["W01", "W02", "W03"])
        self.assertTrue(solution["W04_reposition_holdout"]["excluded_from_primary_solve"])
        self.assertLess(s3.rotation_distance_deg(np.asarray(solution["R_world_from_camera"]), truth), 1e-5)

    def test_distance_coverage_reports_near_mid_far_PnP_values(self) -> None:
        _, reports, registry = synthetic_pose_inputs(include_w04=False)
        result = s3.coverage(registry, reports)
        self.assertTrue(result["enough_for_first_solve"])
        self.assertEqual(result["per_pose"]["W01"]["estimated_board_plane_distance_mm"], 600.0)
        self.assertEqual(result["per_pose"]["W03"]["estimated_board_plane_distance_mm"], 1000.0)
        self.assertIsNotNone(result["orientation_vs_distance"])
        self.assertIsNotNone(result["fixed_height_consistency_vs_distance"])

    def test_camera_height_sensitivity_remains_962_972_982(self) -> None:
        result = s3.height_sensitivity(972.0)
        self.assertEqual(result["evaluated_camera_heights_mm"], [962.0, 972.0, 982.0])
        self.assertEqual(result["world_z_constant_offset_from_nominal_mm"], [-10.0, 0.0, 10.0])

    def test_end_to_end_solution_and_candidate_fixed_board_fields(self) -> None:
        _, reports, registry = synthetic_pose_inputs(include_w04=True)
        solution = solve_with_synthetic(reports, registry)
        self.assertEqual(solution["decision"], "EVIDENCE_READY_FOR_OWNER_JUDGMENT")
        candidate = s3.candidate_from_solution(solution, registry)
        self.assertEqual(candidate["candidate_status"], "CANDIDATE_NOT_PROMOTED")
        self.assertEqual(candidate["board_mode"], "FIXED_VERTICAL_BOARD")
        self.assertFalse(candidate["board_moved_between_poses"])
        self.assertEqual(candidate["capture_mode"], "VEHICLE_MULTI_DISTANCE")
        self.assertEqual(candidate["distance_coverage"], "near_mid_far")
        self.assertEqual(candidate["fixed_board_active_bottom_height_mm"], 912.0)
        self.assertEqual(candidate["fixed_board_height_uncertainty_mm"], 5.0)
        self.assertFalse(candidate["absolute_geometry_valid"])

    def test_candidate_schema_requires_fixed_board_fields(self) -> None:
        schema = json.loads((s3.WORKSPACE / "candidate_schema.json").read_text(encoding="utf-8"))
        required = set(schema["required"])
        self.assertTrue({
            "board_mode", "board_moved_between_poses", "capture_mode", "distance_coverage",
            "fixed_board_active_bottom_height_mm", "fixed_board_height_uncertainty_mm",
            "fixed_height_consistency", "orientation_vs_distance",
        }.issubset(required))
        self.assertEqual(schema["properties"]["board_mode"]["const"], "FIXED_VERTICAL_BOARD")


class ProtectionAndCorrectionTests(unittest.TestCase):
    def test_new_tool_hash_differs_from_known_old_hash(self) -> None:
        self.assertNotEqual(s3.sha256_file(Path(s3.__file__)), OLD_TOOL_SHA256)

    def test_protected_hashes_unchanged(self) -> None:
        baseline = json.loads((s3.WORKSPACE / "protected_hashes_before.json").read_text(encoding="utf-8"))
        entries = [baseline["stage2_final_bundle"], baseline["old_stage7c_result"], *baseline["read_only_source_files"]]
        for entry in entries:
            self.assertEqual(s3.sha256_file(Path(entry["path"])), entry["sha256"], entry["path"])

    def test_formal_runtime_is_declared_forbidden_and_not_candidate_target(self) -> None:
        manifest = json.loads((s3.WORKSPACE / "workspace_manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["formal_runtime_modified"])
        self.assertIn("C:\\ec\\_deltar_formal", manifest["forbidden_write_roots"])
        self.assertTrue(str(s3.CANDIDATE).startswith(str(s3.WORKSPACE)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
