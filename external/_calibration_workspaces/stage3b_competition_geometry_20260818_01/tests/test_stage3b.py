from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE))

import stage3b_competition_geometry as tool
from stage2_dynamic_fr_adapter import (
    dynamic_fr_from_tof,
    load_stage2_bundle,
    reference_parity,
)
from target_matching import match_targets
from z_only_geometry_adapter import world_z_from_camera_points


class DynamicFrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_stage2_bundle(tool.STAGE2_PATH)

    def test_exact_reference_parity(self) -> None:
        evidence = reference_parity(self.bundle)
        self.assertTrue(evidence["all_pass"], evidence)
        self.assertGreaterEqual(evidence["fixture_count"], 2)

    def test_per_zone_depth_changes_fr_and_invalid_policy_is_masked(self) -> None:
        distance = np.linspace(700.0, 1700.0, 64)
        tof = {
            "distance_mm": distance.tolist(),
            "target_status": [5] * 64,
            "nb_target_detected": [1] * 64,
        }
        direct, audit = dynamic_fr_from_tof(self.bundle, tof, "direct_640x360")
        letterbox, _ = dynamic_fr_from_tof(self.bundle, tof, "letterbox_640x480")
        self.assertEqual(direct.shape, (64, 4))
        np.testing.assert_allclose(
            letterbox[:, (0, 2)], direct[:, (0, 2)] + 60.0, atol=2e-5
        )
        np.testing.assert_allclose(
            letterbox[:, (1, 3)], direct[:, (1, 3)], atol=2e-5
        )
        self.assertEqual(audit["valid_zone_count"], 64)


class GravityTests(unittest.TestCase):
    def test_world_z_is_dot_plus_camera_height(self) -> None:
        candidate, _ = tool.load_gravity_candidate(tool.STAGE3A_PATH)
        points = np.asarray([[1.0, 2.0, 3.0], [-5.0, 7.0, 11.0]])
        expected = (
            points @ np.asarray(candidate["gravity_authority"]["u_up_camera"])
            + float(candidate["camera_height_mm"])
        )
        np.testing.assert_allclose(world_z_from_camera_points(points, candidate), expected)
        self.assertFalse(candidate["gravity_authority"]["full_world_rotation_authority"])


class RegistryTests(unittest.TestCase):
    def _make_scan(self, root: Path, index: int) -> Path:
        scan = root / f"scan_dynamic_{index:03d}"
        scan.mkdir()
        (scan / "scan.json").write_text(
            json.dumps({"created_at": f"2026-08-18T00:{index:02d}:00Z"}),
            encoding="utf-8",
        )
        (scan / ".validation_receipt.json").write_text("{}", encoding="utf-8")
        return scan

    def test_unbounded_roles_and_duplicate_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry_path = root / "registry.json"
            with (
                mock.patch.object(tool, "VALIDATED_ROOT", root),
                mock.patch.object(tool, "REGISTRY_PATH", registry_path),
                mock.patch.object(tool, "analyze_sample", side_effect=lambda sample_id: {"sample_id": sample_id}),
            ):
                for index in range(1, 6):
                    self._make_scan(root, index)
                    tool.register_latest()
                registry = tool.load_registry()
                self.assertEqual(len(registry["samples"]), 5)
                self.assertEqual(registry["samples"][0]["role"], "PRIMARY_COMPETITION_VIEW")
                self.assertTrue(
                    all(
                        item["role"] == "ROBUSTNESS_EXTRA"
                        for item in registry["samples"][1:]
                    )
                )
                self.assertIsNone(registry["capture_count_limit"])
                with self.assertRaises(tool.Stage3BError):
                    tool.register_latest()


class MatchingTests(unittest.TestCase):
    def test_vertical_bottle_order_and_sports_ball_class(self) -> None:
        truth = tool.read_json(tool.TRUTH_PATH)
        observations = []
        for frame_index in range(4):
            observations.extend(
                [
                    {
                        "class_name": "bottle", "frame_id": f"f{frame_index}",
                        "sequence": frame_index, "logical_object_id": f"u{frame_index}",
                        "bbox_xyxy": [10, 40, 30, 100], "bbox_center_y": 70,
                        "support_height_mm": 1060 + frame_index, "object_height_mm": 181,
                        "top_z_mm": 1241 + frame_index, "distance_mm": 1800,
                        "confidence": .9, "support_source": "matched_plane", "region_source": "mask",
                    },
                    {
                        "class_name": "bottle", "frame_id": f"f{frame_index}",
                        "sequence": frame_index, "logical_object_id": f"l{frame_index}",
                        "bbox_xyxy": [12, 180, 32, 250], "bbox_center_y": 215,
                        "support_height_mm": 730 - frame_index, "object_height_mm": 179,
                        "top_z_mm": 909 - frame_index, "distance_mm": 1810,
                        "confidence": .9, "support_source": "point_p5_fallback", "region_source": "mask",
                    },
                    {
                        "class_name": "sports ball", "frame_id": f"f{frame_index}",
                        "sequence": frame_index, "logical_object_id": f"v{frame_index}",
                        "bbox_xyxy": [80, 170, 150, 245], "bbox_center_y": 207.5,
                        "support_height_mm": 727, "object_height_mm": 196,
                        "top_z_mm": 923, "distance_mm": 1820,
                        "confidence": .8, "support_source": "matched_plane", "region_source": "mask",
                    },
                ]
            )
        result = match_targets(observations, truth)
        self.assertTrue(result["all_targets_resolved"])
        self.assertEqual(result["targets"]["upper_bottle"]["valid_frame_count"], 4)
        self.assertEqual(result["targets"]["lower_volleyball"]["match_status"], "RESOLVED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
