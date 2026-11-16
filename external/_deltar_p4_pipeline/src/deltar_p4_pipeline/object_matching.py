from __future__ import annotations

from typing import Any


def bbox_iou(left: list[float], right: list[float]) -> float:
    ix1, iy1 = max(left[0], right[0]), max(left[1], right[1])
    ix2, iy2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return float(intersection / union) if union > 0 else 0.0


def greedy_match(detections: list[dict[str, Any]], segments: list[dict[str, Any]], threshold: float = 0.5) -> list[dict[str, Any]]:
    candidates = []
    for di, det in enumerate(detections):
        for si, seg in enumerate(segments):
            if det.get("class_name") == seg.get("class_name"):
                candidates.append((bbox_iou(det["bbox_xyxy"], seg["bbox_xyxy"]), di, si))
    used_d: set[int] = set()
    used_s: set[int] = set()
    output: list[dict[str, Any]] = []
    for iou, di, si in sorted(candidates, reverse=True):
        if iou < threshold or di in used_d or si in used_s:
            continue
        used_d.add(di); used_s.add(si)
        output.append({"matched_object_id": f"object_{len(output):03d}", "detect_instance_id": detections[di]["instance_id"],
                       "segment_instance_id": segments[si]["instance_id"], "match_iou": float(iou), "match_status": "matched"})
    for di, det in enumerate(detections):
        if di not in used_d:
            output.append({"matched_object_id": None, "detect_instance_id": det["instance_id"], "segment_instance_id": None,
                           "match_iou": None, "match_status": "unmatched_detect"})
    for si, seg in enumerate(segments):
        if si not in used_s:
            output.append({"matched_object_id": None, "detect_instance_id": None, "segment_instance_id": seg["instance_id"],
                           "match_iou": None, "match_status": "unmatched_segment"})
    return output
