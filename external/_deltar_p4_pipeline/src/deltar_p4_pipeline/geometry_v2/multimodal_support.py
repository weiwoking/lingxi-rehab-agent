"""Frozen M3 continuous support modes with the final W4 peak combiner."""
from __future__ import annotations

import math
import statistics
from typing import Any

import numpy as np
from scipy.signal import find_peaks

from .support_selector import _eligible


CONTEXT_BOTTOM_Y_LINK_PX = 30.0
SCENE_SPANNING_BBOX_AREA_FRACTION = 0.40
DUPLICATE_HEIGHT_MM = 14.0
DUPLICATE_AXIAL_MM = 110.0
DUPLICATE_BBOX_GAP_PX = 8.0
CONTINUOUS_KDE_BANDWIDTH_MM = 24.0
MODE_MINIMUM_SEPARATION_MM = 42.0
LATE_MAXIMUM_MODES = 4


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _bbox_gap(a: list[float], b: list[float]) -> float:
    ax, ay, aw, ah = map(float, a)
    bx, by, bw, bh = map(float, b)
    dx = max(0.0, max(ax, bx) - min(ax + aw, bx + bw))
    dy = max(0.0, max(ay, by) - min(ay + ah, by + bh))
    return math.hypot(dx, dy)


def _evidence_weight(candidate: dict[str, Any]) -> float:
    contact = candidate["contact"]
    tier = int(candidate["tier"])
    tier_weight = {3: 1.0, 2: 0.9, 1: 0.55, 0: 0.28, -1: 0.12}.get(tier, 0.12)
    coverage = min(1.0, max(0.0, _finite(contact.get("contact_coverage"))))
    footprint = min(1.0, max(0.0, _finite(contact.get("footprint_coverage"))))
    direct = 1.12 if contact.get("direct_contact") else 1.0
    line = 0.28 if contact.get("line_like") else 1.0
    residual = contact.get("interior_axial_residual_mm")
    depth = 0.55 if residual is None else math.exp(-abs(_finite(residual)) / 900.0)
    spread = max(1.0, float(candidate["spread_mm"]))
    compact = 1.0 / (1.0 + spread / 60.0)
    return max(
        1e-8,
        tier_weight
        * direct
        * line
        * depth
        * compact
        * (0.35 + 0.40 * coverage + 0.25 * footprint),
    )


