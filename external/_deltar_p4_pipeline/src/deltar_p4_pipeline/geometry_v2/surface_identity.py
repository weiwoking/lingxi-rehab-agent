"""Physical surface association without support-height smoothing."""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np


def associate_same_surfaces(objects: list[dict[str, Any]], cfg: dict[str, Any], frame_id: str) -> None:
    """Attach identity only; never average, replace, or invent support height."""
    groups: dict[str, list[int]] = {}
    audits: list[list[dict[str, Any]]] = [[] for _ in objects]
    for left in range(len(objects)):
        left_support = objects[left]["generic_support"]
        left_mask = left_support.get("_surface_component_mask")
        left_id = left_support.get("support_surface_id")
        if not isinstance(left_mask, np.ndarray):
            continue
        for right in range(left + 1, len(objects)):
            right_support = objects[right]["generic_support"]
            right_mask = right_support.get("_surface_component_mask")
            right_id = right_support.get("support_surface_id")
            if not isinstance(right_mask, np.ndarray):
                continue
            same_id = bool(left_id and left_id == right_id)
            overlap = int(np.count_nonzero(left_mask & right_mask))
            radius = int(cfg["component_link_dilation_px"])
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
            dilated_overlap = int(np.count_nonzero(cv2.dilate(left_mask.astype(np.uint8), kernel).astype(bool) & right_mask))
            left_axial = left_support.get("_selected_median_axial_mm")
            right_axial = right_support.get("_selected_median_axial_mm")
            axial_delta = None if left_axial is None or right_axial is None else abs(float(left_axial) - float(right_axial))
            associated = bool(
                (same_id or max(overlap, dilated_overlap) >= int(cfg["minimum_component_overlap_pixels"]))
                and axial_delta is not None
                and axial_delta <= float(cfg["maximum_component_axial_delta_mm"])
            )
            row = {"peer_index": right, "same_surface_id": same_id, "overlap_pixels": overlap,
                   "dilated_overlap_pixels": dilated_overlap, "axial_delta_mm": axial_delta, "associated": associated}
            audits[left].append(row)
            audits[right].append({**row, "peer_index": left})
            if associated:
                key = str(left_id or right_id or f"overlap:{min(left, right)}")
                groups.setdefault(key, []).extend([left, right])
    for number, members in enumerate(groups.values(), 1):
        group_id = f"{frame_id}:physical_surface:{number:02d}"
        for index in sorted(set(members)):
            objects[index]["same_surface_group_id"] = group_id
    for index, item in enumerate(objects):
        item["consensus_used"] = False
        item["same_surface_audit"] = {"policy": "identity_only_no_height_mutation", "pairs": audits[index]}
        for key in ("_surface_component_mask", "_selected_median_axial_mm"):
            item["generic_support"].pop(key, None)
