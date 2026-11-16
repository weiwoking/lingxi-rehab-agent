from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import numpy as np

from deltar_p4_pipeline.official_object_benchmark import OFFICIAL_ROOT, load_hdf5, official_samples, read_json, unpack_mask, write_json
from deltar_p4_pipeline.official_old_chain_benchmark import estimate_old_distance, raw_zone_ablations
from deltar_p4_pipeline.official_zone_mapping import mapping_audit


def zone_occupancy(mask: np.ndarray, fr: np.ndarray, zones: list[int]) -> float:
    covered = np.zeros(mask.shape, dtype=bool)
    height, width = mask.shape
    for index in zones:
        sy, sx, ey, ex = (int(value) for value in fr[index])
        covered[max(0, sy):min(height, ey), max(0, sx):min(width, ex)] = True
    return float(np.logical_and(mask, covered).sum() / max(mask.sum(), 1))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("outputs_stage5_2a"))
    args = parser.parse_args()
    manifest = read_json(args.output_dir / "matched_object_manifest.json")
    samples = {row["sample_index"]: row for row in official_samples()}
    objects_by_sample: dict[int, list[dict]] = {}
    for obj in manifest["objects"]:
        objects_by_sample.setdefault(obj["sample_index"], []).append(obj)
    results = []
    first_fr = None
    all_fr_identical = True
    for sample_index, objects in objects_by_sample.items():
        data = load_hdf5(samples[sample_index])
        if first_fr is None:
            first_fr = data["fr"]
        else:
            all_fr_identical = all_fr_identical and bool(np.array_equal(first_fr, data["fr"]))
        for obj in objects:
            primary = estimate_old_distance(obj["detect_instance"]["bbox_xyxy"], data["hist_data"], data["fr"], data["mask"])
            legacy = estimate_old_distance(obj["detect_instance"]["bbox_xyxy"], data["hist_data"], data["fr"], data["mask"], legacy_flip=True)
            segment_mask = unpack_mask(obj["segment_instance"]["mask"])
            occupancy = zone_occupancy(segment_mask, data["fr"], primary["canonical_zone_indices"])
            results.append({
                "object_id": obj["object_id"],
                "sample_index": sample_index,
                "class_name": obj["class_name"],
                **primary,
                "object_to_zone_occupancy": occupancy,
                "zone_group": "single-zone" if primary["raw_zone_count"] == 1 else "mixed-zone",
                "legacy_hardcoded_flip": legacy,
                "raw_zone_ablations": raw_zone_ablations(obj["detect_instance"]["bbox_xyxy"], data["hist_data"], data["fr"], data["mask"]),
                "algorithm_provenance": {
                    "reference_files": [
                        r"C:\ec\esp-claw\pc\vision_interpreter\app\fusion.py",
                        r"C:\ec\esp-claw\pc\vision_interpreter\app\geometry.py",
                    ],
                    "bbox_input": True,
                    "official_hist_input": True,
                    "official_fr_mapping": True,
                    "official_mask_input": True,
                    "gt_used": False,
                    "segment_used": False,
                    "mad_scale": 3.0,
                    "statistic": "median",
                },
            })
    mapping = {
        "schema_version": "deltar_stage5_2a_zone_mapping.v1",
        **mapping_audit(first_fr),
        "all_processed_samples_fr_identical": all_fr_identical,
        "comparison": "main identity adapter preserves the old algorithm; legacy hardcoded flip is reported only as degradation/ablation",
    }
    write_json(args.output_dir / "official_zone_mapping.json", mapping)
    write_json(args.output_dir / "old_chain_results.json", {
        "schema_version": "deltar_stage5_2a_old_chain_results.v1",
        "object_count": len(results),
        "valid_count": sum(row["status"] == "valid" for row in results),
        "rejection_reasons": dict(Counter(row.get("rejection_reason") or "valid" for row in results)),
        "gt_used_for_inference": False,
        "results": results,
    })
    print(f"old chain: {sum(row['status']=='valid' for row in results)}/{len(results)} valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