def build_object_distribution(
    observations: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    cfg: dict[str, Any],
    object_id: str,
    class_name: str,
) -> list[dict[str, Any]]:
    """Reproduce Round2 full-candidate and height-preserving provenance semantics."""
    raw: list[dict[str, Any]] = []
    for index, (surface, contact) in enumerate(zip(observations, evidence_rows)):
        estimate, _ = _eligible(surface, contact, cfg)
        tier = int(estimate["evidence_tier"]) if estimate else -1
        method = str(estimate["method"]) if estimate else "raw_rejected_environment_surface"
        height = float(estimate["support_mm"] if estimate else surface["height_mm"])
        row = {
            "raw_id": f"{object_id}:observation:{index:03d}",
            "object_id": object_id,
            "class_name": class_name,
            "surface_id": str(surface["surface_id"]),
            "candidate_height_mm": height,
            "axial_depth_mm": _finite(surface.get("median_axial_mm"), float("nan")),
            "surface_bbox_xywh": [
                _finite(value) for value in surface.get("bbox_xywh", [0, 0, 0, 0])
            ],
            "pixel_count": int(surface.get("pixel_count") or 0),
            "spread_mm": max(
                1.0,
                _finite(
                    estimate.get("uncertainty_mm") if estimate else surface.get("height_mad_mm"),
                    12.0,
                ),
            ),
            "tier": tier,
            "method": method,
            "contact": contact,
        }
        row["raw_evidence_weight"] = _evidence_weight(row)
        raw.append(row)

    groups: list[list[dict[str, Any]]] = []
    for row in sorted(raw, key=lambda item: (-item["tier"], item["candidate_height_mm"], item["raw_id"])):
        for group in groups:
            anchor = group[0]
            axial_close = (
                not math.isfinite(row["axial_depth_mm"])
                or not math.isfinite(anchor["axial_depth_mm"])
                or abs(row["axial_depth_mm"] - anchor["axial_depth_mm"]) <= DUPLICATE_AXIAL_MM
            )
            if (
                row["method"] == anchor["method"]
                and abs(row["candidate_height_mm"] - anchor["candidate_height_mm"])
                <= DUPLICATE_HEIGHT_MM
                and axial_close
                and _bbox_gap(row["surface_bbox_xywh"], anchor["surface_bbox_xywh"])
                <= DUPLICATE_BBOX_GAP_PX
            ):
                group.append(row)
                break
        else:
            groups.append([row])

    representatives: list[dict[str, Any]] = []
    for index, group in enumerate(groups):
        weights = np.asarray([item["raw_evidence_weight"] for item in group], dtype=np.float64)
        representative = max(
            group,
            key=lambda item: (item["tier"], item["raw_evidence_weight"], item["pixel_count"]),
        )
        group_weight = float(min(1.0, weights.max() + 0.20 * (weights.sum() - weights.max())))
        representatives.append(
            {
                "provenance_group_id": f"{object_id}:full_group:{index:03d}",
                "representative_weight_raw": group_weight,
                "members": group,
            }
        )
    total = sum(item["representative_weight_raw"] for item in representatives)
    output: list[dict[str, Any]] = []
    for group in representatives:
        group_weight = group["representative_weight_raw"] / max(total, 1e-12)
        members = group["members"]
        member_weights = np.asarray([item["raw_evidence_weight"] for item in members], dtype=np.float64)
        member_weights /= member_weights.sum()
        for raw_row, within in zip(members, member_weights):
            output.append(
                {
                    "raw_id": raw_row["raw_id"],
                    "provenance_group_id": group["provenance_group_id"],
                    "object_id": object_id,
                    "class_name": class_name,
                    "height_mm": float(raw_row["candidate_height_mm"]),
                    "weight": float(group_weight * within),
                    "tier": int(raw_row["tier"]),
                    "method": str(raw_row["method"]),
                    "surface_id": str(raw_row["surface_id"]),
                }
            )
    return output


def _bbox_area_fraction(bbox: list[float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1]) / (640.0 * 360.0)


def context_clusters(objects: list[dict[str, Any]]) -> list[list[int]]:
    """Exact frozen same-frame bbox-bottom context formation."""
    regular: list[tuple[float, int]] = []
    singleton: list[list[int]] = []
    for index, obj in enumerate(objects):
        raw_bbox = obj.get("bbox_xyxy")
        bbox = [_finite(v) for v in raw_bbox] if isinstance(raw_bbox, list) and len(raw_bbox) == 4 else [0.0] * 4
        if _bbox_area_fraction(bbox) >= SCENE_SPANNING_BBOX_AREA_FRACTION:
            singleton.append([index])
        else:
            regular.append((bbox[3], index))
    groups: list[list[int]] = []
    for bottom_y, index in sorted(regular):
        if not groups:
            groups.append([index])
            continue
        centre = statistics.median(
            float(objects[i]["bbox_xyxy"][3]) for i in groups[-1]
        )
        if abs(bottom_y - centre) <= CONTEXT_BOTTOM_Y_LINK_PX:
            groups[-1].append(index)
        else:
            groups.append([index])
    return sorted(
        groups + singleton,
        key=lambda members: statistics.mean(
            float(objects[i]["bbox_xyxy"][3])
            if isinstance(objects[i].get("bbox_xyxy"), list)
            else 0.0
            for i in members
        ),
    )


