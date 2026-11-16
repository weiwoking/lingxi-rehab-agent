from __future__ import annotations

import tempfile
import unittest
import warnings
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import deltar_p4_pipeline.camera_tof_calibration as calibration_module
from deltar_p4_pipeline.camera_tof_calibration import (
    PlaneObservation,
    SolverResult,
    calibrate_capture,
    enumerate_candidates,
    mechanical_diagnostics,
    nominal_zone_rays,
    transform_plane_camera_to_tof,
)
from deltar_p4_pipeline.camera_tof_contracts import CameraTofError
from deltar_p4_pipeline.camera_tof_contracts import validate_profile
from deltar_p4_pipeline.camera_tof_outputs import build_profile, select_candidate, write_overlays


def synthetic_observations() -> list[PlaneObservation]:
    rotation, _ = cv2.Rodrigues(np.asarray([0.025, -0.018, 0.012]))
    translation = np.asarray([-20.0, -3.5, 6.5])
    rays = nominal_zone_rays()
    normals = [
        [0.00, 0.00, 1.00], [0.22, 0.02, 0.975], [-0.20, 0.07, 0.977],
        [0.02, 0.24, 0.970], [0.08, -0.23, 0.970], [0.17, -0.16, 0.972],
        [-0.15, -0.17, 0.974], [0.14, 0.18, 0.973], [-0.08, 0.25, 0.965],
        [0.24, -0.08, 0.967], [-0.19, 0.19, 0.963], [0.21, 0.15, 0.966],
    ]
    values = []
    for index, normal_value in enumerate(normals):
        normal = np.asarray(normal_value, dtype=np.float64)
        normal /= np.linalg.norm(normal)
        offset = -(360.0 + index * 27.0)
        normal_tof, offset_tof = transform_plane_camera_to_tof(normal, offset, rotation, translation)
        axial_distance = -offset_tof / ((rays / rays[:, 2:3]) @ normal_tof)
        values.append(PlaneObservation(
            pose_name=f"pose_{index:02d}", split="train" if index < 8 else "heldout",
            normal_camera=normal, offset_camera_mm=offset,
            raw_distance_mm=axial_distance, raw_mad_mm=np.full(64, 0.2),
            raw_sigma_mm=np.full(64, 1.0), raw_valid=np.ones(64, dtype=bool),
            raw_rejections=tuple(() for _ in range(64)), frame_count=5,
            camera_diagnostics={"rms_px_median": 0.2},
            board_polygon=np.asarray([[100, 100], [400, 100], [400, 300], [100, 300]], dtype=float),
            image_rgb=np.zeros((360, 640, 3), dtype=np.uint8), source_hashes={},
        ))
    return values


