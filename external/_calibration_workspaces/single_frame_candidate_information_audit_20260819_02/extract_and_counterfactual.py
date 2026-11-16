from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from scipy.signal import find_peaks


WORKSPACE = Path(__file__).resolve().parent
ROUND1 = Path(r"C:\ec\_calibration_workspaces\single_frame_candidate_distribution_fusion_20260819_01")
PARAMETERS = {
    "duplicate_height_mm": 14.0,
    "duplicate_axial_mm": 110.0,
    "duplicate_bbox_gap_px": 8.0,
    "continuous_kde_bandwidth_mm": 24.0,
    "mode_minimum_separation_mm": 42.0,
    "huber_delta_mm": 45.0,
    "trim_weight_fraction": 0.10,
    "late_dominant_probability": 0.68,
    "late_dominant_ratio": 1.85,
    "late_maximum_modes": 4,
    "sensitivity_bandwidths_mm": [12.0, 18.0, 24.0, 32.0, 48.0, 64.0],
}


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def bbox_gap(a: list[float], b: list[float]) -> float:
    ax, ay, aw, ah = map(float, a)
    bx, by, bw, bh = map(float, b)
    dx = max(0.0, max(ax, bx) - min(ax + aw, bx + bw))
    dy = max(0.0, max(ay, by) - min(ay + ah, by + bh))
    return math.hypot(dx, dy)


def evidence_weight(candidate: dict[str, Any]) -> float:
    contact = candidate["contact"]
    tier = int(candidate["tier"])
    tier_weight = {3: 1.0, 2: 0.9, 1: 0.55, 0: 0.28, -1: 0.12}.get(tier, 0.12)
    coverage = min(1.0, max(0.0, finite(contact.get("contact_coverage"))))
    footprint = min(1.0, max(0.0, finite(contact.get("footprint_coverage"))))
    direct = 1.12 if contact.get("direct_contact") else 1.0
    line = 0.28 if contact.get("line_like") else 1.0
    residual = contact.get("interior_axial_residual_mm")
    depth = 0.55 if residual is None else math.exp(-abs(finite(residual)) / 900.0)
    spread = max(1.0, candidate["spread_mm"])
    compact = 1.0 / (1.0 + spread / 60.0)
    return max(1e-8, tier_weight * direct * line * depth * compact * (0.35 + 0.40 * coverage + 0.25 * footprint))


