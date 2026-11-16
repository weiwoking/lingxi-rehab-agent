"""Class-agnostic support estimation and geometry-derived surface grouping."""
from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np


def bbox_mask(bbox: list[float], shape: tuple[int, int]) -> np.ndarray:
    x1, y1, x2, y2 = (float(v) for v in bbox)
    result = np.zeros(shape, dtype=bool)
    ix1, iy1 = max(0, int(math.floor(x1))), max(0, int(math.floor(y1)))
    ix2, iy2 = min(shape[1], int(math.ceil(x2))), min(shape[0], int(math.ceil(y2)))
    if ix2 > ix1 and iy2 > iy1:
        result[iy1:iy2, ix1:ix2] = True
    return result


def bottom_contour(region: np.ndarray) -> np.ndarray:
    region = np.asarray(region, dtype=bool)
    xs = np.flatnonzero(np.any(region, axis=0))
    if not xs.size:
        return np.empty((0, 2), dtype=np.float64)
    ys = np.asarray([np.flatnonzero(region[:, x])[-1] for x in xs], dtype=np.float64)
    keep = np.ones(xs.size, dtype=bool)
    for i in range(xs.size):
        local = ys[max(0, i - 2):min(xs.size, i + 3)]
        if local.size >= 3:
            keep[i] = abs(ys[i] - np.median(local)) <= 3.0
    return np.column_stack((xs[keep], ys[keep]))


def _context_mask(region: np.ndarray, bbox: list[float], cfg: dict[str, Any]) -> np.ndarray:
    shape = region.shape
    contour = bottom_contour(region)
    result = np.zeros(shape, dtype=bool)
    down, side, above = int(cfg["context_down_px"]), int(cfg["context_side_px"]), int(cfg["context_above_px"])
    if contour.size:
        for x_value, y_value in contour:
            x, y = int(round(x_value)), int(round(y_value))
            result[max(0, y - above):min(shape[0], y + down + 1), max(0, x - 1):min(shape[1], x + 2)] = True
        x1, _, x2, y2 = (float(v) for v in bbox)
        result[max(0, int(y2) - above):min(shape[0], int(y2) + down + 1), max(0, int(x1) - side):min(shape[1], int(x2) + side + 1)] = True
    dilated = cv2.dilate(region.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1).astype(bool)
    return result & ~dilated


