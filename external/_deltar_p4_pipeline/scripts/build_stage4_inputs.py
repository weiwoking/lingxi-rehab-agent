from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE31 = ROOT / "outputs_stage3_1"
OUT = ROOT / "outputs_stage4" / "input_groups_manifest.json"


def candidate(record: dict, source_type: str, sample_id: str) -> dict:
    recommended = record.get("recommended_candidate", {})
    return {
        "available": True,
        "source_type": source_type,
        "object_id": record["object_id"],
        "confidence": record["class_confidence"],
        "bbox_xyxy": record["bbox_xyxy"],
        "mask_npz_path": f"outputs_stage3/{sample_id}/segment_masks.npz" if source_type == "segmentation" else None,
        "mask_key": record.get("mask_key"),
        "region_method": "segment_eroded_3px" if source_type == "segmentation" else "bbox_full",
        "statistic_method": "mad_median" if source_type == "segmentation" else "median",
        "canvas_candidates": {
            canvas: {
                "status": item.get("status"),
                "surface_depth_m": item.get("surface_depth_m"),
                "pixel_count": item.get("pixel_count"),
                "valid_ratio": item.get("valid_ratio"),
                "multimodal": item.get("multimodal"),
            }
            for canvas, item in recommended.items()
            if canvas in {"direct_640x360", "letterbox_640x480"}
        },
    }


def unavailable(source_type: str) -> dict:
    return {"available": False, "source_type": source_type, "object_id": None, "confidence": None, "bbox_xyxy": [], "mask_npz_path": None, "mask_key": None, "region_method": None, "statistic_method": None, "canvas_candidates": {}}


def build() -> dict:
    groups: list[dict] = []
    samples: list[str] = []
    for result_path in sorted(STAGE31.glob("official_*/stage3_1_object_depth_results.json")):
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        sample_id = payload["sample_id"]
        samples.append(sample_id)
        segmentation = {row["object_id"]: row for row in payload["object_sets"]["segmentation_native"]}
        detection = {row["object_id"]: row for row in payload["object_sets"]["detection_native"]}
        paired_detection: set[str] = set()
        pair_by_seg = {}
        for pair in payload["object_sets"]["matched_comparison"]:
            pair_by_seg[pair["segment_object_id"]] = pair
            paired_detection.add(pair["detect_object_id"])
        for seg_id, seg in segmentation.items():
            pair = pair_by_seg.get(seg_id)
            det = detection[pair["detect_object_id"]] if pair else None
            groups.append({
                "sample_id": sample_id,
                "logical_object_id": f"{sample_id}:logical:{len(groups):03d}",
                "class_id": seg["class_id"],
                "class_name": seg["class_name"],
                "segmentation_primary": candidate(seg, "segmentation", sample_id),
                "detection_fallback": candidate(det, "detection_fallback", sample_id) if det else unavailable("detection_fallback"),
                "match": {"status": "matched" if pair else "unmatched_segmentation", "iou": pair.get("match_iou") if pair else None},
                "source_depth_refs": {"direct_640x360": f"outputs_stage3/{sample_id}/direct_depth_source.npy", "letterbox_640x480": f"outputs_stage3/{sample_id}/letterbox_depth_source.npy"},
                "provenance": {"stage3_1_result": result_path.relative_to(ROOT).as_posix(), "segmentation_object_id": seg_id, "detection_object_id": det["object_id"] if det else None, "gt_used_for_inference": False},
            })
        for det_id, det in detection.items():
            if det_id in paired_detection:
                continue
            groups.append({
                "sample_id": sample_id,
                "logical_object_id": f"{sample_id}:logical:{len(groups):03d}",
                "class_id": det["class_id"],
                "class_name": det["class_name"],
                "segmentation_primary": unavailable("segmentation"),
                "detection_fallback": candidate(det, "detection_fallback", sample_id),
                "match": {"status": "unmatched_detection", "iou": None},
                "source_depth_refs": {"direct_640x360": f"outputs_stage3/{sample_id}/direct_depth_source.npy", "letterbox_640x480": f"outputs_stage3/{sample_id}/letterbox_depth_source.npy"},
                "provenance": {"stage3_1_result": result_path.relative_to(ROOT).as_posix(), "segmentation_object_id": None, "detection_object_id": det_id, "gt_used_for_inference": False},
            })
    counts = {
        "samples": len(samples),
        "groups": len(groups),
        "matched": sum(g["match"]["status"] == "matched" for g in groups),
        "unmatched_segmentation": sum(g["match"]["status"] == "unmatched_segmentation" for g in groups),
        "unmatched_detection": sum(g["match"]["status"] == "unmatched_detection" for g in groups),
    }
    manifest = {"schema_version": "deltar_stage4_object_group_input.v1.1", "status": "success", "counts": counts, "object_groups": groups, "provenance": {"stage3_1_schema": "deltar_stage3_1_object_sets.v1", "source_depth_required_shape": [360, 640], "scalar_surface_depth_is_not_geometry_input": True}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(build()["counts"], indent=2))
