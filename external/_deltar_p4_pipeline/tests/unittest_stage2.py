from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from deltar_p4_pipeline.canvas import apply_canvas, regular_grid_source, transform_fr, transform_intrinsics
from deltar_p4_pipeline.contracts import load_json, validate_profile, validate_tof
from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.rgb565 import decode_rgb565_le, encode_rgb565_le, roundtrip_metrics
from deltar_p4_pipeline.tof_adapter import adapt_inputs
from deltar_p4_pipeline.validation import quantize_depth_mm, validate_depth_png

WORKTREE = Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128")


def tof() -> dict:
    return {"schema_version": "rehab_tof_raw.v1", "sequence": 1, "timestamp_us": 0, "distance_mm": [2000] * 64, "range_sigma_mm": [40] * 64, "target_status": [5] * 64, "nb_target_detected": [1] * 64}


def profile(**overrides) -> dict:
    value = {
        "schema_version": "deltar_p4_profile.v1", "profile_id": "test_v1", "sample_kind": "synthetic",
        "image": {"width": 640, "height": 360, "stride": 1280, "pixel_format": "RGB565_LE"},
        "canvas": {"strategy": "letterbox_640x480", "fill_rgb": [0, 0, 0]},
        "zone_mapping": {"strategy": "identity"},
        "histogram": {"mu_strategy": "distance_mm_to_m", "sigma_strategy": "range_sigma_mm_to_m"},
        "mask": {"strategy": "all_valid"}, "fr": {"strategy": "regular_grid_source_then_transform"}, "seed": 0,
        "test_intrinsics": {"fx": 500.0, "fy": 500.0, "cx": 320.0, "cy": 180.0},
    }
    value.update(overrides)
    return value


class Rgb565Tests(unittest.TestCase):
    def test_known_vectors(self):
        words = np.asarray([0xF800, 0x07E0, 0x001F, 0xFFFF, 0], dtype="<u2")
        self.assertEqual(decode_rgb565_le(words.tobytes(), width=5, height=1, stride=10)[0].tolist(), [[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 255], [0, 0, 0]])

    def test_little_endian(self):
        self.assertEqual(encode_rgb565_le(np.asarray([[[255, 0, 0]]], dtype=np.uint8)), b"\x00\xf8")

    def test_roundtrip_bounds(self):
        rng = np.random.default_rng(0)
        rgb = rng.integers(0, 256, (360, 640, 3), dtype=np.uint8)
        self.assertTrue(all(actual <= bound for actual, bound in zip(roundtrip_metrics(rgb)["max_error_rgb"], [7, 3, 7])))

    def test_size_rejected(self):
        with self.assertRaises(PipelineError) as caught:
            decode_rgb565_le(b"bad")
        self.assertEqual(caught.exception.code, "E_IMAGE_SIZE")


class CanvasTests(unittest.TestCase):
    def setUp(self):
        self.rgb = np.zeros((360, 640, 3), dtype=np.uint8)

    def test_letterbox(self):
        result = apply_canvas(self.rgb, "letterbox_640x480")
        self.assertEqual(result.valid_region, (60, 0, 420, 640))
        self.assertEqual(int(result.valid_mask.sum()), 360 * 640)

    def test_letterbox_intrinsics(self):
        result = apply_canvas(self.rgb, "letterbox_640x480")
        value = transform_intrinsics({"fx": 500, "fy": 500, "cx": 320, "cy": 180}, result.source_to_canvas)
        self.assertEqual((value["fx"], value["fy"], value["cx"], value["cy"]), (500, 500, 320, 240))

    def test_resize(self):
        result = apply_canvas(self.rgb, "resize_640x480")
        self.assertAlmostEqual(result.source_to_canvas[1, 1], 4 / 3)
        self.assertIn("GEOMETRIC_DISTORTION_NON_UNIFORM_SCALE", result.warnings)

    def test_center_crop(self):
        result = apply_canvas(self.rgb, "scale_to_fill_center_crop_640x480")
        self.assertTrue(np.allclose(result.source_to_canvas @ result.canvas_to_source, np.eye(3)))
        self.assertEqual(result.source_to_canvas[0, 0], 853 / 640)
        self.assertEqual(result.source_to_canvas[1, 1], 480 / 360)
        self.assertGreater(result.metadata["lost_fov_ratio"], 0.24)

    def test_regular_grid(self):
        fr = transform_fr(regular_grid_source(), apply_canvas(self.rgb, "letterbox_640x480").source_to_canvas)
        self.assertEqual(fr[0].tolist(), [60.0, 0.0, 105.0, 80.0])
        self.assertEqual(fr[-1].tolist(), [375.0, 560.0, 420.0, 640.0])

    def test_direct_shapes(self):
        self.assertEqual(apply_canvas(self.rgb, "direct_640x360").rgb.shape[:2], (360, 640))
        self.assertEqual(apply_canvas(self.rgb, "direct_640x384_pad32").rgb.shape[:2], (384, 640))


class ContractTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_tof(tof())["range_sigma_mm"][0], 40.0)
        self.assertEqual(validate_profile(profile())["profile_id"], "test_v1")

    def test_all_missing_fields(self):
        for field in ("sequence", "timestamp_us", "distance_mm", "range_sigma_mm", "target_status", "nb_target_detected"):
            with self.subTest(field=field):
                value = tof(); del value[field]
                with self.assertRaises(PipelineError) as caught:
                    validate_tof(value)
                self.assertEqual(caught.exception.code, "E_FIELD_MISSING")

    def test_all_array_lengths(self):
        for field in ("distance_mm", "range_sigma_mm", "target_status", "nb_target_detected"):
            with self.subTest(field=field):
                value = tof(); value[field] = value[field][:-1]
                with self.assertRaises(PipelineError) as caught:
                    validate_tof(value)
                self.assertEqual(caught.exception.code, "E_ARRAY_LENGTH")

    def test_negative_sigma(self):
        value = tof(); value["range_sigma_mm"][0] = -1
        with self.assertRaises(PipelineError) as caught:
            validate_tof(value)
        self.assertEqual(caught.exception.code, "E_NEGATIVE_SIGMA")

    def test_bool_not_integer(self):
        value = tof(); value["sequence"] = True
        with self.assertRaises(PipelineError) as caught:
            validate_tof(value)
        self.assertEqual(caught.exception.code, "E_TYPE")

    def test_string_not_number(self):
        value = tof(); value["distance_mm"][0] = "2"
        with self.assertRaises(PipelineError) as caught:
            validate_tof(value)
        self.assertEqual(caught.exception.code, "E_TYPE")

    def test_status_range(self):
        value = tof(); value["target_status"][0] = 256
        with self.assertRaises(PipelineError) as caught:
            validate_tof(value)
        self.assertEqual(caught.exception.code, "E_STATUS_RANGE")

    def test_nonfinite_json(self):
        with tempfile.TemporaryDirectory() as folder:
            for literal in ("NaN", "Infinity", "-Infinity"):
                path = Path(folder) / "bad.json"; path.write_text('{"x":' + literal + "}", encoding="utf-8")
                with self.subTest(literal=literal), self.assertRaises(PipelineError) as caught:
                    load_json(path)
                self.assertEqual(caught.exception.code, "E_JSON_PARSE")

    def test_mask_bool64(self):
        with self.assertRaises(PipelineError) as caught:
            validate_profile(profile(mask={"strategy": "explicit_bool64", "values": [1] * 64}))
        self.assertEqual(caught.exception.code, "E_TYPE")

    def test_out_of_bounds_fr_allowed(self):
        value = validate_profile(profile(fr={"strategy": "explicit_model_canvas", "values": [[-10, -20, 500, 700]] * 64}))
        self.assertEqual(value["fr"]["values"][0], [-10.0, -20.0, 500.0, 700.0])

    def test_bad_fr_extent(self):
        for row in ([0, 0, 0, 1], [0, 2, 1, 2], [1, 0, 0, 1]):
            with self.subTest(row=row), self.assertRaises(PipelineError) as caught:
                validate_profile(profile(fr={"strategy": "explicit_model_canvas", "values": [row] * 64}))
            self.assertEqual(caught.exception.code, "E_FR_INVALID")

    def test_unsupported_profile(self):
        with self.assertRaises(PipelineError) as caught:
            validate_profile(profile(canvas={"strategy": "guess", "fill_rgb": [0, 0, 0]}))
        self.assertEqual(caught.exception.code, "E_PROFILE_UNSUPPORTED")


class AdapterAndOutputTests(unittest.TestCase):
    def test_tof_adapter(self):
        rgb = np.zeros((360, 640, 3), dtype=np.uint8)
        canvas = apply_canvas(rgb, "letterbox_640x480")
        inputs = adapt_inputs(canvas.rgb, canvas, validate_tof(tof()), validate_profile(profile()), WORKTREE)
        self.assertEqual(list(inputs.hypotheses.shape), [1, 64, 16])
        self.assertTrue(torch.all(inputs.raw_hist[:, 0] == 2.0))
        self.assertTrue(torch.all(inputs.raw_hist[:, 1] == 0.04))
        self.assertEqual(inputs.patch_info["zone_num"].tolist(), [8])
        self.assertEqual(list(inputs.patch_info[4]["pad_size"].shape), [1, 2])

    def test_candidate_mask(self):
        raw = tof(); raw["nb_target_detected"][0] = 0; raw["target_status"][1] = 255; raw["target_status"][2] = 9
        canvas = apply_canvas(np.zeros((360, 640, 3), dtype=np.uint8), "letterbox_640x480")
        inputs = adapt_inputs(canvas.rgb, canvas, validate_tof(raw), validate_profile(profile(mask={"strategy": "status_count_candidate_v1"})), WORKTREE)
        self.assertEqual(inputs.mask[0, :3].tolist(), [False, False, True])

    def test_zone_flip(self):
        raw = tof(); raw["distance_mm"] = list(range(64))
        canvas = apply_canvas(np.zeros((360, 640, 3), dtype=np.uint8), "letterbox_640x480")
        inputs = adapt_inputs(canvas.rgb, canvas, validate_tof(raw), validate_profile(profile(zone_mapping={"strategy": "flip_horizontal"})), WORKTREE)
        self.assertTrue(np.allclose(inputs.raw_hist[:8, 0].numpy(), np.asarray([7, 6, 5, 4, 3, 2, 1, 0]) / 1000))

    def test_quantization(self):
        depth = np.asarray([[1.0004, 1.0005, 100.0, np.nan, -1.0]], dtype=np.float32)
        mm = quantize_depth_mm(depth)
        self.assertEqual(mm[0, 0], 1000)
        self.assertIn(mm[0, 1], (1000, 1001))
        self.assertEqual(mm[0, 2:].tolist(), [65535, 0, 0])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "depth.png"; Image.fromarray(mm, mode="I;16").save(path)
            self.assertTrue(validate_depth_png(depth, path)["exact_quantized_match"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
