from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from deltar_p4_pipeline.scan_service_contracts import (
    MAX_FRAMES,
    MAX_UPLOAD_BYTES,
    ServiceError,
    expected_fields,
    scan_identity,
)
from deltar_p4_pipeline.scan_validator import validate_scan
from tests.scan_service_helpers import copy_scan, multipart_bytes


ROOT = Path(__file__).resolve().parents[1]
REAL_SCAN = Path(r"C:\ec\scan_test\scan_20260718_081523_000")


class Stage8APCCompatibilityTests(unittest.TestCase):
    def test_frozen_limits(self) -> None:
        self.assertEqual(11, MAX_FRAMES)
        self.assertEqual(6 * 1024 * 1024, MAX_UPLOAD_BYTES)

    def test_ten_and_eleven_accepted_twelve_rejected(self) -> None:
        for count in (10, 11):
            self.assertEqual(
                (f"scan_{count}", count),
                scan_identity({
                    "scan_id": f"scan_{count}",
                    "frame_count": count,
                    "capture_mode": "multi_frame",
                }),
            )
        with self.assertRaises(ServiceError):
            scan_identity({
                "scan_id": "scan_12",
                "frame_count": 12,
                "capture_mode": "multi_frame",
            })

    def test_eleven_frame_field_contract_is_complete(self) -> None:
        fields = expected_fields(11)
        self.assertEqual(34, len(fields))
        for sequence in range(11):
            prefix = f"frame_{sequence:06d}_"
            self.assertIn(prefix + "frame_json", fields)
            self.assertIn(prefix + "tof_json", fields)
            self.assertIn(prefix + "image_rgb565", fields)

    def test_eleven_frame_input_contract_and_payload_fit_six_mib(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stage8a_pc_") as temporary:
            scan = copy_scan(
                REAL_SCAN,
                Path(temporary) / "scan_stage8a_11",
                frame_count=11,
                scan_id="scan_stage8a_11",
            )
            validated = validate_scan(
                scan, None, max_frames=11,
                max_scan_bytes=MAX_UPLOAD_BYTES,
            )
            self.assertEqual(list(range(11)), [
                frame.frame["sequence"] for frame in validated.frames
            ])
            parts = multipart_bytes(scan)
            raw_part_bytes = sum(len(value[1]) for _, value in parts)
            self.assertLess(raw_part_bytes, MAX_UPLOAD_BYTES)
            self.assertEqual(34, len(parts))
            for sequence in range(11):
                frame = scan / "frames" / f"frame_{sequence:06d}"
                self.assertTrue((frame / "frame.json").is_file())
                self.assertTrue((frame / "tof.json").is_file())
                self.assertEqual(460800, (frame / "image.rgb565").stat().st_size)

    def test_legacy_five_frame_contract_remains_valid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stage8a_pc_legacy_") as temporary:
            scan = copy_scan(
                REAL_SCAN,
                Path(temporary) / "scan_stage8a_5",
                frame_count=5,
                scan_id="scan_stage8a_5",
            )
            validated = validate_scan(scan, None, max_frames=11)
            self.assertEqual(5, len(validated.frames))

    def test_service_keeps_exact_bounded_header_gate_and_job_creation(self) -> None:
        service = (
            ROOT / "src" / "deltar_p4_pipeline" / "scan_service.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "if length > service_config.max_upload_bytes:", service
        )
        self.assertIn(
            "scan_identity(scan, max_frames=service_config.max_frames)",
            service,
        )
        self.assertIn("validated = manager.storage.commit_validated", service)
        self.assertIn("manager.accept(record)", service)
        self.assertIn("manager.start(record)", service)
        self.assertNotIn("compact_scene", service)
        self.assertNotIn("latest_successful_scene", service)

    def test_runner_defaults_match_contract_without_stage7_change(self) -> None:
        runner = (ROOT / "scripts" / "run_scan_service.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'default=6 * 1024 * 1024', runner
        )
        self.assertIn('default=11', runner)
        worker = (
            ROOT / "src" / "deltar_p4_pipeline" / "scan_service_worker.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("compact_scene", worker)
        self.assertNotIn("latest_successful_scene", worker)


if __name__ == "__main__":
    unittest.main(verbosity=2)
