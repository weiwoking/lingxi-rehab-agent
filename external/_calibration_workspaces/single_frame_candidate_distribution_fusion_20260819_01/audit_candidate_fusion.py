from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from scipy.signal import find_peaks


WORKSPACE = Path(__file__).resolve().parent
RESULT_ROOT = Path(r"C:\ec\_deltar_scan_service_data\results")
REPLAY_SPECS = {
    "G001": ("scan_20260818_092411_000", "scan_20260818_092411_numericfirst01"),
    "G002": ("scan_20260818_092729_000", "scan_20260818_092729_numericfirst01"),
    "SCAN_161718": ("scan_20260818_161718_000", "scan_20260818_161718_numericfirst01"),
    "SCAN_165011": ("scan_20260818_165011_000", "scan_20260818_165011_numericfirst01"),
    "SCAN_070604": ("scan_20260819_070604_000", "scan_20260819_070604_numericfirst01"),
    "SCAN_085223": ("scan_20260819_085223_000", "scan_20260819_085223_numericfirst01"),
}

# These parameters are evidence semantics, image geometry, or robust-statistics
# scales.  They are frozen without loading any shelf-height truth.
PARAMETERS = {
    "context_bottom_y_link_px": 30.0,
    "scene_spanning_bbox_area_fraction": 0.40,
    "duplicate_height_mm": 14.0,
    "duplicate_axial_mm": 110.0,
    "duplicate_bbox_gap_px": 8.0,
    "candidate_kernel_bandwidth_mm": 24.0,
    "minimum_peak_separation_mm": 42.0,
    "minimum_peak_relative_density": 0.10,
    "hypothesis_assignment_radius_mm": 42.0,
    "huber_delta_mm": 45.0,
    "trim_weight_fraction": 0.10,
    "dominant_posterior_probability": 0.68,
    "dominant_posterior_ratio": 1.85,
    "maximum_hypotheses_in_expectation": 4,
}

METHODS = (
    "current_winner_take_all",
    "provenance_weighted_median",
    "trimmed_huber_location",
    "surface_hypothesis_expectation",
    "mixture_posterior_soft_estimate",
    "hybrid_dominant_or_soft",
)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def public_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): public_json(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, list):
        return [public_json(v) for v in value]
    if isinstance(value, tuple):
        return [public_json(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def bbox_area_fraction(bbox: list[float], image_width: float = 640.0, image_height: float = 360.0) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1]) / (image_width * image_height)


def bbox_gap(a: list[float], b: list[float]) -> float:
    ax1, ay1, aw, ah = map(float, a)
    bx1, by1, bw, bh = map(float, b)
    ax2, ay2, bx2, by2 = ax1 + aw, ay1 + ah, bx1 + bw, by1 + bh
    dx = max(0.0, max(ax1, bx1) - min(ax2, bx2))
    dy = max(0.0, max(ay1, by1) - min(ay2, by2))
    return math.hypot(dx, dy)


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def evidence_weight(row: dict[str, Any]) -> float:
    contact = row["contact"]
    tier = int(row["evidence_tier"])
    tier_weight = {3: 1.00, 2: 0.90, 1: 0.55, 0: 0.28, -1: 0.12}.get(tier, 0.12)
    coverage = min(1.0, max(0.0, finite(contact.get("contact_coverage"))))
    footprint = min(1.0, max(0.0, finite(contact.get("footprint_coverage"))))
    direct = 1.12 if contact.get("direct_contact") else 1.0
    line = 0.28 if contact.get("line_like") else 1.0
    residual = contact.get("interior_axial_residual_mm")
    depth_consistency = 0.55 if residual is None else math.exp(-abs(finite(residual)) / 900.0)
    uncertainty = max(1.0, finite(row.get("surface_spread_mm"), finite(row.get("uncertainty_mm"), 12.0)))
    compactness = 1.0 / (1.0 + uncertainty / 60.0)
    return max(1e-6, tier_weight * direct * line * compactness * depth_consistency * (0.35 + 0.40 * coverage + 0.25 * footprint))


