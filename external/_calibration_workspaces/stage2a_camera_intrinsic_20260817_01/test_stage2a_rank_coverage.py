import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import stage2a_camera_intrinsic as stage2a


class CoverageCsvRegressionTest(unittest.TestCase):
    columns = [
        "pose_id", "group", "frame_id", "board_center_x", "board_center_y",
        "board_coverage", "corner_spatial_cells_4x3",
        "viewing_angle_proxy_deg", "provisional_pnp_rms_px",
    ]

    @staticmethod
    def manifest(selected_frame=None):
        value = {"poses": {pose: {"scan_id": f"scan_{pose}"} for pose in stage2a.LEGACY_POSE_IDS}}
        if selected_frame is not None:
            value["poses"]["A01"]["selected_frame"] = selected_frame
        return value

    def test_partial_dataset_projects_full_ranking_row_to_coverage_schema(self):
        ranking_row = {
            "pose_id": "A01", "frame_id": "frame_000003",
            "board_center_x": 305.5, "board_center_y": 161.9,
            "board_coverage": 0.1477, "corner_spatial_cells_4x3": 4,
            "viewing_angle_proxy_deg": 4.53,
            "provisional_pnp_rms_px": 0.152,
            "quality_score": 92.1, "source_sha256": {"image.rgb565": "abc"},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with mock.patch.object(stage2a, "WORKSPACE", workspace):
                stage2a.coverage_plot(
                    [ranking_row], self.manifest("frame_000003")
                )
            with (workspace / "coverage_metrics.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                reader = csv.DictReader(stream)
                rows = list(reader)
                self.assertEqual(reader.fieldnames, self.columns)
            self.assertEqual(len(rows), 1)
            self.assertEqual(set(rows[0]), set(self.columns))
            self.assertEqual(rows[0]["group"], "legacy")
            self.assertEqual(rows[0]["frame_id"], "frame_000003")
            self.assertNotIn("quality_score", rows[0])
            self.assertNotIn("source_sha256", rows[0])
            self.assertTrue((workspace / "dataset_coverage.png").is_file())

    def test_empty_selection_writes_readable_header_only_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with mock.patch.object(stage2a, "WORKSPACE", workspace):
                stage2a.coverage_plot([], self.manifest())
            with (workspace / "coverage_metrics.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, self.columns)
                self.assertEqual(list(reader), [])


if __name__ == "__main__":
    unittest.main()
