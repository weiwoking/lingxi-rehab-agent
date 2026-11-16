from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image

from deltar_p4_pipeline.cli import _parser
from deltar_p4_pipeline.stage7d_contracts import (
    FORMAL_CAMERA_PATH,
    FORMAL_CAMERA_TOF_PATH,
    FORMAL_CAMERA_WORLD_PATH,
    bind_stage7d_profiles,
    validate_horizontal_planes_document,
    validate_stage7d_summary,
)
from deltar_p4_pipeline.stage7d_outputs import Stage7DProcessor


ROOT = Path(__file__).resolve().parents[1]
STAGE7D_PROFILE = ROOT / "profiles" / "stage7d_horizontal_plane_profile.json"


class Stage7DOutputIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        context = bind_stage7d_profiles(
            FORMAL_CAMERA_PATH,
            FORMAL_CAMERA_WORLD_PATH,
            STAGE7D_PROFILE,
            FORMAL_CAMERA_TOF_PATH,
        )
        fast_profile = dict(context.profile)
        fast_profile["ransac_iterations"] = 32
        fast_profile["fit_stride_px"] = 4
        cls.context = replace(context, profile=fast_profile)

    def test_additive_dual_semantics_outputs_and_old_files_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            old_files = {
                root / "result.json": b'{"schema_version":"deltar_scan_result.v1.1"}',
                root / "job_report.json": b'{"schema_version":"deltar_pc_job_report.v1.1"}',
                root / "frame_results" / "frame_000000.json": b'{"schema_version":"deltar_scan_frame_result.v1.1"}',
            }
            for path, content in old_files.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            rgb = np.zeros((360, 640, 3), dtype=np.uint8)
            rgb[..., 1] = np.linspace(0, 255, 640, dtype=np.uint8)[None, :]
            depth = np.full((360, 640), 1.2, dtype=np.float32)
            depth[:8, :] = 0.0
            processor = Stage7DProcessor(
                self.context,
                root,
                "synthetic_scan",
                diagnostics_enabled=True,
            )
            record = processor.process_frame("frame_000000", rgb, depth)
            summary = processor.write_summary()

            self.assertEqual(
                set(record["hypotheses"]), {"axial_z", "radial_range"}
            )
            self.assertEqual(
                summary["selection_status"],
                "unresolved_pending_real_stage7d_acceptance",
            )
            self.assertFalse(summary["absolute_geometry_valid"])
            validate_stage7d_summary(summary)
            for hypothesis in ("axial_z", "radial_range"):
                folder = root / "stage7d" / "frame_000000" / hypothesis
                document = json.loads(
                    (folder / "horizontal_planes.v1.json").read_text(
                        encoding="utf-8"
                    )
                )
                validate_horizontal_planes_document(document)
                self.assertTrue((folder / "depth_source_640x360.npy").is_file())
                height_map = np.load(folder / "world_height_mm.npy", allow_pickle=False)
                self.assertEqual(height_map.shape, (360, 640))
                with np.load(folder / "plane_masks.npz", allow_pickle=False) as masks:
                    self.assertIn("ground_mask", masks.files)
                    self.assertIn("support_surface_mask", masks.files)
                for image_name in (
                    "world_height_visual.png",
                    "horizontal_plane_overlay.png",
                ):
                    with Image.open(folder / image_name) as image:
                        self.assertEqual(image.size, (640, 360))
            comparison = json.loads(
                (
                    root
                    / "stage7d"
                    / "frame_000000"
                    / "semantics_comparison.json"
                ).read_text(encoding="utf-8")
            )
            self.assertIn("axial_z", comparison)
            self.assertIn("radial_range", comparison)
            self.assertNotIn("selected_semantics", comparison)
            for path, content in old_files.items():
                self.assertEqual(path.read_bytes(), content)

    def test_default_scan_run_remains_opt_out(self) -> None:
        args = _parser().parse_args(
            [
                "scan-run",
                "--scan-dir",
                "input",
                "--output-dir",
                "output",
            ]
        )
        self.assertFalse(args.stage7d_horizontal_planes)
        self.assertFalse(args.stage7d_diagnostics)
        self.assertIsNone(args.camera_intrinsics_profile)
        self.assertIsNone(args.camera_world_profile)
        self.assertIsNone(args.camera_tof_profile)
        self.assertIsNone(args.stage7d_profile)

    def test_authority_only_preserves_axial_and_omits_radial(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            rgb = np.zeros((360, 640, 3), dtype=np.uint8)
            depth = np.full((360, 640), 1.2, dtype=np.float32)
            processor = Stage7DProcessor(self.context, root, "authority_scan")
            record = processor.process_frame("frame_000000", rgb, depth)
            summary = processor.write_summary()

            self.assertEqual(set(record["hypotheses"]), {"axial_z"})
            self.assertNotIn("semantics_comparison", record)
            self.assertTrue(
                (root / "stage7d" / "frame_000000" / "axial_z").is_dir()
            )
            self.assertFalse(
                (root / "stage7d" / "frame_000000" / "radial_range").exists()
            )
            self.assertFalse(
                (root / "stage7d" / "frame_000000" / "semantics_comparison.json").exists()
            )
            self.assertEqual(record["_stage7d_timing"]["stage7d_radial_ms"], 0.0)
            self.assertEqual(
                record["_stage7d_timing"]["stage7d_diagnostics_enabled"], 0.0
            )
            self.assertIn("W_STAGE7D_DIAGNOSTICS_DISABLED", summary["warnings"])

    def test_cli_can_explicitly_enable_radial_diagnostics(self) -> None:
        args = _parser().parse_args(
            [
                "scan-run",
                "--scan-dir",
                "input",
                "--output-dir",
                "output",
                "--stage7d-diagnostics",
            ]
        )
        self.assertTrue(args.stage7d_diagnostics)

    def test_json_schemas_are_strict_json(self) -> None:
        for name in (
            "deltar_horizontal_planes.v1.schema.json",
            "stage7d_summary.v1.schema.json",
        ):
            value = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
            self.assertEqual(value["$schema"], "https://json-schema.org/draft/2020-12/schema")


if __name__ == "__main__":
    unittest.main()