def extract_object_candidates(scan_label: str, frame: dict[str, Any], object_index: int, obj: dict[str, Any]) -> list[dict[str, Any]]:
    generic = obj.get("diagnostics", {}).get("generic_support", {})
    diagnostics = generic.get("diagnostics", {})
    by_surface: dict[str, dict[str, Any]] = {}

    def add(kind: str, item: dict[str, Any]) -> None:
        surface = item.get("surface") or {}
        surface_id = str(surface.get("surface_id") or f"missing:{len(by_surface)}")
        method = str(item.get("method") or ("raw_rejected_environment_surface" if kind == "rejected" else "best_effort_environment_surface"))
        tier = int(item.get("evidence_tier", -1 if kind == "rejected" else 0))
        support_mm = finite(item.get("support_mm"), finite(surface.get("height_mm"), float("nan")))
        if not math.isfinite(support_mm):
            return
        candidate = {
            "scan_label": scan_label,
            "frame_sequence": int(frame["sequence"]),
            "frame_id": str(frame["frame_id"]),
            "object_index": object_index,
            "object_id": str(obj.get("logical_object_id") or obj.get("provenance", {}).get("detection_object_id") or f"object:{object_index}"),
            "class_name": str(obj.get("class_name") or "unknown"),
            "object_bbox_xyxy": [finite(v) for v in obj.get("bbox_xyxy", [0, 0, 0, 0])],
            "source_region_id": str(obj.get("provenance", {}).get("segmentation_object_id") or obj.get("provenance", {}).get("detection_object_id") or f"object:{object_index}"),
            "candidate_generator": method,
            "candidate_kind": kind,
            "surface_id": surface_id,
            "component_id": surface_id,
            "support_mm": support_mm,
            "surface_height_mm": finite(surface.get("height_mm"), support_mm),
            "axial_depth_mm": finite(surface.get("median_axial_mm"), float("nan")),
            "surface_bbox_xywh": [finite(v) for v in surface.get("bbox_xywh", [0, 0, 0, 0])],
            "surface_origin": str(surface.get("origin") or "unknown"),
            "pixel_count": int(surface.get("pixel_count") or 0),
            "surface_spread_mm": max(1.0, finite(item.get("uncertainty_mm"), finite(surface.get("height_mad_mm"), 12.0))),
            "evidence_tier": tier,
            "rejection_reasons": list(item.get("rejection_reasons") or []),
            "contact": public_json(item.get("contact") or {}),
            "selected_by_current": surface_id == str(diagnostics.get("selected_surface_id")),
            "current_support_mm": finite(obj.get("geometry", {}).get("support_height_mm"), float("nan")),
            "current_confidence": finite(obj.get("geometry_confidence")),
            "current_status": str(obj.get("geometry_status") or "unknown"),
            "current_method": str(generic.get("source") or diagnostics.get("selected_method") or "unknown"),
        }
        candidate["raw_evidence_weight"] = evidence_weight(candidate)
        old = by_surface.get(surface_id)
        if old is None or (candidate["evidence_tier"], candidate["raw_evidence_weight"]) > (old["evidence_tier"], old["raw_evidence_weight"]):
            by_surface[surface_id] = candidate

    for item in diagnostics.get("rejected_candidates") or []:
        add("rejected", item)
    for item in diagnostics.get("best_effort_candidates") or []:
        add("best_effort", item)
    for item in diagnostics.get("eligible_candidates") or []:
        add("eligible", item)
    return sorted(by_surface.values(), key=lambda row: (row["support_mm"], row["surface_id"]))


def consolidate_object_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    for row in sorted(rows, key=lambda item: (-item["evidence_tier"], item["support_mm"], item["surface_id"])):
        placed = False
        for group in groups:
            anchor = group[0]
            axial_close = (
                not math.isfinite(row["axial_depth_mm"])
                or not math.isfinite(anchor["axial_depth_mm"])
                or abs(row["axial_depth_mm"] - anchor["axial_depth_mm"]) <= PARAMETERS["duplicate_axial_mm"]
            )
            if (
                abs(row["support_mm"] - anchor["support_mm"]) <= PARAMETERS["duplicate_height_mm"]
                and axial_close
                and bbox_gap(row["surface_bbox_xywh"], anchor["surface_bbox_xywh"]) <= PARAMETERS["duplicate_bbox_gap_px"]
                and row["candidate_generator"] == anchor["candidate_generator"]
            ):
                group.append(row)
                placed = True
                break
        if not placed:
            groups.append([row])

    output: list[dict[str, Any]] = []
    for index, group in enumerate(groups):
        weights = np.asarray([row["raw_evidence_weight"] for row in group], dtype=np.float64)
        heights = np.asarray([row["support_mm"] for row in group], dtype=np.float64)
        representative = max(group, key=lambda row: (row["evidence_tier"], row["raw_evidence_weight"], row["pixel_count"]))
        merged = dict(representative)
        merged.update({
            "provenance_group_id": f"{representative['object_id']}:source_group:{index:03d}",
            "support_mm": float(np.average(heights, weights=weights)),
            "raw_evidence_weight": float(min(1.0, weights.max() + 0.20 * (weights.sum() - weights.max()))),
            "duplicate_candidate_count": len(group),
            "duplicate_surface_ids": [row["surface_id"] for row in group],
            "independent_component_count": 1,
        })
        output.append(merged)

    total = sum(row["raw_evidence_weight"] for row in output)
    for row in output:
        row["normalized_object_weight"] = row["raw_evidence_weight"] / max(total, 1e-12)
    return sorted(output, key=lambda row: (row["support_mm"], row["provenance_group_id"]))