def full_candidates(obj: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for observation in obj["observations"]:
        surface = observation["surface"]
        estimate = observation.get("selector_candidate")
        tier = int(estimate["evidence_tier"]) if estimate else -1
        method = str(estimate["method"]) if estimate else "raw_rejected_environment_surface"
        row = {
            "raw_id": f"{obj['object_id']}:observation:{int(observation['observation_index']):03d}",
            "object_id": obj["object_id"],
            "class_name": obj["class_name"],
            "surface_id": surface["surface_id"],
            "source_component_id": surface["surface_id"],
            "origin": surface["origin"],
            "surface_height_mm": float(surface["height_mm"]),
            "candidate_height_mm": float(estimate["support_mm"] if estimate else surface["height_mm"]),
            "height_transform_mm": float((estimate["support_mm"] if estimate else surface["height_mm"]) - surface["height_mm"]),
            "axial_depth_mm": finite(surface.get("median_axial_mm"), float("nan")),
            "surface_bbox_xywh": [finite(value) for value in surface.get("bbox_xywh", [0, 0, 0, 0])],
            "pixel_count": int(surface.get("pixel_count") or 0),
            "spread_mm": max(1.0, finite(estimate.get("uncertainty_mm") if estimate else surface.get("height_mad_mm"), 12.0)),
            "tier": tier,
            "method": method,
            "eligible": estimate is not None,
            "rejection_reasons": observation.get("selector_rejection_reasons") or [],
            "contact": observation["contact"],
        }
        row["raw_evidence_weight"] = evidence_weight(row)
        output.append(row)
    total = sum(item["raw_evidence_weight"] for item in output)
    for item in output:
        item["raw_object_normalized_weight"] = item["raw_evidence_weight"] / max(total, 1e-12)
    return output


def consolidate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    for row in sorted(rows, key=lambda item: (-item["tier"], item["candidate_height_mm"], item["raw_id"])):
        for group in groups:
            anchor = group[0]
            axial_close = (
                not math.isfinite(row["axial_depth_mm"])
                or not math.isfinite(anchor["axial_depth_mm"])
                or abs(row["axial_depth_mm"] - anchor["axial_depth_mm"]) <= PARAMETERS["duplicate_axial_mm"]
            )
            if (
                row["method"] == anchor["method"]
                and abs(row["candidate_height_mm"] - anchor["candidate_height_mm"]) <= PARAMETERS["duplicate_height_mm"]
                and axial_close
                and bbox_gap(row["surface_bbox_xywh"], anchor["surface_bbox_xywh"]) <= PARAMETERS["duplicate_bbox_gap_px"]
            ):
                group.append(row)
                break
        else:
            groups.append([row])
    representatives = []
    for index, group in enumerate(groups):
        raw_weights = np.asarray([item["raw_evidence_weight"] for item in group], dtype=np.float64)
        heights = np.asarray([item["candidate_height_mm"] for item in group], dtype=np.float64)
        representative = max(group, key=lambda item: (item["tier"], item["raw_evidence_weight"], item["pixel_count"]))
        group_weight = float(min(1.0, raw_weights.max() + 0.20 * (raw_weights.sum() - raw_weights.max())))
        representatives.append({
            "provenance_group_id": f"{representative['object_id']}:full_group:{index:03d}",
            "object_id": representative["object_id"],
            "class_name": representative["class_name"],
            "method": representative["method"],
            "tier": representative["tier"],
            "representative_height_mm": float(np.average(heights, weights=raw_weights)),
            "representative_weight_raw": group_weight,
            "raw_ids": [item["raw_id"] for item in group],
            "raw_heights_mm": heights.tolist(),
            "raw_weights": raw_weights.tolist(),
            "height_min_mm": float(heights.min()),
            "height_max_mm": float(heights.max()),
            "height_span_mm": float(heights.max() - heights.min()),
            "height_shift_from_best_member_mm": float(np.average(heights, weights=raw_weights) - representative["candidate_height_mm"]),
            "duplicate_count": len(group),
        })
    total = sum(item["representative_weight_raw"] for item in representatives)
    raw_by_id = {item["raw_id"]: item for item in rows}
    height_preserving = []
    for group in representatives:
        group["normalized_object_weight"] = group["representative_weight_raw"] / max(total, 1e-12)
        member_weights = np.asarray(group["raw_weights"], dtype=np.float64)
        member_weights = member_weights / member_weights.sum()
        for raw_id, within in zip(group["raw_ids"], member_weights):
            raw = raw_by_id[raw_id]
            height_preserving.append({
                "raw_id": raw_id,
                "provenance_group_id": group["provenance_group_id"],
                "object_id": raw["object_id"],
                "class_name": raw["class_name"],
                "height_mm": raw["candidate_height_mm"],
                "surface_height_mm": raw["surface_height_mm"],
                "weight": float(group["normalized_object_weight"] * within),
                "tier": raw["tier"],
                "method": raw["method"],
                "surface_id": raw["surface_id"],
            })
    return representatives, height_preserving


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values, kind="stable")
    return float(values[order][np.searchsorted(np.cumsum(weights[order]), 0.5 * weights.sum(), side="left")])


def huber(values: np.ndarray, weights: np.ndarray) -> float:
    location = weighted_median(values, weights)
    for _ in range(32):
        residual = values - location
        influence = np.minimum(1.0, PARAMETERS["huber_delta_mm"] / np.maximum(np.abs(residual), 1e-12))
        updated = float(np.average(values, weights=weights * influence))
        if abs(updated - location) < 1e-8:
            break
        location = updated
    return location


def trimmed_mean(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values, kind="stable")
    cumulative = np.cumsum(weights[order]) / weights.sum()
    keep = (cumulative >= PARAMETERS["trim_weight_fraction"]) & (cumulative <= 1.0 - PARAMETERS["trim_weight_fraction"])
    if not np.any(keep):
        return float(np.average(values, weights=weights))
    return float(np.average(values[order][keep], weights=weights[order][keep]))


def density(values: np.ndarray, weights: np.ndarray, bandwidth: float) -> tuple[np.ndarray, np.ndarray]:
    low = math.floor((float(values.min()) - 3.0 * bandwidth) / 2.0) * 2.0
    high = math.ceil((float(values.max()) + 3.0 * bandwidth) / 2.0) * 2.0
    grid = np.arange(low, high + 2.0, 2.0, dtype=np.float64)
    result = np.sum(weights[:, None] * np.exp(-0.5 * ((grid[None, :] - values[:, None]) / bandwidth) ** 2), axis=0)
    return grid, result


