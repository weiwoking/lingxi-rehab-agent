from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from deltar_p4_pipeline.camera_tof_calibration import manifest_template, nominal_zone_rays
from deltar_p4_pipeline.camera_tof_contracts import (
    CameraTofError,
    PROFILE_KEYS,
    prepare_output_dir,
    sha256_file,
    validate_capture_manifest,
    validate_profile,
    validate_report,
)


class ContractTests(unittest.TestCase):
    def test_manifest_template_uses_actual_900_by_600_backer(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            camera = root / "camera.json"
            board = root / "board.json"
            camera.write_text("{}", encoding="utf-8")
            board.write_text("{}", encoding="utf-8")
            manifest = manifest_template(
                root / "capture.json", camera_profile=camera, board_manifest=board,
            )
            self.assertEqual(manifest["backer"]["width_mm"], 900.0)
            self.assertEqual(manifest["backer"]["height_mm"], 600.0)

    def test_capture_is_closed_world_and_hash_checked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            camera = root / "camera.json"
            board = root / "board.json"
            scan = root / "scan"
            scan.mkdir()
            camera.write_text("{}", encoding="utf-8")
            board.write_text("{}", encoding="utf-8")
            value = {
                "schema_version": "camera_tof_capture.v1", "capture_id": "capture_1",
                "created_at": "2026-07-23T00:00:00Z",
                "camera_profile": "camera.json", "camera_profile_sha256": sha256_file(camera),
                "board_manifest": "board.json", "board_manifest_sha256": sha256_file(board),
                "backer": {"width_mm": 600, "height_mm": 600, "material": "matte board", "matte": True, "opaque": True},
                "poses": [
                    {"pose_name": "train", "split": "train", "scan_dir": "scan", "stationary_confirmed": True,
                     "estimated_distance_mm": 400, "tilt_hint": "left", "image_region_hint": "centre", "notes": ""},
                    {"pose_name": "held", "split": "heldout", "scan_dir": "scan", "stationary_confirmed": True,
                     "estimated_distance_mm": 500, "tilt_hint": "right", "image_region_hint": "edge", "notes": ""},
                ],
            }
            path = root / "capture.json"
            validated = validate_capture_manifest(value, manifest_path=path)
            self.assertEqual(len(validated["_resolved_poses"]), 2)
            invalid = {**value, "unknown": 1}
            with self.assertRaises(CameraTofError):
                validate_capture_manifest(invalid, manifest_path=path)
            invalid_hash = {**value, "camera_profile_sha256": "0" * 64}
            with self.assertRaises(CameraTofError):
                validate_capture_manifest(invalid_hash, manifest_path=path)

    def test_profile_transform_flags_and_closed_world(self) -> None:
        rotation = np.eye(3)
        translation = np.asarray([-20.0, -3.5, 6.5])
        transform = np.eye(4)
        transform[:3, 3] = translation
        profile = {
            "schema_version": "camera_tof_extrinsics.v1", "profile_id": "profile",
            "created_at": "2026-07-23T00:00:00Z", "camera_profile_id": "camera",
            "camera_resolution": [640, 360], "tof_model": "VL53L5CX",
            "tof_resolution": [8, 8], "tof_frequency_hz": 15,
            "transform_direction": "T_camera_from_tof",
            "matrix_convention": "row-major, column-vector",
            "T_camera_from_tof": transform.tolist(), "rotation_matrix": rotation.tolist(),
            "translation": translation.tolist(), "translation_unit": "mm",
            "orientation": {"name": "identity", "raw_index_for_canonical_index": list(range(64))},
            "orientation_resolution_status": "canonicalized_gauge",
            "zone_index_contract": "raw to canonical",
            "ray_model": {
                "name": "nominal", "hfov_deg": 45.0, "vfov_deg": 45.0,
                "provenance": "synthetic", "distance_semantics": "axial_z",
            },
            "distance_semantics": "axial_z", "zone_rays": nominal_zone_rays().tolist(),
            "zone_valid_mask": [True] * 64, "camera_tof_overlap_mask": [True] * 64,
            "solver_method": "test", "training_pose_count": 8, "heldout_pose_count": 4,
            "training_point_count": 300, "heldout_point_count": 120,
            "residual_summary": {
                "train": {"count": 300, "mean_mm": 1.0, "median_mm": 1.0, "rms_mm": 1.0, "p95_mm": 2.0, "max_mm": 3.0},
                "heldout": {"count": 120, "mean_mm": 1.0, "median_mm": 1.0, "rms_mm": 1.0, "p95_mm": 2.0, "max_mm": 3.0},
            },
            "candidate_margin": {"absolute_mm": 1.0, "relative": 0.5, "next_group": "x", "gauge_candidate_ids": ["identity/axial_z"]},
            "gauge_equivalence": {"present": False, "candidate_ids": ["identity/axial_z"], "canonicalization": "test"},
            "mechanical_prior": {
                "tof_is_camera_right": True, "expected_tx_mm": -20.0,
                "expected_abs_ty_mm": 3.5, "expected_abs_tz_mm": 6.5, "usage": "test",
            },
            "mechanical_prior_is_measured": False,
            "validation_status": "PASS", "camera_tof_extrinsics": {"status": "measured"},
            "absolute_geometry_valid": False, "world_pose_resolved": False,
            "source_data_hashes": {}, "warnings": [], "unresolved_fields": ["world_pose"],
        }
        self.assertEqual(set(profile), PROFILE_KEYS)
        validate_profile(profile, require_pass=True)
        with self.assertRaises(CameraTofError):
            validate_profile({**profile, "absolute_geometry_valid": True})
        with self.assertRaises(CameraTofError):
            validate_profile({**profile, "translation_unit": "m"})
        with self.assertRaises(CameraTofError):
            validate_profile({**profile, "transform_direction": "T_tof_from_camera"})
        contradictory = {
            **profile,
            "mechanical_prior": {
                **profile["mechanical_prior"],
                "expected_tx_mm": 20.0,
            },
        }
        with self.assertRaises(CameraTofError):
            validate_profile(contradictory)
        with self.assertRaises(CameraTofError):
            validate_profile({**profile, "unknown": 1})

    def test_output_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "new"
            self.assertEqual(prepare_output_dir(target), target.resolve())
            (target / "result.txt").write_text("occupied", encoding="utf-8")
            with self.assertRaises(CameraTofError):
                prepare_output_dir(target)

    def test_report_is_closed_world_and_keeps_world_unresolved(self) -> None:
        report = {
            "schema_version": "camera_tof_calibration_report.v1",
            "created_at": "2026-07-23T00:00:00Z", "capture_id": "capture",
            "status": "FAIL", "validation_status": "FAIL",
            "selected_candidate": None, "training_pose_count": 8,
            "heldout_pose_count": 4,
            "gates": [{"name": "heldout", "status": "FAIL", "details": {"rms_mm": 30}}],
            "warnings": [], "formal_profile_generated": False,
            "camera_tof_extrinsics_status": "unresolved",
            "absolute_geometry_valid": False, "world_pose_resolved": False,
            "tool_version": "test",
        }
        validate_report(report)
        with self.assertRaises(CameraTofError):
            validate_report({**report, "unknown": True})
        with self.assertRaises(CameraTofError):
            validate_report({**report, "absolute_geometry_valid": True})


if __name__ == "__main__":
    unittest.main()