def context_clusters(objects: list[dict[str, Any]]) -> list[list[int]]:
    # Scene-spanning detections are not silently discarded: they receive their
    # own context because their footprint describes an enclosing scene region,
    # not a portable-object contact row.
    regular: list[tuple[float, int]] = []
    singleton: list[list[int]] = []
    for index, obj in enumerate(objects):
        bbox = [finite(v) for v in obj.get("bbox_xyxy", [0, 0, 0, 0])]
        if bbox_area_fraction(bbox) >= PARAMETERS["scene_spanning_bbox_area_fraction"]:
            singleton.append([index])
        else:
            regular.append((bbox[3], index))
    groups: list[list[int]] = []
    for bottom_y, index in sorted(regular):
        if not groups:
            groups.append([index])
            continue
        centre = statistics.median(float(objects[i]["bbox_xyxy"][3]) for i in groups[-1])
        if abs(bottom_y - centre) <= PARAMETERS["context_bottom_y_link_px"]:
            groups[-1].append(index)
        else:
            groups.append([index])
    return sorted(groups + singleton, key=lambda members: statistics.mean(float(objects[i]["bbox_xyxy"][3]) for i in members))


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values, kind="stable")
    values, weights = values[order], weights[order]
    return float(values[np.searchsorted(np.cumsum(weights), 0.5 * weights.sum(), side="left")])


def trimmed_rows(rows: list[dict[str, Any]], fraction: float) -> list[dict[str, Any]]:
    if len(rows) <= 2 or fraction <= 0.0:
        return rows
    values = np.asarray([row["support_mm"] for row in rows], dtype=np.float64)
    weights = np.asarray([row["normalized_weight"] for row in rows], dtype=np.float64)
    order = np.argsort(values, kind="stable")
    cumulative = np.cumsum(weights[order]) / weights.sum()
    keep = (cumulative >= fraction) & (cumulative <= 1.0 - fraction)
    selected = [rows[int(order[i])] for i in np.flatnonzero(keep)]
    return selected or rows


def huber_location(rows: list[dict[str, Any]]) -> float:
    values = np.asarray([row["support_mm"] for row in rows], dtype=np.float64)
    base = np.asarray([row["normalized_weight"] for row in rows], dtype=np.float64)
    location = weighted_median(values, base)
    delta = PARAMETERS["huber_delta_mm"]
    for _ in range(24):
        residual = values - location
        influence = np.minimum(1.0, delta / np.maximum(np.abs(residual), 1e-12))
        updated = float(np.average(values, weights=base * influence))
        if abs(updated - location) < 1e-7:
            break
        location = updated
    return location