class IntegrationTests(unittest.TestCase):
    def test_gauge_tie_break_prefers_expected_negative_camera_x(self) -> None:
        observations = synthetic_observations()
        summary = {
            "count": 100,
            "mean_mm": 2.0,
            "median_mm": 2.0,
            "rms_mm": 3.0,
            "p95_mm": 5.0,
            "max_mm": 7.0,
        }

        def candidate(
            candidate_id: str,
            orientation_class: str,
            semantics: str,
            tx_mm: float,
            heldout_rms: float,
        ) -> dict[str, object]:
            heldout = {**summary, "rms_mm": heldout_rms}
            return {
                "candidate_id": candidate_id,
                "status": "success",
                "orientation": "identity",
                "orientation_class": orientation_class,
                "distance_semantics": semantics,
                "translation_mm": [tx_mm, 0.0, 0.0],
                "rotation_matrix": np.eye(3).tolist(),
                "train": dict(summary),
                "heldout": heldout,
                "stability": {
                    "successful_starts": 3,
                    "rotation_spread_deg": 0.1,
                    "translation_spread_mm": 0.1,
                },
                "rank": 6,
                "condition_number": 100.0,
                "mechanical": mechanical_diagnostics(
                    np.eye(3), [tx_mm, 0.0, 0.0]
                ),
            }

        ranking = [
            candidate("A", "proper_rotation_gauge", "axial_z", -20.0, 3.0),
            candidate("B", "proper_rotation_gauge", "axial_z", 20.0, 3.0),
            candidate("reflection", "reflection", "axial_z", -20.0, 33.0),
            candidate("radial", "proper_rotation_gauge", "radial_range", -20.0, 15.0),
        ]
        best, _, _, _ = select_candidate(ranking, observations)
        self.assertIsNotNone(best)
        assert best is not None
        self.assertEqual(best["candidate_id"], "A")

    def test_candidate_enumeration_reports_gauge_and_rejects_wrong_parity(self) -> None:
        observations = synthetic_observations()
        ranking, _ = enumerate_candidates(observations)
        self.assertEqual(len(ranking), 16)
        best, margin, gates, warnings = select_candidate(ranking, observations)
        self.assertIsNotNone(best)
        assert best is not None
        self.assertEqual(best["distance_semantics"], "axial_z")
        self.assertEqual(best["orientation_class"], "proper_rotation_gauge")
        self.assertGreaterEqual(len(margin["gauge_candidate_ids"]), 2)
        self.assertIn("D4_PROPER_ROTATION_GAUGE_EQUIVALENCE", warnings)
        self.assertEqual(next(item for item in gates if item["name"] == "reflection_separation")["status"], "PASS")
        self.assertEqual(next(item for item in gates if item["name"] == "distance_semantics_separation")["status"], "PASS")

    def test_synthetic_pass_profile_keeps_world_flags_false_and_writes_overlays(self) -> None:
        observations = synthetic_observations()
        ranking, diagnostics = enumerate_candidates(observations)
        best, margin, gates, warnings = select_candidate(ranking, observations)
        self.assertIsNotNone(best)
        self.assertTrue(all(item["status"] == "PASS" for item in gates))
        assert best is not None
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            overlap, _ = write_overlays(
                output, observations, best, diagnostics[best["candidate_id"]],
                np.asarray([[881.7, 0, 337.3], [0, 881.8, 188.2], [0, 0, 1.0]]),
                np.zeros(5),
            )
            profile = build_profile(
                best, margin, observations, diagnostics[best["candidate_id"]],
                {
                    "camera_profile_id": "synthetic_camera",
                    "source_hashes": {},
                    "created_at": "2026-07-23T00:00:00Z",
                },
                overlap, warnings,
            )
            validate_profile(profile, require_pass=True)
            self.assertFalse(profile["absolute_geometry_valid"])
            self.assertFalse(profile["world_pose_resolved"])
            self.assertEqual(profile["camera_tof_extrinsics"], {"status": "measured"})
            self.assertEqual(profile["mechanical_prior"]["expected_tx_mm"], -20.0)
            self.assertEqual(len(list((output / "overlays").glob("*.png"))), 36)

    def test_nonfinite_board_polygon_is_skipped_without_cast_warning(self) -> None:
        observations = synthetic_observations()
        observations[0] = replace(
            observations[0],
            board_polygon=np.asarray(
                [[np.nan, 100.0], [400.0, 100.0], [400.0, 300.0], [100.0, 300.0]]
            ),
        )
        ranking, diagnostics = enumerate_candidates(observations)
        best, _, _, _ = select_candidate(ranking, observations)
        self.assertIsNotNone(best)
        assert best is not None
        with tempfile.TemporaryDirectory() as raw, warnings.catch_warnings(
            record=True
        ) as caught:
            warnings.simplefilter("always")
            write_overlays(
                Path(raw),
                observations,
                best,
                diagnostics[best["candidate_id"]],
                np.asarray(
                    [[881.7, 0, 337.3], [0, 881.8, 188.2], [0, 0, 1.0]]
                ),
                np.zeros(5),
            )
        self.assertFalse(
            any("invalid value encountered in cast" in str(item.message) for item in caught)
        )
        self.assertEqual(
            diagnostics[best["candidate_id"]][observations[0].pose_name][
                "board_polygon_overlay"
            ]["status"],
            "SKIPPED_NONFINITE",
        )

    def test_input_failure_never_writes_formal_profile(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest = root / "bad.json"
            camera = root / "camera.json"
            board = root / "board.json"
            output = root / "output"
            manifest.write_text("{}", encoding="utf-8")
            camera.write_text("{}", encoding="utf-8")
            board.write_text("{}", encoding="utf-8")
            with self.assertRaises(CameraTofError):
                calibrate_capture(manifest, camera, board, output)
            self.assertTrue((output / "calibration_report.json").is_file())
            self.assertFalse((output / "camera_tof_extrinsics.v1.json").exists())

    def test_heldout_planes_do_not_participate_in_optimization(self) -> None:
        observations = synthetic_observations()
        changed = [
            replace(item, offset_camera_mm=item.offset_camera_mm + 120.0)
            if item.split == "heldout" else item
            for item in observations
        ]
        original_ranking, _ = enumerate_candidates(observations)
        changed_ranking, _ = enumerate_candidates(changed)
        original = next(item for item in original_ranking if item["candidate_id"] == "identity/axial_z")
        altered = next(item for item in changed_ranking if item["candidate_id"] == "identity/axial_z")
        np.testing.assert_allclose(original["rotation_matrix"], altered["rotation_matrix"], atol=1e-10)
        np.testing.assert_allclose(original["translation_mm"], altered["translation_mm"], atol=1e-8)
        self.assertNotAlmostEqual(original["heldout"]["rms_mm"], altered["heldout"]["rms_mm"], places=3)

    def test_heldout_never_selects_internal_solution(self) -> None:
        marker = synthetic_observations()[0]

        def candidate_dataset(
            observations: list[PlaneObservation], orientation: str, semantics: str,
        ) -> tuple[dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]], dict[str, object]]:
            del orientation, semantics
            train_points = np.zeros((12, 3), dtype=np.float64)
            train_normals = np.tile([1.0, 0.0, 0.0], (12, 1))
            train_offsets = np.zeros(12, dtype=np.float64)
            held_points = np.zeros((4, 3), dtype=np.float64)
            held_normals = np.tile([1.0, 0.0, 0.0], (4, 1))
            held_offsets = np.full(4, observations[0].offset_camera_mm, dtype=np.float64)
            return {
                "train": (train_points, train_normals, train_offsets, np.arange(12)),
                "heldout": (held_points, held_normals, held_offsets, np.arange(4)),
            }, {}

        def fake_solve(
            points: np.ndarray, normals: np.ndarray, offsets: np.ndarray, *,
            initial_rotation_vector: np.ndarray, initial_translation: np.ndarray,
            **_: object,
        ) -> SolverResult:
            del points, normals, offsets
            tx = float(initial_translation[0])
            train_residual = 1.0 if tx == 0.0 else 2.0
            return SolverResult(
                rotation=np.eye(3), translation=np.asarray([tx, 0.0, 0.0]),
                residuals=np.full(12, train_residual), converged=True,
                iterations=1, rank=6, condition_number=10.0,
                robust_scale_mm=1.0, cost=train_residual,
                initial_rotation_vector=np.asarray(initial_rotation_vector),
                initial_translation=np.asarray(initial_translation),
            )

        starts = [
            ("train_best", np.zeros(3), np.asarray([0.0, 0.0, 0.0])),
            ("heldout_favourite", np.zeros(3), np.asarray([10.0, 0.0, 0.0])),
        ]
        original = [replace(marker, offset_camera_mm=-10.0)]
        changed = [replace(marker, offset_camera_mm=0.0)]
        with (
            patch.object(calibration_module, "D4_STRATEGIES", ("identity",)),
            patch.object(calibration_module, "_initializations", return_value=starts),
            patch.object(calibration_module, "_candidate_dataset", side_effect=candidate_dataset),
            patch.object(calibration_module, "solve_point_to_plane", side_effect=fake_solve),
        ):
            original_ranking, _ = enumerate_candidates(original)
            changed_ranking, _ = enumerate_candidates(changed)

        first = next(item for item in original_ranking if item["candidate_id"] == "identity/axial_z")
        second = next(item for item in changed_ranking if item["candidate_id"] == "identity/axial_z")
        self.assertEqual(first["selected_start"], "train_best")
        self.assertEqual(second["selected_start"], "train_best")
        self.assertEqual(first["internal_solution_selection"], "train_only")
        self.assertTrue(all("heldout" not in item for item in first["starts"] if item["status"] == "success"))
        np.testing.assert_array_equal(first["rotation_matrix"], second["rotation_matrix"])
        np.testing.assert_array_equal(first["translation_mm"], second["translation_mm"])
        self.assertNotEqual(first["heldout"]["rms_mm"], second["heldout"]["rms_mm"])

    def test_formal_pose_gate_requires_8_train_and_4_heldout(self) -> None:
        observations = synthetic_observations()
        ranking, _ = enumerate_candidates(observations)
        cases = (
            ("7_train_4_heldout", observations[1:], "train_pose_count"),
            ("8_train_3_heldout", observations[:-1], "heldout_pose_count"),
        )
        for name, subset, failed_gate in cases:
            with self.subTest(name=name):
                _, _, gates, _ = select_candidate(ranking, subset)
                statuses = {item["name"]: item["status"] for item in gates}
                self.assertEqual(statuses[failed_gate], "FAIL")
        _, _, complete_gates, _ = select_candidate(ranking, observations)
        complete_statuses = {item["name"]: item["status"] for item in complete_gates}
        self.assertEqual(complete_statuses["train_pose_count"], "PASS")
        self.assertEqual(complete_statuses["heldout_pose_count"], "PASS")

    def test_synthetic_pass_writes_profile_and_pose_gate_fail_does_not(self) -> None:
        observations = synthetic_observations()
        ranking, diagnostics = enumerate_candidates(observations)
        metadata = {
            "camera_profile_id": "synthetic_camera",
            "source_hashes": {},
            "matrix": np.asarray([[881.7, 0, 337.3], [0, 881.8, 188.2], [0, 0, 1.0]]),
            "distortion": np.zeros(5),
        }
        manifest = {"capture_id": "synthetic_capture"}

        def run(root: Path, values: list[PlaneObservation]) -> dict[str, object]:
            with (
                patch.object(
                    calibration_module, "load_capture_observations",
                    return_value=(manifest, values, metadata),
                ),
                patch.object(
                    calibration_module, "enumerate_candidates",
                    return_value=(ranking, diagnostics),
                ),
                patch(
                    "deltar_p4_pipeline.camera_tof_outputs.write_overlays",
                    return_value=([True] * 64, {}),
                ),
                patch("deltar_p4_pipeline.camera_tof_outputs.write_residual_csv"),
            ):
                return calibrate_capture(
                    root / "capture.json", root / "camera.json", root / "board.json",
                    root / "output",
                )

        with tempfile.TemporaryDirectory() as raw_pass:
            pass_root = Path(raw_pass)
            report = run(pass_root, observations)
            self.assertEqual(report["status"], "PASS")
            profile_path = pass_root / "output" / "camera_tof_extrinsics.v1.json"
            self.assertTrue(profile_path.is_file())

        with tempfile.TemporaryDirectory() as raw_fail:
            fail_root = Path(raw_fail)
            report = run(fail_root, observations[1:])
            self.assertEqual(report["status"], "FAIL")
            self.assertFalse((fail_root / "output" / "camera_tof_extrinsics.v1.json").exists())


if __name__ == "__main__":
    unittest.main()
