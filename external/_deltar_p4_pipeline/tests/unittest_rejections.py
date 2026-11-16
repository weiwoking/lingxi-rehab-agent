from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from deltar_p4_pipeline.cli import run_sample
from deltar_p4_pipeline.deltar_bridge import DeltarBridge
from deltar_p4_pipeline.errors import PipelineError


def valid_tof() -> dict:
    return {"schema_version": "rehab_tof_raw.v1", "sequence": 1, "timestamp_us": 0, "distance_mm": [2000] * 64, "range_sigma_mm": [40] * 64, "target_status": [5] * 64, "nb_target_detected": [1] * 64}


def valid_profile() -> dict:
    return {
        "schema_version": "deltar_p4_profile.v1", "profile_id": "reject_v1", "sample_kind": "synthetic",
        "image": {"width": 640, "height": 360, "stride": 1280, "pixel_format": "RGB565_LE"},
        "canvas": {"strategy": "letterbox_640x480", "fill_rgb": [0, 0, 0]},
        "zone_mapping": {"strategy": "identity"},
        "histogram": {"mu_strategy": "distance_mm_to_m", "sigma_strategy": "range_sigma_mm_to_m"},
        "mask": {"strategy": "all_valid"}, "fr": {"strategy": "regular_grid_source_then_transform"}, "seed": 0,
        "test_intrinsics": {"fx": 500, "fy": 500, "cx": 320, "cy": 180},
    }


class CliRejectionTests(unittest.TestCase):
    def assert_rejected(self, expected: str, mutate=None, image: bytes | None = b"\0" * 460800, raw_tof: str | None = None, raw_profile: str | None = None):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); sample = root / "sample_bad"; output = root / "output"; sample.mkdir()
            tof, profile = valid_tof(), valid_profile()
            if mutate:
                mutate(tof, profile)
            if image is not None:
                (sample / "image.rgb565").write_bytes(image)
            (sample / "tof.json").write_text(raw_tof if raw_tof is not None else json.dumps(tof), encoding="utf-8")
            (sample / "profile.json").write_text(raw_profile if raw_profile is not None else json.dumps(profile), encoding="utf-8")
            with self.assertRaises(PipelineError) as caught:
                run_sample(sample, output, device="cpu")
            self.assertEqual(caught.exception.code, expected)
            self.assertEqual(list(output.rglob("depth_m.npy")), [])
            result_files = list(output.rglob("result.json"))
            self.assertEqual(len(result_files), 1)
            self.assertEqual(json.loads(result_files[0].read_text(encoding="utf-8"))["status"], "rejected")

    def test_missing_image(self): self.assert_rejected("E_IMAGE_MISSING", image=None)
    def test_image_size(self): self.assert_rejected("E_IMAGE_SIZE", image=b"bad")
    def test_json_parse(self): self.assert_rejected("E_JSON_PARSE", raw_tof="{")
    def test_schema(self): self.assert_rejected("E_SCHEMA_VERSION", lambda t, p: t.update(schema_version="future"))
    def test_missing_field(self): self.assert_rejected("E_FIELD_MISSING", lambda t, p: t.pop("distance_mm"))
    def test_array_length(self): self.assert_rejected("E_ARRAY_LENGTH", lambda t, p: t.update(distance_mm=[1] * 63))
    def test_type(self): self.assert_rejected("E_TYPE", lambda t, p: t.update(sequence="1"))
    def test_nonfinite(self): self.assert_rejected("E_JSON_PARSE", raw_tof=json.dumps(valid_tof()).replace("2000", "NaN", 1))
    def test_negative_sigma(self): self.assert_rejected("E_NEGATIVE_SIGMA", lambda t, p: t["range_sigma_mm"].__setitem__(0, -1))
    def test_status_range(self): self.assert_rejected("E_STATUS_RANGE", lambda t, p: t["target_status"].__setitem__(0, 256))
    def test_count_range(self): self.assert_rejected("E_COUNT_RANGE", lambda t, p: t["nb_target_detected"].__setitem__(0, -1))
    def test_mask_length(self): self.assert_rejected("E_MASK_LENGTH", lambda t, p: p.update(mask={"strategy": "explicit_bool64", "values": [True] * 63}))
    def test_fr_shape(self): self.assert_rejected("E_FR_SHAPE", lambda t, p: p.update(fr={"strategy": "explicit_model_canvas", "values": [[0, 0, 1, 1]] * 63}))
    def test_fr_invalid(self): self.assert_rejected("E_FR_INVALID", lambda t, p: p.update(fr={"strategy": "explicit_model_canvas", "values": [[0, 0, 0, 1]] * 64}))
    def test_profile_unsupported(self): self.assert_rejected("E_PROFILE_UNSUPPORTED", lambda t, p: p.update(canvas={"strategy": "guess", "fill_rgb": [0, 0, 0]}))

    def test_checkpoint_hash_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / "bad.pt"; bad.write_bytes(b"not checkpoint")
            with self.assertRaises(PipelineError) as caught:
                DeltarBridge(Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128"), bad, device="cpu")
            self.assertEqual(caught.exception.code, "E_CHECKPOINT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