def build_hypotheses(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    values = np.asarray([row["support_mm"] for row in rows], dtype=np.float64)
    weights = np.asarray([row["normalized_weight"] for row in rows], dtype=np.float64)
    bandwidth = PARAMETERS["candidate_kernel_bandwidth_mm"]
    low = math.floor((float(values.min()) - 3.0 * bandwidth) / 2.0) * 2.0
    high = math.ceil((float(values.max()) + 3.0 * bandwidth) / 2.0) * 2.0
    grid = np.arange(low, high + 2.0, 2.0, dtype=np.float64)
    density = np.sum(weights[:, None] * np.exp(-0.5 * ((grid[None, :] - values[:, None]) / bandwidth) ** 2), axis=0)
    distance = max(1, int(PARAMETERS["minimum_peak_separation_mm"] / 2.0))
    peaks, props = find_peaks(density, distance=distance, height=float(density.max()) * PARAMETERS["minimum_peak_relative_density"])
    if not peaks.size:
        peaks = np.asarray([int(np.argmax(density))], dtype=np.int64)
    peak_centres = [float(grid[index]) for index in peaks]

    assigned: dict[int, list[int]] = defaultdict(list)
    for row_index, value in enumerate(values):
        distances = np.abs(np.asarray(peak_centres) - value)
        nearest = int(np.argmin(distances))
        if distances[nearest] <= PARAMETERS["hypothesis_assignment_radius_mm"]:
            assigned[nearest].append(row_index)
    hypotheses: list[dict[str, Any]] = []
    for peak_index, members in assigned.items():
        member_values = values[members]
        member_weights = weights[members]
        centre = float(np.average(member_values, weights=member_weights))
        spread = float(np.sqrt(np.average((member_values - centre) ** 2, weights=member_weights)))
        object_ids = sorted({rows[index]["object_id"] for index in members})
        component_ids = sorted({rows[index]["provenance_group_id"] for index in members})
        evidence = float(member_weights.sum())
        independence_boost = 1.0 + 0.35 * max(0, len(object_ids) - 1)
        compactness = 1.0 / (1.0 + spread / max(1.0, bandwidth))
        posterior_score = evidence * independence_boost * compactness
        hypotheses.append({
            "center_mm": centre,
            "spread_mm": spread,
            "normalized_evidence": evidence,
            "posterior_score": posterior_score,
            "independent_object_count": len(object_ids),
            "independent_component_count": len(component_ids),
            "object_ids": object_ids,
            "component_ids": component_ids,
            "candidate_count": len(members),
            "candidate_indices": members,
        })
    hypotheses.sort(key=lambda item: (-item["posterior_score"], item["center_mm"]))
    total = sum(item["posterior_score"] for item in hypotheses)
    for item in hypotheses:
        item["posterior_probability"] = item["posterior_score"] / max(total, 1e-12)
    return hypotheses


def distribution_confidence(hypotheses: list[dict[str, Any]]) -> tuple[float, str | None]:
    if not hypotheses:
        return 0.0, "no_metric_candidate_distribution"
    probabilities = np.asarray([item["posterior_probability"] for item in hypotheses], dtype=np.float64)
    entropy = -float(np.sum(probabilities * np.log(np.maximum(probabilities, 1e-12))))
    normalized_entropy = entropy / math.log(len(probabilities)) if len(probabilities) > 1 else 0.0
    top = hypotheses[0]
    confidence = 0.25 + 0.45 * top["posterior_probability"] + 0.15 * min(1.0, top["independent_object_count"] / 2.0) + 0.15 * (1.0 - min(1.0, top["spread_mm"] / 60.0))
    confidence *= 1.0 - 0.45 * normalized_entropy
    degraded = None
    if len(hypotheses) > 1 and (top["posterior_probability"] < 0.72 or abs(hypotheses[0]["center_mm"] - hypotheses[1]["center_mm"]) > 45.0):
        degraded = "ambiguous_support:multimodal_candidate_distribution"
    if top["spread_mm"] > 45.0:
        degraded = "ambiguous_support:wide_candidate_distribution"
    return float(max(0.05, min(0.92, confidence))), degraded


def method_outputs(rows: list[dict[str, Any]], hypotheses: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not rows:
        return {}
    values = np.asarray([row["support_mm"] for row in rows], dtype=np.float64)
    weights = np.asarray([row["normalized_weight"] for row in rows], dtype=np.float64)
    trimmed = trimmed_rows(rows, PARAMETERS["trim_weight_fraction"])
    h_values = np.asarray([item["center_mm"] for item in hypotheses], dtype=np.float64)
    h_evidence = np.asarray([item["normalized_evidence"] for item in hypotheses], dtype=np.float64)
    h_posterior = np.asarray([item["posterior_probability"] for item in hypotheses], dtype=np.float64)
    limit = min(PARAMETERS["maximum_hypotheses_in_expectation"], len(hypotheses))
    h_values_limited = h_values[:limit]
    h_evidence_limited = h_evidence[:limit]
    h_posterior_limited = h_posterior[:limit]
    dominant = hypotheses[0]
    ratio = float("inf") if len(hypotheses) == 1 else dominant["posterior_probability"] / max(hypotheses[1]["posterior_probability"], 1e-12)
    soft_value = float(np.average(h_values_limited, weights=h_posterior_limited))
    hybrid_value = dominant["center_mm"] if (
        dominant["posterior_probability"] >= PARAMETERS["dominant_posterior_probability"]
        or ratio >= PARAMETERS["dominant_posterior_ratio"]
    ) else soft_value
    confidence, degradation = distribution_confidence(hypotheses)
    return {
        key: {
            **value,
            "confidence": confidence,
            "geometry_status": "degraded" if degradation else "ok",
            "degradation_reason": degradation,
        }
        for key, value in {
            "provenance_weighted_median": {"support_mm": weighted_median(values, weights)},
            "trimmed_huber_location": {"support_mm": huber_location(trimmed)},
            "surface_hypothesis_expectation": {"support_mm": float(np.average(h_values_limited, weights=h_evidence_limited))},
            "mixture_posterior_soft_estimate": {"support_mm": soft_value},
            "hybrid_dominant_or_soft": {"support_mm": float(hybrid_value)},
        }.items()
    }


def summarize(values: Iterable[float]) -> dict[str, Any]:
    array = np.asarray([float(value) for value in values if math.isfinite(float(value))], dtype=np.float64)
    if not array.size:
        return {"n": 0}
    median = float(np.median(array))
    mad = float(np.median(np.abs(array - median)))
    return {
        "n": int(array.size),
        "mean_mm": float(np.mean(array)),
        "median_mm": median,
        "std_mm": float(np.std(array)),
        "mad_mm": mad,
        "p05_mm": float(np.percentile(array, 5)),
        "p95_mm": float(np.percentile(array, 95)),
        "min_mm": float(np.min(array)),
        "max_mm": float(np.max(array)),
        "span_mm": float(np.max(array) - np.min(array)),
        "repeatability_outlier_gt50_from_scan_median": int(np.count_nonzero(np.abs(array - median) > 50.0)),
        "repeatability_outlier_gt75_from_scan_median": int(np.count_nonzero(np.abs(array - median) > 75.0)),
        "repeatability_outlier_gt100_from_scan_median": int(np.count_nonzero(np.abs(array - median) > 100.0)),
    }


def build_audit() -> dict[str, Any]:
    audit: dict[str, Any] = {
        "schema_version": "single_frame_candidate_distribution_audit.v1",
        "runtime_uses_cross_frame_information": False,
        "eleven_frame_repeatability_is_evaluation_only": True,
        "truth_loaded_during_method_construction": False,
        "parameters": PARAMETERS,
        "inputs": {},
        "scans": {},
    }
    for scan_label, (source_scan_id, replay_id) in REPLAY_SPECS.items():
        artifact = RESULT_ROOT / replay_id / "stage8b_all_object_geometry.v1.json"
        document = json.loads(artifact.read_text(encoding="utf-8"))
        audit["inputs"][scan_label] = {
            "source_scan_id": source_scan_id,
            "replay_id": replay_id,
            "artifact_path": str(artifact),
            "artifact_sha256": sha256_file(artifact),
        }
        scan_output = {"source_scan_id": source_scan_id, "replay_id": replay_id, "frames": []}
        for frame in sorted(document["frames"], key=lambda item: int(item["sequence"])):
            objects = frame["objects"]
            candidates_by_object: dict[int, list[dict[str, Any]]] = {}
            normalized_by_object: dict[int, list[dict[str, Any]]] = {}
            for object_index, obj in enumerate(objects):
                raw = extract_object_candidates(scan_label, frame, object_index, obj)
                candidates_by_object[object_index] = raw
                normalized_by_object[object_index] = consolidate_object_candidates(raw)
            frame_output = {
                "sequence": int(frame["sequence"]),
                "frame_id": str(frame["frame_id"]),
                "objects": [],
                "support_contexts": [],
            }
            for object_index, obj in enumerate(objects):
                frame_output["objects"].append({
                    "object_index": object_index,
                    "object_id": str(obj.get("logical_object_id") or obj.get("provenance", {}).get("detection_object_id") or f"object:{object_index}"),
                    "class_name": str(obj.get("class_name") or "unknown"),
                    "bbox_xyxy": [finite(v) for v in obj.get("bbox_xyxy", [0, 0, 0, 0])],
                    "current_support_mm": finite(obj.get("geometry", {}).get("support_height_mm"), float("nan")),
                    "current_confidence": finite(obj.get("geometry_confidence")),
                    "current_status": str(obj.get("geometry_status") or "unknown"),
                    "current_method": str(obj.get("diagnostics", {}).get("generic_support", {}).get("source") or "unknown"),
                    "raw_candidates": candidates_by_object[object_index],
                    "provenance_normalized_candidates": normalized_by_object[object_index],
                })
            for context_index, members in enumerate(context_clusters(objects)):
                rows: list[dict[str, Any]] = []
                for object_index in members:
                    rows.extend(normalized_by_object[object_index])
                if not rows:
                    continue
                # Every object contributes one total vote; a component-rich
                # object cannot dominate a second independent object.
                object_count = len({row["object_id"] for row in rows})
                for row in rows:
                    row["normalized_weight"] = row["normalized_object_weight"] / max(1, object_count)
                hypotheses = build_hypotheses(rows)
                methods = method_outputs(rows, hypotheses)
                context_id = f"{scan_label}:frame:{int(frame['sequence']):02d}:context:{context_index:02d}"
                current_values = [finite(objects[index].get("geometry", {}).get("support_height_mm"), float("nan")) for index in members]
                frame_output["support_contexts"].append({
                    "context_id": context_id,
                    "member_object_indices": members,
                    "member_object_ids": [frame_output["objects"][index]["object_id"] for index in members],
                    "member_classes": [frame_output["objects"][index]["class_name"] for index in members],
                    "bbox_bottom_y_center": float(statistics.median(objects[index]["bbox_xyxy"][3] for index in members)),
                    "raw_candidate_count": sum(len(candidates_by_object[index]) for index in members),
                    "provenance_group_count": len(rows),
                    "independent_object_count": object_count,
                    "candidate_distribution": rows,
                    "surface_hypotheses": hypotheses,
                    "current_object_supports_mm": current_values,
                    "current_context_median_mm": float(np.nanmedian(np.asarray(current_values, dtype=np.float64))),
                    "method_outputs": methods,
                })
            scan_output["frames"].append(frame_output)
        audit["scans"][scan_label] = scan_output
    return public_json(audit)


def comparison(audit: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": "truth_blind_offline_fusion_comparison.v1",
        "truth_used": False,
        "metric_definition": "object-level outputs; same-frame context fusion copied to each context member; 11 frames only summarized after all independent outputs exist",
        "scans": {},
        "aggregate": {},
    }
    aggregate_values: dict[str, list[float]] = defaultdict(list)
    for scan_label, scan in audit["scans"].items():
        per_method: dict[str, list[float]] = defaultdict(list)
        per_frame_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for frame in scan["frames"]:
            for context in frame["support_contexts"]:
                members = context["member_object_indices"]
                for object_index, current_value in zip(members, context["current_object_supports_mm"]):
                    per_method["current_winner_take_all"].append(current_value)
                    per_frame_method["current_winner_take_all"].append({
                        "frame_sequence": frame["sequence"], "object_index": object_index, "context_id": context["context_id"], "support_mm": current_value,
                    })
                for method, output in context["method_outputs"].items():
                    value = output["support_mm"]
                    for object_index in members:
                        per_method[method].append(value)
                        per_frame_method[method].append({
                            "frame_sequence": frame["sequence"], "object_index": object_index, "context_id": context["context_id"], "support_mm": value,
                        })
        result["scans"][scan_label] = {
            method: {"summary": summarize(values), "outputs": per_frame_method[method]}
            for method, values in per_method.items()
        }
        for method, values in per_method.items():
            aggregate_values[method].extend(values)
    result["aggregate"] = {method: summarize(values) for method, values in aggregate_values.items()}
    # Selection is based only on repeatability objective ranks, never truth.
    objectives: dict[str, dict[str, float]] = {}
    for method in METHODS:
        scan_metrics = [result["scans"][scan][method]["summary"] for scan in result["scans"]]
        objectives[method] = {
            "mean_scan_mad_mm": float(np.mean([m["mad_mm"] for m in scan_metrics])),
            "mean_scan_std_mm": float(np.mean([m["std_mm"] for m in scan_metrics])),
            "mean_scan_span_mm": float(np.mean([m["span_mm"] for m in scan_metrics])),
            "repeatability_outlier_gt100_count": int(sum(m["repeatability_outlier_gt100_from_scan_median"] for m in scan_metrics)),
        }
    candidates = [method for method in METHODS if method != "current_winner_take_all"]
    selected = min(candidates, key=lambda method: (
        objectives[method]["repeatability_outlier_gt100_count"],
        objectives[method]["mean_scan_mad_mm"],
        objectives[method]["mean_scan_span_mm"],
        METHODS.index(method),
    ))
    result["truth_blind_objectives"] = objectives
    result["selected_method"] = selected
    return public_json(result)


def method_per_frame_outputs(audit: dict[str, Any], selected_method: str) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    for scan_label, scan in audit["scans"].items():
        outputs[scan_label] = []
        for frame in scan["frames"]:
            frame_rows = []
            for context in frame["support_contexts"]:
                output = context["method_outputs"][selected_method]
                frame_rows.append({
                    "frame_sequence": frame["sequence"],
                    "context_id": context["context_id"],
                    "member_object_indices": context["member_object_indices"],
                    "support_mm": output["support_mm"],
                    "confidence": output["confidence"],
                    "geometry_status": output["geometry_status"],
                    "degradation_reason": output["degradation_reason"],
                    "hypotheses": context["surface_hypotheses"],
                })
            outputs[scan_label].append(frame_rows)
    return public_json(outputs)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(public_json(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def write_markdown(audit: dict[str, Any], compare: dict[str, Any]) -> None:
    lines = [
        "# Candidate Distribution Audit (truth blind)", "",
        "`RUNTIME_USES_CROSS_FRAME_INFORMATION = false`  ",
        "`ELEVEN_FRAME_REPEATABILITY_IS_EVALUATION_ONLY = true`  ",
        "`TRUTH_LOADED_DURING_METHOD_CONSTRUCTION = false`", "",
        "Every method consumes one frame only. The eleven results are collected only after every frame output has been calculated independently. Candidate provenance is consolidated within each object/source component, then each object receives one total vote inside an image-spatial support context.", "",
        "## Inputs", "",
        "| Scan | Replay | Geometry artifact SHA-256 | Frames |", "|---|---|---|---:|",
    ]
    for scan_label, item in audit["inputs"].items():
        lines.append(f"| {scan_label} | `{item['replay_id']}` | `{item['artifact_sha256']}` | {len(audit['scans'][scan_label]['frames'])} |")
    lines += ["", "## Truth-blind method comparison", "", "| Method | mean scan MAD | mean scan std | mean scan span | >100 mm repeatability outliers |", "|---|---:|---:|---:|---:|"]
    for method in METHODS:
        metrics = compare["truth_blind_objectives"][method]
        lines.append(f"| `{method}` | {metrics['mean_scan_mad_mm']:.3f} | {metrics['mean_scan_std_mm']:.3f} | {metrics['mean_scan_span_mm']:.3f} | {metrics['repeatability_outlier_gt100_count']} |")
    lines += ["", f"Truth-blind selected method: `{compare['selected_method']}`.", "", "Selection order is frozen: minimize >100 mm repeatability outliers, then mean scan MAD, then mean scan span. Shelf truth is not present in this script or comparison file.", "", "## Extraction counts", "", "| Scan | Frames | Objects | Raw candidates | Provenance groups | Hypotheses |", "|---|---:|---:|---:|---:|---:|"]
    for scan_label, scan in audit["scans"].items():
        frames = scan["frames"]
        lines.append(f"| {scan_label} | {len(frames)} | {sum(len(f['objects']) for f in frames)} | {sum(c['raw_candidate_count'] for f in frames for c in f['support_contexts'])} | {sum(c['provenance_group_count'] for f in frames for c in f['support_contexts'])} | {sum(len(c['surface_hypotheses']) for f in frames for c in f['support_contexts'])} |")
    lines += ["", "## Candidate semantics", "", "- `raw_candidates` is the union of current eligible, rejected, and best-effort environment-surface observations, deduplicated only by the exact recorded surface ID.", "- `provenance_normalized_candidates` consolidates close duplicates only within one object, generator, axial neighborhood, and spatial component neighborhood.", "- Per-object weights sum to one; candidate-generating abundance cannot create extra votes.", "- Support contexts are formed from same-frame bbox-bottom image geometry. Scene-spanning detections are isolated. No 725/1065 constant or target-name rule participates.", "- Narrow/unimodal distributions can be normal confidence. Wide or competing modes remain numeric but become degraded/low confidence.", ""]
    (WORKSPACE / "CANDIDATE_DISTRIBUTION_AUDIT.md").write_text("\n".join(lines), encoding="utf-8")


def svg_distribution(context: dict[str, Any], width: int = 900, height: int = 240) -> str:
    rows = context["candidate_distribution"]
    hypotheses = context["surface_hypotheses"]
    values = [row["support_mm"] for row in rows]
    if not values:
        return "<p>No candidates.</p>"
    low, high = min(values), max(values)
    if high - low < 1.0:
        low, high = low - 1.0, high + 1.0
    pad_x, pad_y = 52, 28
    def x(value: float) -> float:
        return pad_x + (value - low) / (high - low) * (width - 2 * pad_x)
    marks = []
    for row in rows:
        color = "#2d6cdf" if row["evidence_tier"] >= 1 else ("#8f6ad8" if row["evidence_tier"] == 0 else "#a0a7b4")
        radius = 3.0 + 6.0 * math.sqrt(max(0.0, row["normalized_weight"]))
        marks.append(f'<circle cx="{x(row["support_mm"]):.1f}" cy="{height-54:.1f}" r="{radius:.1f}" fill="{color}"><title>{html.escape(row["object_id"])} {row["support_mm"]:.1f} mm; w={row["normalized_weight"]:.3f}</title></circle>')
    for index, hypothesis in enumerate(hypotheses[:6]):
        hx = x(hypothesis["center_mm"])
        marks.append(f'<line x1="{hx:.1f}" y1="28" x2="{hx:.1f}" y2="{height-42}" stroke="#d94f4f" stroke-width="{max(1.0, 5.0*hypothesis["posterior_probability"]):.1f}" opacity="0.8"/>')
        marks.append(f'<text x="{hx:.1f}" y="{22+15*(index%2):.1f}" text-anchor="middle" font-size="11">H{index+1} {hypothesis["center_mm"]:.1f} ({hypothesis["posterior_probability"]:.2f})</text>')
    marks.append(f'<line x1="{pad_x}" y1="{height-38}" x2="{width-pad_x}" y2="{height-38}" stroke="#555"/>')
    for value in np.linspace(low, high, 6):
        marks.append(f'<text x="{x(float(value)):.1f}" y="{height-14}" text-anchor="middle" font-size="11">{value:.0f}</text>')
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Candidate support height distribution">{"".join(marks)}</svg>'


def write_visualizations(audit: dict[str, Any], compare: dict[str, Any]) -> None:
    visual_root = WORKSPACE / "visualization"
    visual_root.mkdir(exist_ok=True)
    index_rows = []
    selected = compare["selected_method"]
    for scan_label, scan in audit["scans"].items():
        sections = [f"<h1>{html.escape(scan_label)} — single-frame candidate distributions</h1>"]
        for frame in scan["frames"]:
            sections.append(f"<section id='frame-{frame['sequence']}'><h2>Frame {frame['sequence']}</h2>")
            for context in frame["support_contexts"]:
                chosen = context["method_outputs"][selected]
                sections.append(f"<h3>{html.escape(context['context_id'])}</h3><p>objects={html.escape(', '.join(context['member_classes']))}; current={html.escape(', '.join(f'{v:.1f}' for v in context['current_object_supports_mm']))} mm; proposed={chosen['support_mm']:.1f} mm; confidence={chosen['confidence']:.3f}; status={chosen['geometry_status']}</p>")
                sections.append(svg_distribution(context))
                sections.append("<table><thead><tr><th>peak</th><th>center mm</th><th>spread</th><th>posterior</th><th>objects</th><th>components</th></tr></thead><tbody>")
                for idx, hyp in enumerate(context["surface_hypotheses"][:8], 1):
                    sections.append(f"<tr><td>H{idx}</td><td>{hyp['center_mm']:.2f}</td><td>{hyp['spread_mm']:.2f}</td><td>{hyp['posterior_probability']:.3f}</td><td>{hyp['independent_object_count']}</td><td>{hyp['independent_component_count']}</td></tr>")
                sections.append("</tbody></table>")
            sections.append("</section>")
        style = "body{font-family:Segoe UI,Arial,sans-serif;max-width:1100px;margin:auto;padding:20px;color:#20242a}section{border-top:1px solid #ccd2da;padding-top:12px}svg{width:100%;height:auto;background:#fafbfc;border:1px solid #d8dde5}table{border-collapse:collapse;width:100%;margin-bottom:24px}th,td{border-bottom:1px solid #ddd;text-align:right;padding:5px}th:first-child,td:first-child{text-align:left}p{font-size:14px}"
        page = "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>" + html.escape(scan_label) + " candidate distribution</title><style>" + style + "</style></head><body>" + "".join(sections) + "</body></html>"
        page_path = visual_root / f"{scan_label.lower()}-candidate-distributions.html"
        page_path.write_text(page, encoding="utf-8")
        index_rows.append(f"<li><a href='{page_path.name}'>{html.escape(scan_label)}</a></li>")
    index_page = "<!doctype html><html><head><meta charset='utf-8'><title>Candidate Distribution Audit</title></head><body><h1>Candidate Distribution Audit</h1><p>Truth-blind selected method: <code>" + html.escape(selected) + "</code></p><ul>" + "".join(index_rows) + "</ul></body></html>"
    (visual_root / "index.html").write_text(index_page, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-visualization", action="store_true")
    args = parser.parse_args()
    audit = build_audit()
    compare = comparison(audit)
    write_json(WORKSPACE / "candidate_distribution_audit.json", audit)
    write_json(WORKSPACE / "offline_fusion_method_comparison_truth_blind.json", compare)
    write_markdown(audit, compare)
    if not args.no_visualization:
        write_visualizations(audit, compare)
    selected_method = compare["selected_method"]
    outputs = method_per_frame_outputs(audit, selected_method)
    output_path = WORKSPACE / "frozen_truth_blind_per_frame_outputs.json"
    write_json(output_path, outputs)
    manifest = {
        "schema_version": "fusion_method_freeze.v1",
        "truth_loaded": False,
        "runtime_uses_cross_frame_information": False,
        "eleven_frame_repeatability_is_evaluation_only": True,
        "method": selected_method,
        "parameters": PARAMETERS,
        "selection_objective": "truth-blind repeatability: >100 mm outliers, mean scan MAD, mean scan span",
        "code_path": str(Path(__file__).resolve()),
        "code_sha256": sha256_file(Path(__file__).resolve()),
        "input_artifact_hashes": {key: value["artifact_sha256"] for key, value in audit["inputs"].items()},
        "candidate_distribution_audit_sha256": sha256_bytes(canonical_json(audit)),
        "method_comparison_sha256": sha256_bytes(canonical_json(compare)),
        "per_frame_outputs_path": str(output_path),
        "per_frame_outputs_sha256": sha256_bytes(canonical_json(outputs)),
    }
    write_json(WORKSPACE / "FUSION_METHOD_FREEZE.json", manifest)
    print(json.dumps({"selected_method": selected_method, "workspace": str(WORKSPACE), "freeze": manifest}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
