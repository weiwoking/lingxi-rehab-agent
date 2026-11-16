from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.rgb565 import encode_rgb565_le

OUT = ROOT / "samples" / "synthetic"


def diagnostic_image() -> np.ndarray:
    image = np.zeros((360, 640, 3), dtype=np.uint8)
    image[:180, :320] = (255, 0, 0)
    image[:180, 320:] = (0, 255, 0)
    image[180:, :320] = (0, 0, 255)
    image[180:, 320:] = (255, 255, 255)
    gray = np.arange(256, dtype=np.uint8)
    image[160:176, 192:448] = np.stack((gray, gray, gray), axis=-1)
    bars = [(255, 0, 0), (255, 255, 0), (0, 255, 0), (0, 255, 255), (0, 0, 255), (255, 0, 255), (255, 255, 255), (0, 0, 0)]
    for i, color in enumerate(bars):
        image[176:192, i * 80 : (i + 1) * 80] = color
    # Asymmetric corner markers prove both axes and channel ordering.
    image[:24, :24] = (255, 255, 0)
    image[:12, -36:] = (0, 255, 255)
    image[-36:, :12] = (255, 0, 255)
    image[-20:, -20:] = (64, 128, 192)
    return image


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def build(name: str, distance: list[int], sigma: list[int], status: list[int], count: list[int], mask_strategy: str, mask_values: list[bool] | None = None, fr_strategy: str = "regular_grid_source_then_transform", fr_values: list[list[int]] | None = None) -> None:
    sample = OUT / name
    sample.mkdir(parents=True, exist_ok=True)
    (sample / "image.rgb565").write_bytes(encode_rgb565_le(diagnostic_image()))
    write_json(sample / "source_metadata.json", {
        "orientation_markers": [
            {"name": "top_left", "y": 0, "x": 0, "rgb": [255, 255, 0]},
            {"name": "top_right", "y": 0, "x": 639, "rgb": [0, 255, 255]},
            {"name": "bottom_left", "y": 359, "x": 0, "rgb": [255, 0, 255]},
            {"name": "bottom_right", "y": 359, "x": 639, "rgb": [66, 130, 198]},
        ],
        "purpose": "asymmetric four-corner and primary-color direction/channel check",
    })
    write_json(sample / "tof.json", {
        "schema_version": "rehab_tof_raw.v1", "sequence": 1, "timestamp_us": 0,
        "distance_mm": distance, "range_sigma_mm": sigma,
        "target_status": status, "nb_target_detected": count,
    })
    mask = {"strategy": mask_strategy}
    if mask_values is not None:
        mask["values"] = mask_values
    fr = {"strategy": fr_strategy}
    if fr_values is not None:
        fr["values"] = fr_values
    write_json(sample / "profile.json", {
        "schema_version": "deltar_p4_profile.v1", "profile_id": f"synthetic_{name}_v1", "sample_kind": "synthetic",
        "image": {"width": 640, "height": 360, "stride": 1280, "pixel_format": "RGB565_LE"},
        "canvas": {"strategy": "letterbox_640x480", "fill_rgb": [0, 0, 0]},
        "zone_mapping": {"strategy": "identity"},
        "histogram": {"mu_strategy": "distance_mm_to_m", "sigma_strategy": "range_sigma_mm_to_m"},
        "mask": mask, "fr": fr, "seed": 0,
        "test_intrinsics": {"fx": 500.0, "fy": 500.0, "cx": 320.0, "cy": 180.0},
    })


def main() -> None:
    base_d = [2000] * 64
    base_s = [40] * 64
    base_status = [5] * 64
    base_count = [1] * 64
    build("flat_wall_all_valid", base_d, base_s, base_status, base_count, "all_valid")
    build("left_near_right_far", [1000 if i % 8 < 4 else 3000 for i in range(64)], base_s, base_status, base_count, "all_valid")
    partial = [i % 3 != 0 for i in range(64)]
    build("partial_invalid", base_d, base_s, base_status, base_count, "explicit_bool64", partial)
    build("sigma_gradient", base_d, list(range(10, 74)), base_status, base_count, "all_valid")
    counts = [0 if i % 5 == 0 else 1 for i in range(64)]
    build("count_zero", base_d, base_s, base_status, counts, "status_count_candidate_v1")
    statuses = [[5, 6, 9, 255][i % 4] for i in range(64)]
    build("status_variants", base_d, base_s, statuses, base_count, "status_count_candidate_v1")
    fr = [[-20 + r * 65, -30 + c * 88, 50 + r * 65, 70 + c * 88] for r in range(8) for c in range(8)]
    build("out_of_bounds_fr", base_d, base_s, base_status, base_count, "all_valid", fr_strategy="explicit_model_canvas", fr_values=fr)
    build("all_invalid", base_d, base_s, base_status, base_count, "all_invalid")
    print(json.dumps({"built": sorted(path.name for path in OUT.iterdir() if path.is_dir())}, indent=2))


if __name__ == "__main__":
    main()
