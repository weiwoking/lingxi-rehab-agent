from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch

from deltar_p4_pipeline.deltar_bridge import DeltarBridge, import_official_module
from deltar_p4_pipeline.object_regions import binary_erosion
from deltar_p4_pipeline.official_object_benchmark import bbox_mask, load_hdf5, official_samples, read_json, region_statistics, unpack_mask, write_json


def input_payload(data: dict[str, np.ndarray], official, device: str) -> dict:
    rgb = torch.from_numpy(np.ascontiguousarray(data["rgb"].transpose(2, 0, 1))).float() / 255.0
    mean = torch.tensor([0.485, 0.456, 0.406], dtype=torch.float32).view(3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], dtype=torch.float32).view(3, 1, 1)
    rgb = ((rgb - mean) / std).unsqueeze(0).to(device)
    raw_hist = torch.from_numpy(data["hist_data"]).to(torch.float64)
    mask = torch.from_numpy(data["mask"].astype(bool))
    hypotheses = official.sample_point_from_hist_parallel(raw_hist, mask, SimpleNamespace(zone_sample_num=16, sample_uniform=False))
    fr = torch.from_numpy(data["fr"]).to(torch.float32)
    patch = official.patch_info_from_rect_data(fr)
    batched_patch = {}
    for key, value in patch.items():
        if isinstance(value, dict):
            batched_patch[key] = {subkey: item.unsqueeze(0).to(device) for subkey, item in value.items()}
        else:
            batched_patch[key] = torch.tensor([value], dtype=torch.int64, device=device)
    return {
        "rgb": rgb,
        "additional": {
            "hist_data": hypotheses.unsqueeze(0).to(device),
            "rect_data": fr.unsqueeze(0).to(device),
            "mask": mask.unsqueeze(0).to(device),
            "patch_info": batched_patch,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("outputs_stage5_2a"))
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args()
    worktree = Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128")
    checkpoint = Path(r"C:\ec\_deltar_formal\assets\nyu.pt")
    bridge = DeltarBridge(worktree, checkpoint, args.device)
    model = bridge._load_model()
    official = import_official_module("src.utils.dataloader", worktree)
    manifest = read_json(args.output_dir / "matched_object_manifest.json")
    objects_by_sample: dict[int, list[dict]] = {}
    for obj in manifest["objects"]:
        objects_by_sample.setdefault(obj["sample_index"], []).append(obj)
    results = []
    inference_count = 0
    with torch.inference_mode():
        for sample in official_samples():
            objects = objects_by_sample.get(sample["sample_index"], [])
            data = load_hdf5(sample)
            payload = input_payload(data, official, args.device)
            _, raw_prediction = model(payload)
            dense = bridge._postprocess(raw_prediction, data["rgb"].shape[:2])
            inference_count += 1
            for obj in objects:
                segment = unpack_mask(obj["segment_instance"]["mask"])
                eroded = binary_erosion(segment, 3)
                segment_result = region_statistics(dense, eroded)
                bbox_result = region_statistics(dense, bbox_mask(obj["detect_instance"]["bbox_xyxy"], dense.shape))
                distance = segment_result.get("mad_filtered_median_m") if segment_result["status"] == "valid" else None
                results.append({
                    "object_id": obj["object_id"],
                    "sample_index": sample["sample_index"],
                    "class_name": obj["class_name"],
                    "status": segment_result["status"],
                    "new_distance_m": distance,
                    "region_method": "segment_eroded_3px",
                    "statistic_method": "mad_filtered_median",
                    "pixel_count": segment_result["pixel_count"],
                    "valid_count": segment_result["valid_count"],
                    "mad_m": segment_result.get("mad_m"),
                    "iqr_m": segment_result.get("iqr_m"),
                    "multimodal": segment_result.get("multimodal"),
                    "rejection_reason": segment_result.get("rejection_reason"),
                    "warnings": segment_result.get("warnings", []),
                    "segment_statistics": segment_result,
                    "bbox_full_median_m": bbox_result.get("median_m") if bbox_result["status"] == "valid" else None,
                    "bbox_statistics": bbox_result,
                    "inference_contract": {
                        "path": "direct official native 480x640",
                        "inputs": ["official RGB", "official hist_data", "official FR", "official mask", "official patch_info_from_rect_data"],
                        "stage5_simulator_used": False,
                        "gt_used_for_inference": False,
                        "segment_used_by_deltar_model": False,
                        "segment_used_for_object_region_after_dense_inference": True,
                    },
                })
    write_json(args.output_dir / "new_chain_results.json", {
        "schema_version": "deltar_stage5_2a_new_chain_results.v1",
        "official_native_sample_inference_count": inference_count,
        "object_count": len(results),
        "valid_count": sum(row["status"] == "valid" for row in results),
        "rejection_reasons": dict(Counter(row.get("rejection_reason") or "valid" for row in results)),
        "provenance": bridge.provenance(),
        "gt_used_for_inference": False,
        "results": results,
    })
    print(f"new chain: {sum(row['status']=='valid' for row in results)}/{len(results)} valid; {inference_count} official frames inferred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
