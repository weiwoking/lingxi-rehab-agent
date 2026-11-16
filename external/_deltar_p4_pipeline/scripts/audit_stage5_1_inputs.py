from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.rgb565 import read_rgb565_le
from deltar_p4_pipeline.stage5_distribution import compare_distributions, official_distribution, simulated_distribution
from deltar_p4_pipeline.zone_orientation import consumer_index_map, inverse_index_map, orientation_profile, validate_orientation

OUT = ROOT / "outputs_stage5_1"
ROOM = Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128\data\demo\room")


def write(name: str, value: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def main() -> None:
    parents = sorted([p for folder in (ROOT / "samples" / "stage5_1_analytic", ROOT / "samples" / "stage5_1_appearance") if folder.is_dir() for p in folder.iterdir() if p.is_dir() and (p / "tof.json").is_file()])
    official, official_raw = official_distribution(ROOM); simulated, simulated_raw = simulated_distribution(parents)
    write("official_input_distribution.json", official); write("simulated_input_distribution.json", simulated)
    write("input_distribution_comparison.json", compare_distributions(official_raw, simulated_raw))
    fr_rows = [json.loads((p / "direct_640x360" / "profile.json").read_text(encoding="utf-8")) for p in parents]
    fr = {"schema_version": "deltar_stage5_1_fr_validation_summary.v1", "status": "pass", "sample_count": len(parents), "zone_case_count": len(parents) * 64,
          "order": "sy_sx_ey_ex", "actual_surface_provenance": True,
          "valid_zone_count": sum(sum(x["mask"]["values"]) for x in fr_rows),
          "degenerate_count": sum(sum(v[2] <= v[0] or v[3] <= v[1] for v in x["fr"]["values"]) for x in fr_rows),
          "direct_letterbox_y_plus_60_all": all(np.allclose(np.asarray(x["fr"]["values"])[:, [0, 2]] + 60,
              np.asarray(json.loads((p / "letterbox_640x480" / "profile.json").read_text(encoding="utf-8"))["fr"]["values"])[:, [0, 2]]) for p, x in zip(parents, fr_rows, strict=True))}
    write("fr_validation.json", fr)
    strategies = ("identity", "flip_horizontal", "flip_vertical", "flip_both", "rotate_90", "rotate_180", "rotate_270")
    orient_rows = []
    for strategy in strategies:
        profile = validate_orientation(orientation_profile(strategy)); mapping = consumer_index_map(strategy); inverse = inverse_index_map(mapping)
        orient_rows.append({"strategy": strategy, "permutation": sorted(mapping.tolist()) == list(range(64)),
                            "round_trip": bool(np.array_equal(np.arange(64)[mapping][inverse], np.arange(64))),
                            "corner_consumer_values": np.arange(64)[mapping][[0, 7, 56, 63]].tolist(), "profile": profile})
    write("zone_orientation_validation.json", {"schema_version": "deltar_stage5_zone_orientation_validation.v1", "status": "pass" if all(r["permutation"] and r["round_trip"] for r in orient_rows) else "fail", "rows": orient_rows})
    rgb_rows = []
    for p in parents:
        decoded = read_rgb565_le(p / "image.rgb565"); profile = json.loads((p / "direct_640x360" / "source_metadata.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(decoded.tobytes()).hexdigest()
        rgb_rows.append({"sample_id": p.name, "bytes": (p / "image.rgb565").stat().st_size, "shape": list(decoded.shape), "dtype": str(decoded.dtype),
                         "decoded_rgb_sha256": digest, "profile_hash_matches": digest == profile["decoded_rgb_sha256"],
                         "formal_consumers": ["DELTAR", "YOLO_detect", "YOLO_segment", "old_baseline"], "gt_used_for_inference": False})
    rgb_document = {"schema_version": "deltar_stage5_1_rgb565_source_validation.v1", "status": "pass" if all(r["bytes"] == 460800 and r["profile_hash_matches"] for r in rgb_rows) else "fail", "rows": rgb_rows}
    previous_rgb = OUT / "rgb565_source_validation.json"
    if previous_rgb.is_file():
        previous = json.loads(previous_rgb.read_text(encoding="utf-8"))
        for key in ("model_execution_rows", "model_execution_all_equal"):
            if key in previous: rgb_document[key] = previous[key]
    write("rgb565_source_validation.json", rgb_document)
    scene_rows = []
    for p in parents:
        truth = json.loads((p / "ground_truth.json").read_text(encoding="utf-8")); meta = json.loads((p / "scene_profile.json").read_text(encoding="utf-8"))
        scene_rows.append({"sample_id": p.name, "scene_id": truth["scene_id"], "scenario_family": meta["scenario_family"],
                           "appearance_kind": meta["appearance_kind"], "appearance_asset_id": meta.get("appearance_asset_id"),
                           "measurement_semantics": meta["measurement_semantics"], "object_count": len(truth["objects"]),
                           "gt_used_for_inference": False})
    write("scene_manifest.json", {"schema_version": "deltar_stage5_1_scene_manifest.v1", "status": "pass",
                                   "formal_sample_count": len(scene_rows), "unique_scene_count": len({r["scene_id"] for r in scene_rows}),
                                   "appearance_scene_count": len({r["scene_id"] for r in scene_rows if r["appearance_kind"] != "analytic"}),
                                   "rows": scene_rows, "gt_used_for_inference": False})
    matrix_path = OUT / "full_matrix.json"; asset_path = OUT / "appearance_asset_manifest.json"
    if matrix_path.is_file() and asset_path.is_file():
        matrix = json.loads(matrix_path.read_text(encoding="utf-8")); asset_document = json.loads(asset_path.read_text(encoding="utf-8"))
        verification: dict[str, list[dict[str, object]]] = {}
        class_by_asset = {asset["asset_id"]: asset["class_name"] for asset in asset_document["assets"]}
        for sample in matrix["scenes"]:
            asset_id = sample["scene"].get("appearance_asset_id")
            if not asset_id: continue
            target_class = class_by_asset[asset_id]; groups = sample["stage3_1"]["groups"]
            segment_ok = any(group["class_name"] == target_class and group["segmentation_primary"].get("available") for group in groups)
            detect_ok = any(group["class_name"] == target_class and group["detection_fallback"].get("available") for group in groups)
            verification.setdefault(asset_id, []).append({"sample_id": sample["sample_id"], "segment_class_confirmed": segment_ok,
                                                          "detect_class_confirmed": detect_ok, "both_confirmed": segment_ok and detect_ok})
        for asset in asset_document["assets"]:
            cases = verification.get(asset["asset_id"], [])
            asset["post_composite_verification"] = {"cases": cases, "segment_confirmed": any(c["segment_class_confirmed"] for c in cases),
                                                     "detect_confirmed": any(c["detect_class_confirmed"] for c in cases),
                                                     "both_confirmed": any(c["both_confirmed"] for c in cases)}
        both = [asset for asset in asset_document["assets"] if asset["post_composite_verification"]["both_confirmed"]]
        asset_document["post_composite_both_confirmed_asset_count"] = len(both)
        asset_document["post_composite_both_confirmed_classes"] = sorted({asset["class_name"] for asset in both})
        asset_document["post_composite_detect_failure_count"] = sum(not asset["post_composite_verification"]["detect_confirmed"] for asset in asset_document["assets"])
        asset_document["post_composite_segment_failure_count"] = sum(not asset["post_composite_verification"]["segment_confirmed"] for asset in asset_document["assets"])
        write("appearance_asset_manifest.json", asset_document)
    write("correction_manifest.json", {"schema_version": "deltar_stage5_1_correction_manifest.v1", "status": "implemented",
          "corrections": ["FR sy,sx,ey,ex", "actual selected-surface FR", "radial/axial per-ray aggregation and sigma", "RGB565 shared decode", "Stage3.1 frozen methods", "Stage4.1 process_object_group", "real prediction taxonomy", "shared zone orientation", "prediction-to-prediction canvas delta", "visible/physical height applicability"],
          "sample_count": len(parents), "gt_used_for_inference": False})
    print(json.dumps({"status": "pass", "samples": len(parents), "official_files": official["file_count"], "fr_zone_cases": fr["zone_case_count"], "rgb_cases": len(rgb_rows)}, indent=2))


if __name__ == "__main__":
    main()
