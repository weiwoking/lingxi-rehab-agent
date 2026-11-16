from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(r"C:\ec\_deltar_stage6_test_scans")
GT_OUT = Path(r"C:\ec\_deltar_stage6_test_scans_gt")
SOURCE = ROOT / "samples" / "stage3_official" / "official_000" / "direct_640x360"
PROFILE_IDS = {
    "camera_profile_id": "camera_sc2336_640x360_rgb565_v1",
    "tof_profile_id": "tof_vl53l5cx_orientation_identity_v1",
    "calibration_profile_id": "calibration_unverified_stage6_v1",
    "algorithm_profile_id": "algorithm_stage6_official_v1",
    "scan_strategy_profile_id": "scan_strategy_stage6_guidance_v1",
}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def build_scan(name: str, frame_count: int, *, black: bool = False) -> Path:
    target = OUT / name
    target.mkdir(parents=True, exist_ok=False)
    write_json(target / "scan.json", {
        "schema_version": "deltar_scan_input.v1", "scan_id": name, "created_at": "2026-07-18T00:00:00Z",
        "frame_count": frame_count, "capture_mode": "single_frame" if frame_count == 1 else "multi_frame",
        "device": {"board": "esp32p4_function_ev", "camera": "SC2336", "tof": "VL53L5CX"},
        "profiles": PROFILE_IDS, "notes": None,
    })
    source_tof = json.loads((SOURCE / "tof.json").read_text(encoding="utf-8"))
    for sequence in range(frame_count):
        frame_id = f"frame_{sequence:06d}"
        folder = target / "frames" / frame_id
        folder.mkdir(parents=True)
        if black:
            (folder / "image.rgb565").write_bytes(bytes(460800))
        else:
            shutil.copyfile(SOURCE / "image.rgb565", folder / "image.rgb565")
        tof = {**source_tof, "sequence": sequence, "timestamp_us": sequence * 100000 + 500}
        write_json(folder / "tof.json", tof)
        write_json(folder / "frame.json", {
            "schema_version": "deltar_scan_frame.v1", "frame_id": frame_id, "sequence": sequence,
            "camera_timestamp_us": sequence * 100000 + 1000, "tof_timestamp_us": sequence * 100000 + 500, "pair_delta_us": 500,
            "image": {"filename": "image.rgb565", "width": 640, "height": 360, "pixel_format": "RGB565_LE", "stride_bytes": 1280, "payload_bytes": 460800},
            "tof": {"filename": "tof.json", "resolution": [8, 8]},
        })
    return target


def main() -> None:
    if OUT.exists() and GT_OUT.exists():
        names = sorted(path.name for path in OUT.iterdir() if path.is_dir())
        expected = {"single_valid", "multi_same_object", "multi_two_objects_same_class", "multi_partial_best_frame",
                    "one_frame_corrupt", "all_frames_rejected", "invalid_image_size", "invalid_tof_length",
                    "invalid_path", "unmeasured_calibration"}
        if set(names) != expected or not (GT_OUT / "manifest.json").is_file():
            raise SystemExit("existing Stage 6 assets are incomplete; refusing to overwrite")
        print(json.dumps({"status": "pass", "root": str(OUT), "scan_count": len(names), "reused": True}, ensure_ascii=False))
        return
    if OUT.exists() or GT_OUT.exists():
        raise SystemExit(f"refusing to overwrite partial Stage 6 assets: {OUT} or {GT_OUT}")
    OUT.mkdir(parents=True)
    GT_OUT.mkdir(parents=True)
    build_scan("single_valid", 1)
    build_scan("multi_same_object", 2)
    build_scan("multi_two_objects_same_class", 2)
    build_scan("multi_partial_best_frame", 2)
    corrupt = build_scan("one_frame_corrupt", 2)
    (corrupt / "frames" / "frame_000001" / "image.rgb565").write_bytes(bytes(100))
    build_scan("all_frames_rejected", 1, black=True)
    invalid_image = build_scan("invalid_image_size", 1)
    (invalid_image / "frames" / "frame_000000" / "image.rgb565").write_bytes(bytes(459999))
    invalid_tof = build_scan("invalid_tof_length", 1)
    tof_path = invalid_tof / "frames" / "frame_000000" / "tof.json"
    tof = json.loads(tof_path.read_text(encoding="utf-8")); tof["distance_mm"] = tof["distance_mm"][:-1]; write_json(tof_path, tof)
    invalid_path = build_scan("invalid_path", 1)
    frame_path = invalid_path / "frames" / "frame_000000" / "frame.json"
    frame = json.loads(frame_path.read_text(encoding="utf-8")); frame["image"]["filename"] = "..\\image.rgb565"; write_json(frame_path, frame)
    build_scan("unmeasured_calibration", 1)
    write_json(GT_OUT / "manifest.json", {
        "schema_version": "deltar_stage6_test_gt_manifest.v1", "inference_directories_contain_gt": False,
        "source_sample": "stage3_official/official_000", "use": "test construction provenance only",
    })
    write_json(ROOT / "outputs_stage6" / "test_scan_manifest.json", {
        "schema_version": "deltar_stage6_test_scan_manifest.v1", "root": str(OUT),
        "scan_names": sorted(path.name for path in OUT.iterdir() if path.is_dir()), "gt_root_separate": str(GT_OUT),
        "reproducible_builder": "scripts/build_stage6_test_scans.py", "gt_used_for_inference": False,
    })
    print(json.dumps({"status": "pass", "root": str(OUT), "scan_count": 10}, ensure_ascii=False))


if __name__ == "__main__":
    main()
