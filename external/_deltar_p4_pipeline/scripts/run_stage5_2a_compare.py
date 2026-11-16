from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from deltar_p4_pipeline.object_regions import binary_erosion
from deltar_p4_pipeline.official_depth_metrics import error_record, grouped, numeric_grouped, summarize
from deltar_p4_pipeline.official_object_benchmark import OFFICIAL_ROOT, bbox_mask, load_hdf5, official_samples, read_json, region_statistics, unpack_mask, write_json
from deltar_p4_pipeline.stage5_2a_validation import acceptance


def distance_bin(value: float | None) -> str:
    if value is None:
        return "invalid"
    if value < 1.0:
        return "0-1m"
    if value < 2.0:
        return "1-2m"
    if value < 3.0:
        return "2-3m"
    return "3m+"


def short_side_bin(row: dict) -> str:
    value = float(row["short_side_px"])
    return "<64px" if value < 64 else ("64-160px" if value < 160 else ">=160px")


def background_bin(row: dict) -> str:
    value = float(row["bbox_background_ratio"])
    return "low_<25%" if value < 0.25 else ("medium_25-50%" if value < 0.50 else "high_>=50%")


def area_bin(value: float) -> str:
    return "small_<5k" if value < 5000 else ("medium_5k-30k" if value < 30000 else "large_>=30k")


def zone_count_bin(row: dict) -> str:
    value = int(row.get("valid_zone_count") or 0)
    return "0" if value == 0 else ("1" if value == 1 else ("2-4" if value <= 4 else "5+"))


def occupancy_bin(row: dict) -> str:
    value = float(row.get("object_to_zone_occupancy") or 0.0)
    return "low_<50%" if value < .5 else ("medium_50-90%" if value < .9 else "high_>=90%")