def modes(values: np.ndarray, weights: np.ndarray, bandwidth: float, relative_floor: float = 0.0) -> tuple[list[dict[str, Any]], np.ndarray, np.ndarray]:
    grid, kde = density(values, weights, bandwidth)
    distance = max(1, int(PARAMETERS["mode_minimum_separation_mm"] / 2.0))
    kwargs = {"distance": distance}
    if relative_floor:
        kwargs["height"] = float(kde.max()) * relative_floor
    peaks, _ = find_peaks(kde, **kwargs)
    if not peaks.size:
        peaks = np.asarray([int(np.argmax(kde))], dtype=np.int64)
    peak_heights = [(int(index), float(kde[index])) for index in peaks]
    peak_heights.sort(key=lambda item: (-item[1], float(grid[item[0]])))
    centres = np.asarray([float(grid[index]) for index, _ in peak_heights], dtype=np.float64)
    assignments = np.argmin(np.abs(values[:, None] - centres[None, :]), axis=1)
    output = []
    for mode_index, (grid_index, height) in enumerate(peak_heights):
        member = assignments == mode_index
        if not np.any(member):
            continue
        mass = float(weights[member].sum())
        centre = float(np.average(values[member], weights=weights[member]))
        spread = float(np.sqrt(np.average((values[member] - centre) ** 2, weights=weights[member])))
        output.append({
            "center_mm": centre,
            "grid_peak_mm": float(grid[grid_index]),
            "density": height,
            "mass": mass,
            "spread_mm": spread,
            "candidate_count": int(np.count_nonzero(member)),
        })
    output.sort(key=lambda item: (-item["mass"], -item["density"], item["center_mm"]))
    total = sum(item["mass"] for item in output)
    for item in output:
        item["probability"] = item["mass"] / max(total, 1e-12)
    return output, grid, kde


