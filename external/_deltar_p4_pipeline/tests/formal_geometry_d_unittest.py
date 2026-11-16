from __future__ import annotations

import dataclasses
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np
import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.canvas import apply_canvas
from deltar_p4_pipeline.deltar_bridge import DeltarBridge, import_official_module
from deltar_p4_pipeline.formal_deltar_d import (
    fusion_audits,
    rectangles_form_uniform_grid,
    sample_true_zone_rectangles,
)
from deltar_p4_pipeline.formal_geometry_authority import load_formal_geometry_authority
from deltar_p4_pipeline.geometry_v2.generic_extent import estimate_generic_extent
from deltar_p4_pipeline.geometry_v2.generic_support import group_same_surfaces
from deltar_p4_pipeline.horizontal_planes import gravity_points_from_depth
from deltar_p4_pipeline.rgb565 import read_rgb565_le
from deltar_p4_pipeline.scan_profiles import load_profile_bundle
from deltar_p4_pipeline.stage2_projection import (
    dynamic_rectangles,
    official_envelope_grid,
    project_source_rectangles,
)
from deltar_p4_pipeline.stage7d_contracts import bind_stage7d_profiles
from deltar_p4_pipeline.stage8b4a_contracts import validate_p4_package
from deltar_p4_pipeline.tof_adapter import adapt_inputs, seed_everything


FORMAL_PYTHON = Path(
    r"C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe"
)
VALIDATED = Path(r"C:\ec\_deltar_scan_service_data\validated")
REPLAYS = Path(r"C:\ec\_formal_d_replays_20260818")
RESEARCH = Path(
    r"C:\ec\_calibration_workspaces\deltar_fusion_final_ab_cde_20260818_01"
)


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class FormalAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.authority = load_formal_geometry_authority()

    def test_exact_interpreter_and_authority(self):
        self.assertEqual(Path(sys.executable).resolve(), FORMAL_PYTHON.resolve())
        self.assertEqual(self.authority.manifest["fusion"], "D_TRUE_READ_OFFICIAL_WRITE")
        self.assertEqual(self.authority.manifest["range_semantics"], "axial_z")
        self.assertEqual(self.authority.stage2["schema_version"], "stage2_final_calibration_bundle.v1")
        self.assertEqual(self.authority.stage3a["schema_version"], "camera_gravity.stage3a_candidate.v1")
        self.assertFalse(
            self.authority.stage3a["gravity_authority"]["full_world_rotation_authority"]
        )

    def test_stage2_reference_projection_and_zone_order(self):
        mapping = self.authority.stage2["camera_tof"]["raw_zone_camera_mapping"]
        self.assertEqual([item["raw_index"] for item in mapping], list(range(64)))
        fixtures = self.authority.stage2["deltar_fr"][
            "model_derived_rectangle_by_reference_distance_mm"
        ]
        for depth_text, expected in fixtures.items():
            actual = project_source_rectangles(
                self.authority, np.full(64, float(depth_text), dtype=np.float64)
            )
            self.assertLessEqual(
                float(np.max(np.abs(actual - np.asarray(expected)))), 1e-6
            )

    def test_stage2_direct_letterbox_and_invalid_mask(self):
        frame = VALIDATED / "scan_20260818_092411_000" / "frames" / "frame_000000"
        tof = _read(frame / "tof.json")
        rgb = read_rgb565_le(frame / "image.rgb565")
        direct = apply_canvas(rgb, "direct_640x360")
        letterbox = apply_canvas(rgb, "letterbox_640x480")
        direct_rects, valid, audit = dynamic_rectangles(self.authority, tof, direct)
        letter_rects, letter_valid, _ = dynamic_rectangles(self.authority, tof, letterbox)
        self.assertTrue(np.array_equal(valid, letter_valid))
        self.assertGreater(int(valid.sum()), 0)
        self.assertEqual(audit["measurement_semantics"], "axial_z")
        self.assertTrue(np.array_equal(direct_rects[:, [1, 3]], letter_rects[:, [1, 3]]))
        self.assertTrue(
            np.allclose(letter_rects[:, [0, 2]], direct_rects[:, [0, 2]] + 60.0)
        )
        envelope = official_envelope_grid(direct_rects)
        self.assertFalse(rectangles_form_uniform_grid(torch.from_numpy(direct_rects)))
        self.assertEqual(envelope.shape, (64, 4))

    def test_gravity_world_z_without_full_rotation(self):
        context = bind_stage7d_profiles(ROOT / "profiles" / "stage7d_horizontal_plane_profile.json")
        self.assertFalse(hasattr(context, "transform_world_from_camera"))
        depth = np.full((2, 2), 1.0, dtype=np.float64)
        points, axial, valid = gravity_points_from_depth(
            depth,
            self.authority.camera_model,
            self.authority.gravity_up_camera,
            self.authority.camera_height_mm,
            min_depth_m=0.2,
            max_depth_m=6.0,
        )
        self.assertTrue(valid.all())
        self.assertTrue(np.all(axial == 1000.0))
        camera_points = np.column_stack(
            (points[..., 0][valid], points[..., 1][valid], axial[valid])
        )
        expected = camera_points @ self.authority.gravity_up_camera + 972.0
        self.assertTrue(np.allclose(points[..., 2][valid], expected))


class FormalDFusionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not torch.cuda.is_available():
            raise unittest.SkipTest("CUDA is required by the formal DELTAR contract")
        cls.authority = load_formal_geometry_authority()
        cls.bundle = load_profile_bundle(ROOT / "profiles", validate_model_hashes=True)
        cls.bridge = DeltarBridge(
            Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128"),
            Path(r"C:\ec\_deltar_formal\assets\nyu.pt"),
            device="cuda:0",
        )
        cls.model = cls.bridge._load_model()

    def _input(self, strategy: str):
        frame = VALIDATED / "scan_20260818_092411_000" / "frames" / "frame_000000"
        rgb = read_rgb565_le(frame / "image.rgb565")
        tof = _read(frame / "tof.json")
        canvas = apply_canvas(rgb, strategy)
        profile = {
            "seed": 20260818,
        }
        return adapt_inputs(
            canvas.rgb,
            canvas,
            tof,
            profile,
            self.bridge.worktree,
            self.authority,
            device="cuda:0",
        )

    def test_checkpoint_strict_and_d_activation(self):
        audit = self.bridge.checkpoint_audit
        self.assertIsNotNone(audit)
        self.assertEqual(audit.model_state_keys, 968)
        self.assertEqual(audit.missing_keys, ())
        self.assertEqual(audit.unexpected_keys, ())
        self.assertEqual(audit.shape_mismatches, ())
        self.assertTrue(self.bridge.formal_d_activation["state_values_bit_exact"])
        self.assertEqual(self.bridge.formal_d_activation["new_learnable_parameters"], 0)

    def test_true_zone_sensitivity_without_leakage(self):
        embedding = torch.arange(1 * 3 * 90 * 160, device="cuda:0", dtype=torch.float32).reshape(1, 3, 90, 160)
        ys = np.linspace(0.0, 360.0, 9)
        xs = np.linspace(0.0, 640.0, 9)
        rects = np.asarray(
            [[ys[r], xs[c], ys[r + 1], xs[c + 1]] for r in range(8) for c in range(8)],
            dtype=np.float32,
        )
        moved = rects.copy()
        changed = [18, 27, 36, 45]
        moved[np.ix_(changed, [0, 2])] += 4.0
        first = sample_true_zone_rectangles(
            embedding, torch.from_numpy(rects).unsqueeze(0).cuda(), 4.0, (2, 2)
        )
        second = sample_true_zone_rectangles(
            embedding, torch.from_numpy(moved).unsqueeze(0).cuda(), 4.0, (2, 2)
        )
        delta = torch.amax(torch.abs(first - second), dim=(2, 3, 4))[0]
        self.assertTrue(torch.all(delta[changed] > 0))
        keep = [index for index in range(64) if index not in changed]
        self.assertTrue(torch.equal(delta[keep], torch.zeros_like(delta[keep])))

    def test_uniform_full_model_official_equivalence_all_resolutions(self):
        module = import_official_module("src.models.deltar", self.bridge.worktree)
        official = module.Deltar(n_bins=256, min_val=1e-3, max_val=10.0, norm="linear")
        state = torch.load(self.bridge.checkpoint, map_location="cpu", weights_only=True)
        official.load_state_dict(state, strict=True)
        official = official.cuda().eval()
        official_utils = import_official_module("src.utils.dataloader", self.bridge.worktree)
        for strategy, height in (("direct_640x360", 360), ("letterbox_640x480", 480)):
            inputs = self._input(strategy)
            ys = np.linspace(0.0, float(height), 9)
            xs = np.linspace(0.0, 640.0, 9)
            regular = np.asarray(
                [[ys[r], xs[c], ys[r + 1], xs[c + 1]] for r in range(8) for c in range(8)],
                dtype=np.float32,
            )
            fr = torch.from_numpy(regular).unsqueeze(0).cuda()
            info = official_utils.patch_info_from_rect_data(torch.from_numpy(regular))
            moved = {
                key: (
                    {sub: value.unsqueeze(0).cuda() for sub, value in item.items()}
                    if isinstance(item, dict)
                    else torch.tensor([item], dtype=torch.int64, device="cuda:0")
                )
                for key, item in info.items()
            }
            uniform = dataclasses.replace(inputs, fr=fr, patch_info=moved)
            payload = self.bridge._input_dict(uniform)
            with torch.inference_mode():
                seed_everything(20260818)
                official_bins, official_depth = official(payload)
                seed_everything(20260818)
                d_bins, d_depth = self.model(payload)
            self.assertTrue(torch.equal(official_bins, d_bins))
            self.assertTrue(torch.equal(official_depth, d_depth))
            audits = fusion_audits(self.model)
            self.assertEqual(len(audits), 3)
            for audit in audits.values():
                self.assertTrue(audit["uniform_equivalence_path"])
                self.assertTrue(audit["official_forward_used"])