def improvement(old_value: float | None, new_value: float | None) -> dict:
    if old_value is None or new_value is None:
        return {"absolute_m": None, "percent": None}
    delta = old_value - new_value
    return {"absolute_m": delta, "percent": delta / max(old_value, 1e-12) * 100.0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("outputs_stage5_2a"))
    args = parser.parse_args()
    matched = read_json(args.output_dir / "matched_object_manifest.json")
    old_doc = read_json(args.output_dir / "old_chain_results.json")
    new_doc = read_json(args.output_dir / "new_chain_results.json")
    old_by_id = {row["object_id"]: row for row in old_doc["results"]}
    new_by_id = {row["object_id"]: row for row in new_doc["results"]}
    samples = {row["sample_index"]: row for row in official_samples()}
    data_cache = {}
    paired_rows, ablations = [], []
    for obj in matched["objects"]:
        truth = obj["gt_reference"].get("gt_distance_m")
        old, new = old_by_id.get(obj["object_id"], {}), new_by_id.get(obj["object_id"], {})
        old_distance, new_distance = old.get("old_distance_m"), new.get("new_distance_m")
        old_error, new_error = error_record(old_distance, truth), error_record(new_distance, truth)
        row = {
            "object_id": obj["object_id"], "sample_index": obj["sample_index"], "class_name": obj["class_name"],
            "gt_distance_m": truth, "old_distance_m": old_distance, "new_distance_m": new_distance,
            "old_status": old.get("status", "missing"), "new_status": new.get("status", "missing"),
            "old_rejection_reason": old.get("rejection_reason"), "new_rejection_reason": new.get("rejection_reason"),
            **{f"old_{key}": value for key, value in old_error.items()},
            **{f"new_{key}": value for key, value in new_error.items()},
            "paired_valid": truth is not None and old_distance is not None and new_distance is not None,
            "class_id": obj["class_id"], "size_bin": obj["size_bin"], "bbox_area_px": obj["bbox_area_px"],
            "mask_area_px": obj["mask_area_px"], "short_side_px": obj["short_side_px"], "border": obj["border"],
            "bbox_background_ratio": obj["bbox_background_ratio"], "multimodal": obj["gt_reference"]["regions"]["segment_eroded_3px"].get("multimodal"),
            "valid_zone_count": old.get("valid_zone_count"), "raw_zone_count": old.get("raw_zone_count"),
            "zone_group": old.get("zone_group"), "object_to_zone_occupancy": old.get("object_to_zone_occupancy"),
            "distance_bin": distance_bin(truth),
        }
        paired_rows.append(row)
        if obj["sample_index"] not in data_cache:
            data_cache[obj["sample_index"]] = load_hdf5(samples[obj["sample_index"]])
        data = data_cache[obj["sample_index"]]
        mask = unpack_mask(obj["segment_instance"]["mask"])
        bbox = bbox_mask(obj["detect_instance"]["bbox_xyxy"], data["depth"].shape)
        gt_bbox = region_statistics(data["depth"], bbox)
        ablations.append({
            "object_id": obj["object_id"],
            "O0_gt_oracle_segment_m": truth,
            "O1_old_8x8_m": old_distance,
            "O1b_old_legacy_flip_both_m": old.get("legacy_hardcoded_flip", {}).get("old_distance_m"),
            "O2_old_bbox_oracle_depth_m": gt_bbox.get("median_m"),
            "O3_new_deltar_segment_m": new_distance,
            "O4_new_deltar_bbox_m": new.get("bbox_full_median_m"),
            "O5_gt_depth_segment_m": truth,
            "O6_gt_depth_bbox_m": gt_bbox.get("median_m"),
            "O7_official_raw_zone": old.get("raw_zone_ablations"),
        })
    paired_valid = [row for row in paired_rows if row["paired_valid"]]
    old_all, new_all = summarize(paired_rows, "old"), summarize(paired_rows, "new")
    old_summary, new_summary = summarize(paired_valid, "old"), summarize(paired_valid, "new")
    class_summary = {"scope": "paired valid only", "old": grouped(paired_valid, "class_name", "old"), "new": grouped(paired_valid, "class_name", "new")}
    size_summary = {"scope": "paired valid only", "old": grouped(paired_valid, "size_bin", "old"), "new": grouped(paired_valid, "size_bin", "new"),
                    "short_side": {"old": numeric_grouped(paired_valid, "short_side_bin", short_side_bin, "old"), "new": numeric_grouped(paired_valid, "short_side_bin", short_side_bin, "new")},
                    "bbox_area": {"old": numeric_grouped(paired_valid, "bbox_area_bin", lambda r: area_bin(float(r["bbox_area_px"])), "old"), "new": numeric_grouped(paired_valid, "bbox_area_bin", lambda r: area_bin(float(r["bbox_area_px"])), "new")},
                    "mask_area": {"old": numeric_grouped(paired_valid, "mask_area_bin", lambda r: area_bin(float(r["mask_area_px"])), "old"), "new": numeric_grouped(paired_valid, "mask_area_bin", lambda r: area_bin(float(r["mask_area_px"])), "new")}}
    distance_summary = {"scope": "paired valid only", "old": grouped(paired_valid, "distance_bin", "old"), "new": grouped(paired_valid, "distance_bin", "new")}
    zone_groups = {"old": grouped(paired_valid, "zone_group", "old"), "new": grouped(paired_valid, "zone_group", "new")}
    background_groups = {"old": numeric_grouped(paired_valid, "background_bin", background_bin, "old"), "new": numeric_grouped(paired_valid, "background_bin", background_bin, "new")}
    mixed_old = zone_groups["old"].get("mixed-zone", {}).get("absolute_error_m", {}).get("median")
    mixed_new = zone_groups["new"].get("mixed-zone", {}).get("absolute_error_m", {}).get("median")
    mixed_change = improvement(mixed_old, mixed_new)
    mixed_summary = {"zone_groups": zone_groups, "background_groups": background_groups,
                     "valid_zone_count_groups": {"old": numeric_grouped(paired_valid, "valid_zone_count_bin", zone_count_bin, "old"), "new": numeric_grouped(paired_valid, "valid_zone_count_bin", zone_count_bin, "new")},
                     "occupancy_groups": {"old": numeric_grouped(paired_valid, "occupancy_bin", occupancy_bin, "old"), "new": numeric_grouped(paired_valid, "occupancy_bin", occupancy_bin, "new")},
                     "border_groups": {"old": grouped(paired_valid, "border", "old"), "new": grouped(paired_valid, "border", "new")},
                     "multimodal_groups": {"old": grouped(paired_valid, "multimodal", "old"), "new": grouped(paired_valid, "multimodal", "new")},
                     "new_better": mixed_change["percent"] is not None and mixed_change["percent"] >= 10.0,
                     "material_improvement_threshold": "at least 10% lower median absolute error",
                     "mixed_zone_median_improvement": mixed_change}
    median_change = improvement(old_summary["absolute_error_m"]["median"], new_summary["absolute_error_m"]["median"])
    relative_change = improvement(old_summary["relative_error"]["median"], new_summary["relative_error"]["median"])
    old_clearly_better = [row for row in paired_valid if row["old_absolute_error_m"] + 0.05 < row["new_absolute_error_m"]]
    paired_class_count = len({row["class_name"] for row in paired_valid})
    comparison = {
        "schema_version": "deltar_stage5_2a_old_vs_new_summary.v1",
        "matched_object_count": len(paired_rows), "paired_valid_count": len(paired_valid),
        "matched_class_count": matched["class_count"], "paired_class_count": paired_class_count,
        "main_metric_scope": "same 563-style paired set: old valid, new valid, GT valid",
        "old": old_summary, "new": new_summary,
        "all_matched_chain_coverage": {"old": old_all, "new": new_all},
        "median_absolute_error_improvement": median_change,
        "median_relative_error_improvement": relative_change,
        "old_clearly_better_count_5cm_margin": len(old_clearly_better),
        "old_clearly_better_object_ids": [row["object_id"] for row in old_clearly_better],
        "paired_exclusion_reasons": dict(Counter("old_reject" if row["old_status"] != "valid" else "new_reject" if row["new_status"] != "valid" else "gt_invalid" for row in paired_rows if not row["paired_valid"])),
    }
    acceptance_doc = acceptance(len(paired_rows), len(paired_valid), paired_class_count, old_summary, new_summary, mixed_summary)
    write_json(args.output_dir / "paired_results.json", {"schema_version": "deltar_stage5_2a_paired_results.v1", "pair_rule": "same matched GT object; old valid; new valid; GT valid", "results": paired_rows})
    route_summary = {}
    for route in ("O1_old_8x8_m", "O1b_old_legacy_flip_both_m", "O2_old_bbox_oracle_depth_m", "O3_new_deltar_segment_m", "O4_new_deltar_bbox_m", "O5_gt_depth_segment_m", "O6_gt_depth_bbox_m"):
        route_rows = []
        for item in ablations:
            err = error_record(item.get(route), item.get("O0_gt_oracle_segment_m"))
            route_rows.append({"route_absolute_error_m": err["absolute_error_m"], "route_relative_error": err["relative_error"], "route_rejection_reason": None})
        route_summary[route] = summarize(route_rows, "route")
    segmentation_contribution = improvement(route_summary["O4_new_deltar_bbox_m"]["absolute_error_m"]["median"], route_summary["O3_new_deltar_segment_m"]["absolute_error_m"]["median"])
    write_json(args.output_dir / "ablation_matrix.json", {"schema_version": "deltar_stage5_2a_ablation_matrix.v1", "route_summary": route_summary,
               "segmentation_median_error_improvement": segmentation_contribution, "objects": ablations})
    write_json(args.output_dir / "class_summary.json", class_summary)
    write_json(args.output_dir / "size_summary.json", size_summary)
    write_json(args.output_dir / "distance_summary.json", distance_summary)
    write_json(args.output_dir / "mixed_zone_summary.json", mixed_summary)
    write_json(args.output_dir / "ten_percent_acceptance.json", {"old_valid_count": old_summary["valid_count"], "old_within_10_percent_count": old_summary["within_10_percent_count"], "old_within_10_percent_rate": old_summary["within_10_percent_rate"], "new_valid_count": new_summary["valid_count"], "new_within_10_percent_count": new_summary["within_10_percent_count"], "new_within_10_percent_rate": new_summary["within_10_percent_rate"]})
    write_json(args.output_dir / "old_vs_new_summary.json", comparison)
    write_json(args.output_dir / "stage5_2a_acceptance.json", acceptance_doc)
    print(f"compare: {len(paired_valid)}/{len(paired_rows)} paired; verdict {acceptance_doc['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