def estimate_support(
    region: np.ndarray,
    bbox: list[float],
    axial_mm: np.ndarray,
    world_points_mm: np.ndarray,
    selected_valid: np.ndarray,
    support_planes: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Return a finite generic support, preferring real/local surface evidence."""
    world_z = np.asarray(world_points_mm[..., 2], dtype=np.float64)
    finite = np.isfinite(world_z) & np.isfinite(axial_mm) & (axial_mm > 0)
    diagnostics: dict[str, Any] = {"global_plane_count": len(support_planes), "local_candidates": []}
    # Preserve any actual accepted global support surface. This branch is generic;
    # no expected shelf height is present.
    if support_planes:
        object_bottom = float(np.percentile(world_z[selected_valid], 2))
        viable = []
        for plane in support_planes:
            if plane.get("label") != "support_surface":
                continue
            height = float(plane["median_height_mm"])
            gap = object_bottom - height
            if -30 <= gap <= 160:
                viable.append((abs(gap), plane))
        if viable:
            plane = min(viable, key=lambda x: x[0])[1]
            return {"support_mm": float(plane["median_height_mm"]), "source": "matched_global_plane", "confidence": 0.9, "fallback_reason": None, "surface_spread_mm": float(plane.get("residual_rms_mm", 0.0)), "diagnostics": diagnostics}

    context = _context_mask(region, bbox, cfg) & finite
    values = world_z[context]
    if values.size >= int(cfg["minimum_component_pixels"]):
        width = float(cfg["z_histogram_bin_mm"])
        lo, hi = np.percentile(values, [1, 99])
        edges = np.arange(math.floor(lo / width) * width, hi + 2 * width, width)
        counts, edges = np.histogram(values, bins=edges)
        order = np.argsort(counts)[::-1]
        for bin_index in order[: min(8, counts.size)]:
            centre = (edges[bin_index] + edges[bin_index + 1]) / 2.0
            cluster = context & (np.abs(world_z - centre) <= float(cfg["z_cluster_half_width_mm"]))
            count, labels, stats, _ = cv2.connectedComponentsWithStats(cluster.astype(np.uint8), 8)
            for label in range(1, count):
                component = labels == label
                pixels = int(stats[label, cv2.CC_STAT_AREA])
                span = int(max(stats[label, cv2.CC_STAT_WIDTH], stats[label, cv2.CC_STAT_HEIGHT]))
                if pixels < int(cfg["minimum_component_pixels"]):
                    continue
                z = world_z[component]
                median, mad = float(np.median(z)), float(np.median(np.abs(z - np.median(z))))
                confidence = min(1.0, 0.25 + 0.25 * min(1.0, pixels / 80.0) + 0.25 * min(1.0, span / 30.0) + 0.25 * max(0.0, 1.0 - mad / float(cfg["maximum_component_z_mad_mm"])))
                diagnostics["local_candidates"].append({"height_mm": median, "mad_mm": mad, "pixel_count": pixels, "span_px": span, "confidence": confidence})
        candidates = [v for v in diagnostics["local_candidates"] if v["mad_mm"] <= float(cfg["maximum_component_z_mad_mm"]) and v["span_px"] >= int(cfg["minimum_component_span_px"])]
        if candidates:
            # The largest connected low-spread component is the local-surface
            # authority. Overlapping histogram windows otherwise reward a tiny
            # subset merely because its MAD is smallest.
            maximum_population = max(v["pixel_count"] for v in candidates)
            contact_scale = [
                v for v in candidates
                if v["pixel_count"] >= float(cfg["minimum_relative_component_population"]) * maximum_population
            ]
            # A support is the highest substantial surface immediately below
            # the object, while thin high-Z fragments are rejected by the
            # relative-population gate.
            chosen = max(contact_scale, key=lambda v: (v["height_mm"], v["pixel_count"], -v["mad_mm"]))
            if chosen["confidence"] >= float(cfg["minimum_local_surface_confidence"]):
                return {"support_mm": chosen["height_mm"], "source": "local_gravity_support_surface", "confidence": chosen["confidence"], "fallback_reason": None, "surface_spread_mm": chosen["mad_mm"], "diagnostics": diagnostics}

    object_z = world_z[selected_valid]
    if object_z.size:
        percentile = float(cfg["lower_envelope_percentile"])
        support = float(np.percentile(object_z, percentile))
        spread = float(np.median(np.abs(object_z - np.median(object_z))))
        return {"support_mm": support, "source": "generic_lower_envelope_fallback", "confidence": 0.25, "fallback_reason": "no_qualified_global_or_local_surface", "surface_spread_mm": spread, "diagnostics": diagnostics}
    raise ValueError("generic support requires at least one finite object point")


def group_same_surfaces(objects: list[dict[str, Any]], cfg: dict[str, Any], frame_id: str) -> None:
    """In-place union-find grouping using only support/bottom geometry."""
    n = len(objects)
    parent = list(range(n))
    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    for i in range(n):
        for j in range(i + 1, n):
            ai, aj = objects[i], objects[j]
            if min(ai["generic_support"]["confidence"], aj["generic_support"]["confidence"]) < float(cfg["minimum_member_confidence"]):
                continue
            support_close = abs(ai["generic_support"]["support_mm"] - aj["generic_support"]["support_mm"]) <= float(cfg["maximum_support_delta_mm"])
            bottom_close = abs(float(ai["bbox_xyxy"][3]) - float(aj["bbox_xyxy"][3])) <= float(cfg["maximum_bottom_y_delta_px"])
            if support_close and bottom_close:
                union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    group_number = 0
    for members in groups.values():
        if len(members) < int(cfg["minimum_members"]):
            continue
        group_number += 1
        group_id = f"{frame_id}:surface:{group_number:02d}"
        consensus = float(np.median([objects[i]["generic_support"]["support_mm"] for i in members]))
        for i in members:
            item = objects[i]
            if float(item["final"]["top_mm"]) <= consensus:
                item["same_surface_rejection_reason"] = "consensus_would_make_non_positive_extent"
                continue
            item["same_surface_group_id"] = group_id
            item["consensus_used"] = True
            item["generic_support"]["pre_consensus_support_mm"] = item["generic_support"]["support_mm"]
            item["generic_support"]["support_mm"] = consensus
            item["generic_support"]["source"] = "same_surface_consensus"
            item["final"]["support_mm"] = consensus
            item["final"]["support_source"] = "same_surface_consensus"
            item["final"]["object_height_mm"] = item["final"]["top_mm"] - consensus