class GeometryV2AndContractTests(unittest.TestCase):
    def test_generic_extent_and_same_surface(self):
        z = np.full((20, 20), np.nan)
        valid = np.zeros((20, 20), dtype=bool)
        valid[4:16, 5:15] = True
        z[valid] = np.linspace(700.0, 900.0, int(valid.sum()))
        result = estimate_generic_extent(
            valid,
            z,
            700.0,
            load_formal_geometry_authority().geometry_v2["generic_extent"],
        )
        self.assertTrue(math.isfinite(result["object_height_mm"]))
        self.assertGreater(result["object_height_mm"], 0.0)
        records = [
            {
                "bbox_xyxy": [0, 0, 10, 10],
                "generic_support": {"support_mm": 710.0, "confidence": 0.9, "source": "x", "diagnostics": {"supported_candidate_heights_mm": [710.0, 715.0]}, "_surface_component_mask": np.pad(np.ones((4, 8), dtype=bool), ((8, 8), (4, 8))), "_selected_median_axial_mm": 1800.0},
                "final": {"support_mm": 710.0, "top_mm": 900.0, "object_height_mm": 190.0, "support_source": "x"},
                "same_surface_group_id": None,
                "consensus_used": False,
            },
            {
                "bbox_xyxy": [12, 0, 22, 12],
                "generic_support": {"support_mm": 720.0, "confidence": 0.9, "source": "x", "diagnostics": {"supported_candidate_heights_mm": [720.0, 715.0]}, "_surface_component_mask": np.pad(np.ones((4, 8), dtype=bool), ((8, 8), (4, 8))), "_selected_median_axial_mm": 1810.0},
                "final": {"support_mm": 720.0, "top_mm": 910.0, "object_height_mm": 190.0, "support_source": "x"},
                "same_surface_group_id": None,
                "consensus_used": False,
            },
        ]
        cfg = load_formal_geometry_authority().geometry_v2["same_surface"]
        group_same_surfaces(records, cfg, "frame_000000")
        self.assertTrue(all(item["consensus_used"] for item in records))
        self.assertEqual(records[0]["final"]["support_mm"], 715.0)

    def test_replay_parity_result_authority_and_p4_contract(self):
        research_names = {
            "G001_release": "G001",
            "G002_release": "G002",
            "H001_release": "H001",
            "H002_release": "H002",
            "H003_release": "H003",
        }
        for production_name, research_name in research_names.items():
            root = REPLAYS / production_name
            geometry = _read(root / "stage8b_all_object_geometry.v1.json")
            package = _read(root / "stage8b4a_p4_decision_package.v1.json")
            validate_p4_package(package)
            research = _read(
                RESEARCH / "results" / research_name / "geometry_v2"
                / "D_TRUE_READ_OFFICIAL_WRITE" / "object_records.geometry_v2.json"
            )
            expected = {
                item["logical_object_id"]: (
                    item["final"]["support_mm"], item["final"]["top_mm"],
                    item["final"]["object_height_mm"],
                )
                for item in research
            }
            actual = {
                item["logical_object_id"]: (
                    item["geometry"]["support_height_mm"],
                    item["geometry"]["top_height_above_floor_mm"],
                    item["geometry"]["object_height_mm"],
                )
                for frame in geometry["frames"] for item in frame["objects"]
                if item["numeric_available"]
            }
            self.assertEqual(actual, expected)
            self.assertGreater(geometry["summary"]["generic_baseline_count"], 0)
            self.assertGreater(geometry["summary"]["upright_slender_count"], 0)
            self.assertGreater(geometry["summary"]["roughly_spherical_count"], 0)
            for frame in geometry["frames"]:
                frame_result = _read(root / "frame_results" / f"{frame['frame_id']}.json")
                self.assertEqual(
                    {item["logical_object_id"] for item in frame_result["objects"]},
                    {item["logical_object_id"] for item in frame["objects"]},
                )
                frame_authority = {
                    item["logical_object_id"]: item for item in frame["objects"]
                }
                for item in frame_result["objects"]:
                    authority = frame_authority.get(item["logical_object_id"])
                    self.assertIsNotNone(authority)
                    self.assertEqual(
                        item["provenance"]["stage4_function"],
                        "all_object_geometry.measure_logical_object_v2",
                    )
                    self.assertFalse(item["formal_geometry"]["fallback_used"])
                    if not authority["numeric_available"]:
                        self.assertEqual(item["formal_geometry"]["status"], "rejected")
                        self.assertIsNone(item["formal_geometry"]["selected_geometry"])
                        continue
                    self.assertAlmostEqual(
                        item["bottom_height"]["value_m"] * 1000.0,
                        authority["geometry"]["support_height_mm"],
                    )
                    self.assertTrue(item["formal_geometry"]["absolute_geometry_valid"])

    def test_old_runtime_paths_removed(self):
        active_files = [
            ROOT / "src" / "deltar_p4_pipeline" / name
            for name in (
                "deltar_bridge.py",
                "formal_deltar_d.py",
                "tof_adapter.py",
                "frame_pipeline.py",
                "scan_job.py",
                "scan_cli.py",
                "scan_service.py",
                "scan_service_worker.py",
                "frozen_runtime_resources.py",
                "offline_preflight.py",
                "stage7d_contracts.py",
                "stage7d_outputs.py",
                "horizontal_planes.py",
                "all_object_geometry.py",
                "stage8b4a_package.py",
            )
        ] + [
            ROOT / "scripts" / "run_scan_service.py",
            ROOT / "scripts" / "run_offline_preflight.py",
        ]
        active = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in active_files
        )
        self.assertNotIn("--stage7e-object-geometry", active)
        self.assertNotIn("_apply_competition_shelf_dynamic_stabilizer", active)
        self.assertNotIn("corrected_height_bias_mm", active)
        self.assertNotIn("regular_grid_source_then_transform", active)
        self.assertNotIn("calibration_profile.example.json", active)
        package_source = (ROOT / "src" / "deltar_p4_pipeline" / "stage8b4a_package.py").read_text(encoding="utf-8")
        for forbidden in ("715.0", "1059.0", '"bottle": 180.0', '"sports ball": 200.0'):
            self.assertNotIn(forbidden, package_source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
