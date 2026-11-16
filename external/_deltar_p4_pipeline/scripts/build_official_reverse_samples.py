from __future__ import annotations

import json
import sys
from pathlib import Path

import h5py
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.rgb565 import encode_rgb565_le, roundtrip_metrics

WORKTREE = Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128")
REFERENCE_DEPTH = Path(r"C:\ec\_deltar_formal\outputs\smoke\room\1647156045.486036_depth_m.npy")
OUT = ROOT / "samples" / "official_reverse"
FORMAL = ("letterbox_640x480", "resize_640x480", "scale_to_fill_center_crop_640x480")


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def source_from_official(rgb: np.ndarray, strategy: str) -> np.ndarray:
    if strategy == "letterbox_640x480":
        return rgb[60:420].copy()
    resized = np.asarray(Image.fromarray(rgb, mode="RGB").resize((640, 360), Image.Resampling.BILINEAR))
    if strategy != "scale_to_fill_center_crop_640x480":
        return resized
    source = np.zeros((360, 640, 3), dtype=np.uint8)
    center = np.asarray(Image.fromarray(rgb, mode="RGB").resize((480, 360), Image.Resampling.BILINEAR))
    source[:, 80:560] = center
    source[:, :80] = center[:, :1]
    source[:, 560:] = center[:, -1:]
    return source


def profile(profile_id: str, strategy: str, mask: np.ndarray, fr: np.ndarray, reference: bool) -> dict:
    data = {
        "schema_version": "deltar_p4_profile.v1",
        "profile_id": profile_id,
        "sample_kind": "official_reverse",
        "image": {"width": 640, "height": 360, "stride": 1280, "pixel_format": "RGB565_LE"},
        "canvas": {"strategy": strategy, "fill_rgb": [0, 0, 0]},
        "zone_mapping": {"strategy": "identity"},
        "histogram": {"mu_strategy": "distance_mm_to_m", "sigma_strategy": "range_sigma_mm_to_m"},
        "mask": {"strategy": "explicit_bool64", "values": mask.astype(bool).tolist()},
        "fr": {"strategy": "explicit_model_canvas", "values": fr.astype(float).tolist()},
        "seed": 0,
        "test_intrinsics": {"fx": 611.2, "fy": 609.6, "cx": 323.4, "cy": 184.9},
    }
    if reference:
        data["official_depth_reference"] = str(REFERENCE_DEPTH)
    return data


def build_one(h5_path: Path, index: int, strategy: str, *, reference: bool = False) -> Path:
    with h5py.File(h5_path, "r") as handle:
        rgb = handle["rgb"][:].astype(np.uint8)
        hist = handle["hist_data"][:].astype(np.float64)
        fr = handle["fr"][:].astype(np.float64)
        mask = handle["mask"][:].astype(bool)
    source = source_from_official(rgb, strategy)
    suffix = {"letterbox_640x480": "letterbox", "resize_640x480": "resize", "scale_to_fill_center_crop_640x480": "center_crop"}[strategy]
    sample_dir = OUT / f"official_{index:03d}_{suffix}"
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "image.rgb565").write_bytes(encode_rgb565_le(source))
    tof = {
        "schema_version": "rehab_tof_raw.v1",
        "sequence": index + 1,
        "timestamp_us": 0,
        "distance_mm": np.rint(hist[:, 0] * 1000.0).astype(int).tolist(),
        "range_sigma_mm": np.rint(hist[:, 1] * 1000.0).astype(int).tolist(),
        "target_status": [0] * 64,
        "nb_target_detected": [1] * 64,
    }
    write_json(sample_dir / "tof.json", tof)
    write_json(sample_dir / "profile.json", profile(f"official_reverse_{suffix}_v1", strategy, mask, fr, reference))
    write_json(sample_dir / "source_metadata.json", {
        "source_h5": str(h5_path),
        "official_frame_shape": [480, 640],
        "construction": strategy,
        "rgb888_to_rgb565_quantization": roundtrip_metrics(source),
    })
    return sample_dir


def build_direct(h5_path: Path, strategy: str) -> Path:
    with h5py.File(h5_path, "r") as handle:
        rgb = handle["rgb"][:].astype(np.uint8)
        hist = handle["hist_data"][:].astype(np.float64)
        mask = handle["mask"][:].astype(bool)
        fr = handle["fr"][:].astype(np.float64)
    source = source_from_official(rgb, "resize_640x480")
    fr[:, [0, 2]] *= 0.75
    if strategy == "direct_640x384_pad32":
        fr[:, [0, 2]] += 12.0
    suffix = "direct_360" if strategy == "direct_640x360" else "direct_384"
    sample_dir = OUT / f"official_000_{suffix}"
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "image.rgb565").write_bytes(encode_rgb565_le(source))
    write_json(sample_dir / "tof.json", {
        "schema_version": "rehab_tof_raw.v1", "sequence": 100, "timestamp_us": 0,
        "distance_mm": np.rint(hist[:, 0] * 1000).astype(int).tolist(),
        "range_sigma_mm": np.rint(hist[:, 1] * 1000).astype(int).tolist(),
        "target_status": [0] * 64, "nb_target_detected": [1] * 64,
    })
    write_json(sample_dir / "profile.json", profile(f"probe_{suffix}_v1", strategy, mask, fr, False))
    return sample_dir


def main() -> None:
    listing = json.loads((WORKTREE / "data" / "demo" / "data.json").read_text(encoding="utf-8"))["test"]
    h5_paths = [WORKTREE / "data" / "demo" / listing[i]["filename"] for i in range(3)]
    built = [build_one(h5_paths[0], 0, strategy, reference=True) for strategy in FORMAL]
    built.extend(build_one(h5_paths[i], i, "letterbox_640x480") for i in (1, 2))
    built.extend(build_direct(h5_paths[0], strategy) for strategy in ("direct_640x360", "direct_640x384_pad32"))
    print(json.dumps({"built": [str(path) for path in built]}, indent=2))


if __name__ == "__main__":
    main()
