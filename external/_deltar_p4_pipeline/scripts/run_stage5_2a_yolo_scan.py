from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from deltar_p4_pipeline.object_matching import bbox_iou
from deltar_p4_pipeline.official_object_benchmark import (
    OFFICIAL_ROOT,
    bbox_mask,
    gt_reference,
    load_hdf5,
    match_detect_segment,
    official_samples,
    pack_mask,
    write_json,
)
from deltar_p4_pipeline.yolo_worker_client import run_yolo_worker

DETECT_HASH = "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"
SEGMENT_HASH = "55ed65c56c91713d23e8402371c6c49a6fd84f257f7dce452e8d70e41dcbe152"


def size_bin(mask_area: int, shape: tuple[int, int]) -> str:
    ratio = mask_area / float(shape[0] * shape[1])
    return "small" if ratio < 0.02 else ("medium" if ratio < 0.10 else "large")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("outputs_stage5_2a"))
    parser.add_argument("--confidence", type=float, default=0.25)
    args = parser.parse_args()
    samples = official_samples()
    temp = Path("_codex_tmp_stage5_2a_yolo").resolve()
    rgb_dir, worker_dir = temp / "rgb", temp / "worker"
    rgb_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for sample in samples:
        rgb = load_hdf5(sample)["rgb"]
        path = rgb_dir / f"{sample['sample_index']:03d}_{sample['sample_id']}.png"
        Image.fromarray(rgb, mode="RGB").save(path)
        paths.append(path)
    python = Path(r"C:\ec\_conda_envs\vision_interpreter\python.exe")
    worker = Path("workers/yolo_worker.py").resolve()
    detect = run_yolo_worker(python, worker, Path(r"C:\ec\esp-claw\pc\vision_interpreter\models\yolo11n.pt"), "detect", paths, worker_dir / "detect", expected_sha256=DETECT_HASH, confidence=args.confidence, timeout_s=1200)
    segment = run_yolo_worker(python, worker, Path("models/yolo11n-seg.pt").resolve(), "segment", paths, worker_dir / "segment", expected_sha256=SEGMENT_HASH, confidence=args.confidence, timeout_s=1200)
    manifest_samples = []
    object_rows = []
    diagnostics = []
    class_names: set[str] = set()
    for sample, d_out, s_out in zip(samples, detect, segment, strict=True):
        data = load_hdf5(sample)
        detections = d_out["document"]["instances"]
        segments = s_out["document"]["instances"]
        masks = s_out["masks"]
        packed_segments = []
        for instance in segments:
            packed_segments.append({**instance, "mask": pack_mask(masks[instance["mask_key"]])})
        matched, unmatched = match_detect_segment(detections, segments, masks, threshold=0.5)
        used_segments = {row["segment_index"] for row in matched}
        for local_index, row in enumerate(matched):
            det, seg = detections[row["detect_index"]], segments[row["segment_index"]]
            mask = masks[seg["mask_key"]]
            bbox_region = bbox_mask(det["bbox_xyxy"], mask.shape)
            mask_area, bbox_area = int(mask.sum()), int(bbox_region.sum())
            x1, y1, x2, y2 = det["bbox_xyxy"]
            border = bool(x1 <= 1 or y1 <= 1 or x2 >= mask.shape[1] - 1 or y2 >= mask.shape[0] - 1)
            reference = gt_reference(data["depth"], mask)
            object_id = f"{sample['sample_index']:03d}_object_{local_index:03d}"
            class_names.add(det["class_name"])
            object_rows.append({
                "object_id": object_id,
                "sample_index": sample["sample_index"],
                "sample_id": sample["sample_id"],
                "relative_path": sample["relative_path"],
                "class_id": det["class_id"],
                "class_name": det["class_name"],
                "detect_instance": det,
                "segment_instance": {**seg, "mask": pack_mask(mask)},
                "match_iou": row["match_iou"],
                "match_threshold": 0.5,
                "gt_reference": reference,
                "bbox_area_px": bbox_area,
                "mask_area_px": mask_area,
                "short_side_px": float(min(x2 - x1, y2 - y1)),
                "border": border,
                "size_bin": size_bin(mask_area, mask.shape),
                "bbox_background_ratio": float(max(0.0, 1.0 - mask_area / max(bbox_area, 1))),
            })
        for item in unmatched:
            reason = item["reason"]
            if item["detect_index"] is not None:
                det = detections[item["detect_index"]]
                overlaps = [(bbox_iou(det["bbox_xyxy"], seg["bbox_xyxy"]), si, seg) for si, seg in enumerate(segments)]
                if overlaps:
                    score, si, seg = max(overlaps)
                    if score >= 0.5 and det["class_name"] != seg["class_name"]:
                        reason = "wrong_class"
                    elif score >= 0.5 and si in used_segments:
                        reason = "duplicate"
            diagnostics.append({"sample_index": sample["sample_index"], **item, "reason": reason})
        manifest_samples.append({
            **sample,
            "image": d_out["document"]["image"],
            "detections": detections,
            "segments": packed_segments,
            "matched_count": len(matched),
            "diagnostics": unmatched,
        })
    yolo_manifest = {
        "schema_version": "deltar_stage5_2a_yolo_object_manifest.v1",
        "official_sample_count": len(samples),
        "confidence_threshold": args.confidence,
        "coordinate_system": "official original 640x480 RGB pixels; xyxy half-open",
        "gt_used_for_detection": False,
        "detect_model": detect[0]["document"]["model"],
        "segment_model": segment[0]["document"]["model"],
        "samples": manifest_samples,
    }
    matched_manifest = {
        "schema_version": "deltar_stage5_2a_matched_objects.v1",
        "official_sample_count": len(samples),
        "match_rule": "same class, greedy one-to-one bbox IoU >= 0.5, non-empty segment mask, valid GT region required for primary truth",
        "matched_object_count": len(object_rows),
        "gt_valid_matched_object_count": sum(row["gt_reference"]["status"] == "valid" for row in object_rows),
        "class_count": len(class_names),
        "classes": sorted(class_names),
        "diagnostics": diagnostics,
        "objects": object_rows,
    }
    write_json(args.output_dir / "yolo_object_manifest.json", yolo_manifest)
    write_json(args.output_dir / "matched_object_manifest.json", matched_manifest)
    resolved = temp.resolve()
    expected_parent = Path.cwd().resolve()
    if resolved.parent == expected_parent and resolved.name == "_codex_tmp_stage5_2a_yolo":
        shutil.rmtree(resolved)
    print(f"YOLO: {len(samples)} samples, {len(object_rows)} matched, {len(class_names)} classes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