def retained_modes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        raise ValueError("M3 requires at least one metric candidate")
    values = np.asarray([item["height_mm"] for item in rows], dtype=np.float64)
    weights = np.asarray([item["weight"] for item in rows], dtype=np.float64)
    if not np.isfinite(values).all() or not np.isfinite(weights).all() or np.any(weights <= 0.0):
        raise ValueError("M3 candidate values and weights must be finite and positive")
    weights /= weights.sum()
    bandwidth = CONTINUOUS_KDE_BANDWIDTH_MM
    low = math.floor((float(values.min()) - 3.0 * bandwidth) / 2.0) * 2.0
    high = math.ceil((float(values.max()) + 3.0 * bandwidth) / 2.0) * 2.0
    grid = np.arange(low, high + 2.0, 2.0, dtype=np.float64)
    density = np.sum(
        weights[:, None]
        * np.exp(-0.5 * ((grid[None, :] - values[:, None]) / bandwidth) ** 2),
        axis=0,
    )
    separation = max(1, int(MODE_MINIMUM_SEPARATION_MM / 2.0))
    peaks, _ = find_peaks(density, distance=separation)
    if not peaks.size:
        peaks = np.asarray([int(np.argmax(density))], dtype=np.int64)
    peak_heights = [(int(index), float(density[index])) for index in peaks]
    peak_heights.sort(key=lambda item: (-item[1], float(grid[item[0]])))
    centres = np.asarray([float(grid[index]) for index, _ in peak_heights], dtype=np.float64)
    assignments = np.argmin(np.abs(values[:, None] - centres[None, :]), axis=1)
    modes: list[dict[str, Any]] = []
    for mode_index, (grid_index, height) in enumerate(peak_heights):
        member = assignments == mode_index
        if not np.any(member):
            continue
        mass = float(weights[member].sum())
        center = float(np.average(values[member], weights=weights[member]))
        spread = float(
            np.sqrt(np.average((values[member] - center) ** 2, weights=weights[member]))
        )
        modes.append(
            {
                "center_mm": center,
                "grid_peak_mm": float(grid[grid_index]),
                "density": height,
                "mass": mass,
                "spread_mm": spread,
                "candidate_count": int(np.count_nonzero(member)),
            }
        )
    modes.sort(key=lambda item: (-item["mass"], -item["density"], item["center_mm"]))
    total = sum(item["mass"] for item in modes)
    for item in modes:
        item["probability"] = item["mass"] / max(total, 1e-12)
    return modes[:LATE_MAXIMUM_MODES]


def squared_probability_expectation(modes: list[dict[str, Any]]) -> float:
    if not modes:
        raise ValueError("W4 requires at least one retained mode")
    centres = np.asarray([item["center_mm"] for item in modes], dtype=np.float64)
    probabilities = np.asarray([item["probability"] for item in modes], dtype=np.float64)
    if not np.isfinite(centres).all() or not np.isfinite(probabilities).all():
        raise ValueError("W4 mode centers/probabilities must be finite")
    if np.any(probabilities < 0.0):
        raise ValueError("W4 probabilities must be non-negative")
    weights = probabilities**2
    if float(weights.sum()) <= 0.0:
        raise ValueError("W4 requires positive probability mass")
    return float(np.average(centres, weights=weights))


def combine_context_distributions(
    object_distributions: list[list[dict[str, Any]]],
) -> dict[str, Any]:
    shared: list[dict[str, Any]] = []
    for distribution in object_distributions:
        shared.extend(dict(item) for item in distribution)
    if not shared:
        raise ValueError("M3 context has no metric candidate")
    object_count = len({item["object_id"] for item in shared})
    for item in shared:
        item["weight"] /= max(1, object_count)
    total = sum(item["weight"] for item in shared)
    for item in shared:
        item["weight"] /= max(total, 1e-12)
    modes = retained_modes(shared)
    support = squared_probability_expectation(modes)
    return {
        "support_mm": support,
        "candidate_count": len(shared),
        "independent_object_count": object_count,
        "retained_mode_count": len(modes),
        "retained_modes": modes,
        "combiner": "squared_probability_expectation",
        "formula": "sum(probability^2 * center_mm) / sum(probability^2)",
    }


__all__ = [
    "build_object_distribution",
    "combine_context_distributions",
    "context_clusters",
    "retained_modes",
    "squared_probability_expectation",
]