def estimators(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = np.asarray([item["height_mm"] for item in rows], dtype=np.float64)
    weights = np.asarray([item["weight"] for item in rows], dtype=np.float64)
    weights = weights / weights.sum()
    grid, kde = density(values, weights, PARAMETERS["continuous_kde_bandwidth_mm"])
    candidate_density = np.interp(values, grid, kde)
    posterior_weights = weights * np.sqrt(np.maximum(candidate_density, 1e-12))
    posterior_weights /= posterior_weights.sum()
    mode_rows, _, _ = modes(values, weights, PARAMETERS["continuous_kde_bandwidth_mm"], 0.0)
    limited = mode_rows[:PARAMETERS["late_maximum_modes"]]
    top = limited[0]
    ratio = float("inf") if len(limited) == 1 else top["probability"] / max(limited[1]["probability"], 1e-12)
    soft = float(np.average([item["center_mm"] for item in limited], weights=[item["probability"] for item in limited]))
    late = top["center_mm"] if (top["probability"] >= PARAMETERS["late_dominant_probability"] or ratio >= PARAMETERS["late_dominant_ratio"]) else soft
    return {
        "unweighted_mean": float(np.mean(values)),
        "unweighted_median": float(np.median(values)),
        "weighted_mean": float(np.average(values, weights=weights)),
        "weighted_median": weighted_median(values, weights),
        "trimmed_weighted_mean": trimmed_mean(values, weights),
        "huber_location": huber(values, weights),
        "robust_posterior_mean": float(np.average(values, weights=posterior_weights)),
        "kde_global_mode": float(grid[int(np.argmax(kde))]),
        "late_multimodal_soft": float(late),
        "modes": limited,
        "density_grid": {"height_mm": grid.tolist(), "density": kde.tolist()},
    }


def main() -> int:
    full = json.loads((WORKSPACE / "full_raw_candidate_capture.json").read_text(encoding="utf-8"))
    round1 = json.loads((ROUND1 / "candidate_distribution_audit.json").read_text(encoding="utf-8"))
    full_map = {
        (scan, obj["frame_id"], obj["object_id"]): obj
        for scan, data in full["scans"].items() for obj in data["objects"]
    }
    compact: dict[str, Any] = {
        "schema_version": "candidate_information_round2_compact.v1",
        "truth_loaded": False,
        "runtime_uses_cross_frame_information": False,
        "eleven_frame_repeatability_is_evaluation_only": True,
        "parameters": PARAMETERS,
        "scans": {},
    }
    provenance_rows = []
    context_rows = []
    hypothesis_rows = []
    outputs: dict[str, Any] = {}
    for scan_label, scan in round1["scans"].items():
        compact["scans"][scan_label] = {"frames": []}
        outputs[scan_label] = []
        for frame in scan["frames"]:
            frame_out = {"frame_id": frame["frame_id"], "sequence": frame["sequence"], "objects": [], "contexts": []}
            object_distributions: dict[int, list[dict[str, Any]]] = {}
            for obj in frame["objects"]:
                key = (scan_label, frame["frame_id"], obj["object_id"])
                captured = full_map[key]
                raw = full_candidates(captured)
                groups, height_preserving = consolidate(raw)
                object_distributions[obj["object_index"]] = height_preserving
                serialized_count = len(obj["raw_candidates"])
                frame_out["objects"].append({
                    "object_index": obj["object_index"],
                    "object_id": obj["object_id"],
                    "class_name": obj["class_name"],
                    "bbox_xyxy": obj["bbox_xyxy"],
                    "region_source": captured.get("region_source"),
                    "distance_mm": captured.get("distance_mm"),
                    "current_support_mm": obj["current_support_mm"],
                    "raw_environment_points": captured["raw_environment_points"],
                    "r0_full_candidates": raw,
                    "r0_full_count": len(raw),
                    "r0s_serialized_candidates": obj["raw_candidates"],
                    "r0s_serialized_count": serialized_count,
                    "r0s_missing_due_diagnostic_limit": max(0, len(raw) - serialized_count),
                    "r1_round1_representatives": obj["provenance_normalized_candidates"],
                    "r1_full_representatives": groups,
                    "p1_height_preserving_distribution": height_preserving,
                    "object_local_estimators": estimators(height_preserving),
                })
                for group in groups:
                    provenance_rows.append({
                        "scan_label": scan_label, "frame_id": frame["frame_id"], "object_id": obj["object_id"],
                        **group,
                    })
            for context in frame["support_contexts"]:
                members = context["member_object_indices"]
                shared = []
                for object_index in members:
                    shared.extend(dict(item) for item in object_distributions[object_index])
                object_count = len({item["object_id"] for item in shared})
                for item in shared:
                    item["weight"] /= max(1, object_count)
                total = sum(item["weight"] for item in shared)
                for item in shared:
                    item["weight"] /= max(total, 1e-12)
                estimates = estimators(shared)
                sensitivity = []
                values = np.asarray([item["height_mm"] for item in shared], dtype=np.float64)
                weights = np.asarray([item["weight"] for item in shared], dtype=np.float64)
                for bandwidth in PARAMETERS["sensitivity_bandwidths_mm"]:
                    unpruned, _, _ = modes(values, weights, bandwidth, 0.0)
                    pruned, _, _ = modes(values, weights, bandwidth, 0.10)
                    sensitivity.append({
                        "bandwidth_mm": bandwidth,
                        "unpruned_mode_count": len(unpruned),
                        "unpruned_top_modes": unpruned[:5],
                        "relative_floor_0_10_mode_count": len(pruned),
                        "relative_floor_0_10_top_modes": pruned[:5],
                    })
                context_row = {
                    "context_id": context["context_id"],
                    "member_object_indices": members,
                    "member_object_ids": context["member_object_ids"],
                    "member_classes": context["member_classes"],
                    "bbox_bottom_y_center": context["bbox_bottom_y_center"],
                    "r2_height_preserving_distribution": shared,
                    "r2_candidate_count": len(shared),
                    "r3_first_round_hypotheses": context["surface_hypotheses"],
                    "r3_first_round_hypothesis_count": len(context["surface_hypotheses"]),
                    "p0_first_round_output": context["method_outputs"]["surface_hypothesis_expectation"],
                    "p1_p2_p3_estimators": estimates,
                    "hypothesis_parameter_sensitivity": sensitivity,
                    "current_object_supports_mm": context["current_object_supports_mm"],
                }
                frame_out["contexts"].append(context_row)
                context_rows.append({
                    "scan_label": scan_label, "frame_id": frame["frame_id"], "sequence": frame["sequence"],
                    "context_id": context["context_id"], "member_object_ids": context["member_object_ids"],
                    "member_classes": context["member_classes"], "bbox_bottom_y_center": context["bbox_bottom_y_center"],
                    "object_count": len(context["member_object_ids"]), "candidate_count": len(shared),
                })
                hypothesis_rows.append({
                    "scan_label": scan_label, "frame_id": frame["frame_id"], "context_id": context["context_id"],
                    "first_round_hypotheses": context["surface_hypotheses"], "continuous_modes": estimates["modes"],
                    "sensitivity": sensitivity,
                })
            compact["scans"][scan_label]["frames"].append(frame_out)
            outputs[scan_label].append({
                "frame_id": frame["frame_id"], "sequence": frame["sequence"],
                "contexts": [
                    {
                        "context_id": item["context_id"], "member_object_ids": item["member_object_ids"],
                        "current_object_supports_mm": item["current_object_supports_mm"],
                        "p0_first_round_hypothesis": item["p0_first_round_output"]["support_mm"],
                        "p1_height_preserving_weighted_median": item["p1_p2_p3_estimators"]["weighted_median"],
                        "p2_continuous_huber": item["p1_p2_p3_estimators"]["huber_location"],
                        "p2_trimmed_weighted_mean": item["p1_p2_p3_estimators"]["trimmed_weighted_mean"],
                        "p2_robust_posterior_mean": item["p1_p2_p3_estimators"]["robust_posterior_mean"],
                        "p3_late_multimodal_soft": item["p1_p2_p3_estimators"]["late_multimodal_soft"],
                        "object_local": {
                            obj["object_id"]: {
                                "weighted_median": obj["object_local_estimators"]["weighted_median"],
                                "huber": obj["object_local_estimators"]["huber_location"],
                                "trimmed_weighted_mean": obj["object_local_estimators"]["trimmed_weighted_mean"],
                                "robust_posterior_mean": obj["object_local_estimators"]["robust_posterior_mean"],
                                "late_multimodal_soft": obj["object_local_estimators"]["late_multimodal_soft"],
                            }
                            for obj in frame_out["objects"] if obj["object_index"] in item["member_object_indices"]
                        },
                    }
                    for item in frame_out["contexts"]
                ],
            })
    write_json(WORKSPACE / "round2_compact_candidate_data.json", compact)
    write_json(WORKSPACE / "counterfactual_outputs_truth_blind.json", outputs)
    provenance_doc = {
        "schema_version": "provenance_consolidation_audit.v1", "truth_loaded": False,
        "group_count": len(provenance_rows), "groups": provenance_rows,
        "groups_with_height_averaging": sum(item["duplicate_count"] > 1 for item in provenance_rows),
        "maximum_group_height_span_mm": max((item["height_span_mm"] for item in provenance_rows), default=0.0),
        "height_preserving_provenance_normalization_tested": True,
    }
    context_doc = {"schema_version": "support_context_formation_audit.v1", "truth_loaded": False, "contexts": context_rows}
    hypothesis_doc = {"schema_version": "surface_hypothesis_construction_audit.v1", "truth_loaded": False, "contexts": hypothesis_rows}
    write_json(WORKSPACE / "PROVENANCE_CONSOLIDATION_AUDIT.json", provenance_doc)
    write_json(WORKSPACE / "SUPPORT_CONTEXT_FORMATION_AUDIT.json", context_doc)
    write_json(WORKSPACE / "SURFACE_HYPOTHESIS_CONSTRUCTION_AUDIT.json", hypothesis_doc)

    (WORKSPACE / "CANDIDATE_INFORMATION_FLOW_GRAPH.md").write_text("""# Candidate Information Flow Graph

`raw environment points → production extractor observations → selector candidates → diagnostic serialization → first-round consolidation → support context → KDE/find_peaks hypotheses → final estimate`

| Stage | File / function | Input → output | Numeric / weight transformation | Information effect |
|---|---|---|---|---|
| R-1 environment points | `environment_surfaces.contact_context` | axial/world-Z pixels near object footprint | finite/positive mask; object dilation exclusion | hard spatial exclusion |
| R0 extractor observations | `extract_environment_surfaces` | context pixels → accepted-plane/local-height connected components | 8 mm histogram, ±12 mm band; median height/MAD/axial | hard pixel/span/MAD gates; `_is_duplicate` removes ≥70% overlap within 8 mm |
| S selector representation | `support_selector._eligible` | one surface + contact row → eligible estimate or rejection | projection can replace surface height; assigns evidence tier | hard rejection and possible height transformation |
| D diagnostic serialization | `select_support` diagnostics | full observations/candidates → JSON diagnostics | rejected and best-effort lists capped by `maximum_diagnostic_candidates=20` | audit-only hard truncation; runtime selection already occurred |
| R1 first-round provenance consolidation | Round-1 `consolidate_object_candidates` | serialized candidates → representatives | close same-generator height/axial/spatial rows merged; representative height is evidence-weighted average; group weight capped | changes height and compresses weights |
| R2 support context | Round-1 `context_clusters` | object representatives → bbox-bottom-Y contexts | ≤30 px bottom-Y linking; scene-spanning bbox isolated | group assignment; no candidate-height deletion within assigned object |
| R3 surface hypotheses | Round-1 `build_hypotheses` | context representatives → KDE peaks | 24 mm bandwidth; 42 mm peak distance; 10% relative density floor; 42 mm assignment radius | peak pruning, unassigned tails, reconstructed centers/probabilities |
| R4 estimate | Round-1 `method_outputs` | hypotheses → weighted expectation | top ≤4 hypothesis centers averaged | final numeric reconstruction |

The Round-2 P1 path preserves every selector-candidate height. Provenance groups normalize only total weight; they do not replace member heights. P2 robust estimators operate on that continuous distribution. P3 interprets modes only at the final step.
""", encoding="utf-8")

    (WORKSPACE / "PROVENANCE_CONSOLIDATION_AUDIT.md").write_text(f"""# Provenance Consolidation Audit

- Full-selector observations: {sum(obj['r0_full_count'] for scan in compact['scans'].values() for frame in scan['frames'] for obj in frame['objects'])}.
- Provenance groups: {len(provenance_rows)}.
- Groups with more than one raw height averaged into a representative: {provenance_doc['groups_with_height_averaging']}.
- Maximum within-group height span: {provenance_doc['maximum_group_height_span_mm']:.3f} mm.

The first-round principle (same physical evidence must not gain votes by emitting more candidates) is sound. The representative implementation mixes two separate operations: weight normalization and height averaging. Round 2 tests `HEIGHT_PRESERVING_PROVENANCE_NORMALIZATION`, where every original height remains and only each group's total vote is capped/normalized.
""", encoding="utf-8")
    (WORKSPACE / "SUPPORT_CONTEXT_FORMATION_AUDIT.md").write_text("""# Support Context Formation Audit

The first-round context layer is audit-only code, not production. It groups non-scene-spanning objects by bbox bottom Y within 30 px and isolates detections covering at least 40% of the image. Every candidate stays with its source object; context formation does not delete that object's candidates. Its risk is relational: it can prevent two same-support objects from reinforcing each other or can let an erroneous object distribution contaminate a peer. Truth-labelled split/mix and bottle/ball results are computed only after the truth-blind output freeze.
""", encoding="utf-8")
    (WORKSPACE / "SURFACE_HYPOTHESIS_CONSTRUCTION_AUDIT.md").write_text("""# Surface Hypothesis Construction Audit

The first-round hypothesis pipeline is a lossy representation: candidate heights are replaced by representative heights, then a 24 mm KDE is discretized with `find_peaks`, 42 mm peak separation, a 10% relative-density floor, and a 42 mm assignment radius. Peak centers are reconstructed from assigned members. Round 2 records unpruned and 10%-pruned modes at 12/18/24/32/48/64 mm bandwidths and compares them with a continuous distribution that delays modal interpretation until the final estimate.
""", encoding="utf-8")

    compact_path = WORKSPACE / "round2_compact_candidate_data.json"
    outputs_path = WORKSPACE / "counterfactual_outputs_truth_blind.json"
    freeze = {
        "schema_version": "round2_truth_blind_freeze.v1",
        "truth_loaded": False,
        "runtime_uses_cross_frame_information": False,
        "parameters": PARAMETERS,
        "code_path": str(Path(__file__).resolve()),
        "code_sha256": sha256_file(Path(__file__).resolve()),
        "full_raw_capture_sha256": sha256_file(WORKSPACE / "full_raw_candidate_capture.json"),
        "round1_audit_sha256": sha256_file(ROUND1 / "candidate_distribution_audit.json"),
        "compact_data_sha256": sha256_file(compact_path),
        "counterfactual_outputs_sha256": sha256_file(outputs_path),
    }
    write_json(WORKSPACE / "TRUTH_BLIND_ROUND2_FREEZE.json", freeze)
    print(json.dumps({
        "scans": len(compact["scans"]),
        "objects": sum(len(frame["objects"]) for scan in compact["scans"].values() for frame in scan["frames"]),
        "contexts": len(context_rows),
        "full_candidates": sum(obj["r0_full_count"] for scan in compact["scans"].values() for frame in scan["frames"] for obj in frame["objects"]),
        "serialized_missing": sum(obj["r0s_missing_due_diagnostic_limit"] for scan in compact["scans"].values() for frame in scan["frames"] for obj in frame["objects"]),
        "provenance_groups": len(provenance_rows),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
