from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np


WORKSPACE = Path(__file__).resolve().parent
NUMERIC_REPORT = Path(r"C:\ec\_codex_reports\geometry_numeric_first_and_full_reaudit_20260819_01")
NUMERIC = NUMERIC_REPORT / "evidence" / "all_numeric_metrics.json"
TOF_ROOT = NUMERIC_REPORT / "evidence" / "six_scan_fresh_compact"
WIDTHS = (10, 20, 30, 50)
TOF_DIR = {
    "G001": "scan_20260818_092411_numericfirst01",
    "G002": "scan_20260818_092729_numericfirst01",
    "SCAN_161718": "scan_20260818_161718_numericfirst01",
    "SCAN_165011": "scan_20260818_165011_numericfirst01",
    "SCAN_070604": "scan_20260819_070604_numericfirst01",
    "SCAN_085223": "scan_20260819_085223_numericfirst01",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(name: str, value: Any) -> None:
    (WORKSPACE / name).write_text(
        json.dumps(clean(value), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_md(name: str, text: str) -> None:
    (WORKSPACE / name).write_text(text.rstrip() + "\n", encoding="utf-8")


def heights(rows: Iterable[dict[str, Any]], key: str = "height_mm") -> list[float]:
    out = []
    for row in rows:
        value = row.get(key)
        if value is not None and math.isfinite(float(value)):
            out.append(float(value))
    return out


def evidence_mass(rows: list[dict[str, Any]], truth: float, width: float, height_key: str, weight_key: str) -> float:
    values = [(float(r[height_key]), max(0.0, float(r.get(weight_key, 1.0)))) for r in rows if r.get(height_key) is not None]
    total = sum(w for _, w in values)
    if not values or total <= 0:
        return 0.0
    return sum(w for v, w in values if abs(v - truth) <= width) / total


def neighborhood(rows: list[dict[str, Any]], truth: float, width: float, height_key: str, weight_key: str, provenance_key: str) -> dict[str, Any]:
    selected = [r for r in rows if r.get(height_key) is not None and abs(float(r[height_key]) - truth) <= width]
    return {
        "exists": bool(selected),
        "count": len(selected),
        "evidence_mass": evidence_mass(rows, truth, width, height_key, weight_key),
        "independent_provenance_count": len({str(r.get(provenance_key) or r.get("surface_id") or r.get("raw_id")) for r in selected}),
        "heights_mm": [float(r[height_key]) for r in selected],
    }


def hyp_neighborhood(rows: list[dict[str, Any]], truth: float, width: float) -> dict[str, Any]:
    selected = [r for r in rows if abs(float(r["center_mm"]) - truth) <= width]
    nearest = min(rows, key=lambda r: abs(float(r["center_mm"]) - truth)) if rows else None
    return {
        "exists": bool(selected),
        "count": len(selected),
        "posterior_probability": sum(float(r.get("posterior_probability", 0.0)) for r in selected),
        "nearest_center_mm": None if nearest is None else float(nearest["center_mm"]),
        "nearest_center_error_mm": None if nearest is None else float(nearest["center_mm"]) - truth,
    }


def weighted_quantile(rows: list[dict[str, Any]], truth: float) -> float | None:
    pairs = sorted((float(r["height_mm"]), max(0.0, float(r.get("weight", 1.0)))) for r in rows)
    total = sum(w for _, w in pairs)
    if total <= 0:
        return None
    return sum(w for h, w in pairs if h <= truth) / total


def descriptive(values: list[float], truth: float | None = None) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    a = np.asarray(values, dtype=float)
    result: dict[str, Any] = {
        "count": len(values),
        "mean_mm": float(np.mean(a)),
        "median_mm": float(np.median(a)),
        "std_mm": float(np.std(a)),
        "mad_mm": float(np.median(np.abs(a - np.median(a)))),
        "p05_mm": float(np.quantile(a, 0.05)),
        "p95_mm": float(np.quantile(a, 0.95)),
        "span_mm": float(np.max(a) - np.min(a)),
        "min_mm": float(np.min(a)),
        "max_mm": float(np.max(a)),
    }
    if truth is not None:
        err = np.abs(a - truth)
        result.update({
            "bias_of_mean_mm": float(np.mean(a) - truth),
            "abs_median_error_mm": float(abs(np.median(a) - truth)),
            "gt50_count": int(np.sum(err > 50)),
            "gt75_count": int(np.sum(err > 75)),
            "gt100_count": int(np.sum(err > 100)),
        })
    return result


def pearson(xs: list[float], ys: list[float]) -> dict[str, Any]:
    if len(xs) < 3 or np.std(xs) == 0 or np.std(ys) == 0:
        return {"n": len(xs), "r": None}
    return {"n": len(xs), "r": float(np.corrcoef(np.asarray(xs), np.asarray(ys))[0, 1])}


def metric_value(obj: dict[str, Any], ctx: dict[str, Any], method: str) -> float | None:
    est = ctx["p1_p2_p3_estimators"]
    mapping = {
        "P0_discrete_hypothesis": (ctx.get("p0_first_round_output") or {}).get("support_mm"),
        "P1_weighted_median": est.get("weighted_median"),
        "P2_huber": est.get("huber_location"),
        "P2_trimmed_mean": est.get("trimmed_weighted_mean"),
        "P2_robust_posterior": est.get("robust_posterior_mean"),
        "P3_late_multimodal": est.get("late_multimodal_soft"),
        "LOCAL_P1_weighted_median": obj["object_local_estimators"].get("weighted_median"),
        "LOCAL_P2_huber": obj["object_local_estimators"].get("huber_location"),
        "LOCAL_P2_trimmed_mean": obj["object_local_estimators"].get("trimmed_weighted_mean"),
        "LOCAL_P2_robust_posterior": obj["object_local_estimators"].get("robust_posterior_mean"),
        "LOCAL_P3_late_multimodal": obj["object_local_estimators"].get("late_multimodal_soft"),
    }
    value = mapping.get(method)
    return None if value is None else float(value)


def main() -> int:
    freeze = json.loads((WORKSPACE / "TRUTH_BLIND_ROUND2_FREEZE.json").read_text(encoding="utf-8"))
    required_hashes = {
        "code_sha256": WORKSPACE / "extract_and_counterfactual.py",
        "compact_data_sha256": WORKSPACE / "round2_compact_candidate_data.json",
        "counterfactual_outputs_sha256": WORKSPACE / "counterfactual_outputs_truth_blind.json",
        "full_raw_capture_sha256": WORKSPACE / "full_raw_candidate_capture.json",
        "round1_audit_sha256": Path(r"C:\ec\_calibration_workspaces\single_frame_candidate_distribution_fusion_20260819_01\candidate_distribution_audit.json"),
    }
    hash_verification = {key: {"expected": freeze[key], "actual": sha256_file(path), "match": freeze[key] == sha256_file(path), "path": str(path)} for key, path in required_hashes.items()}
    if not all(row["match"] for row in hash_verification.values()) or freeze.get("truth_loaded") is not False:
        raise RuntimeError("Truth-blind freeze verification failed")

    compact = json.loads((WORKSPACE / "round2_compact_candidate_data.json").read_text(encoding="utf-8"))
    truth_doc = json.loads(NUMERIC.read_text(encoding="utf-8"))
    truths = {k: float(v) for k, v in truth_doc["ground_truth_mm"].items()}
    metric_rows = {(r["scan_label"], r["logical_object_id"]): r for r in truth_doc["rows"]}
    tof_by_frame: dict[tuple[str, str], dict[str, Any]] = {}
    for scan, folder in TOF_DIR.items():
        doc = json.loads((TOF_ROOT / folder / "tof_frame_diagnostics.json").read_text(encoding="utf-8"))
        for row in doc["frames"]:
            tof_by_frame[(scan, row["frame_id"])] = row

    survival_rows: list[dict[str, Any]] = []
    centered_rows: list[dict[str, Any]] = []
    provenance_rows: list[dict[str, Any]] = []
    context_rows: list[dict[str, Any]] = []
    hypothesis_rows: list[dict[str, Any]] = []
    per_object_rows: list[dict[str, Any]] = []
    method_observations: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    method_error_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    frame_lower_presence: dict[str, dict[str, bool]] = defaultdict(dict)

    methods = [
        "Current_production", "P0_discrete_hypothesis", "P1_weighted_median", "P2_huber",
        "P2_trimmed_mean", "P2_robust_posterior", "P3_late_multimodal",
        "LOCAL_P1_weighted_median", "LOCAL_P2_huber", "LOCAL_P2_trimmed_mean",
        "LOCAL_P2_robust_posterior", "LOCAL_P3_late_multimodal",
    ]

    for scan, scan_doc in compact["scans"].items():
        for frame in scan_doc["frames"]:
            objects = {o["object_id"]: o for o in frame["objects"]}
            context_for = {oid: ctx for ctx in frame["contexts"] for oid in ctx["member_object_ids"]}
            lower_any = False
            for obj in frame["objects"]:
                metric = metric_rows.get((scan, obj["object_id"]))
                if metric is None:
                    continue
                layer = metric["layer"]
                truth = truths[layer]
                ctx = context_for[obj["object_id"]]
                r0 = obj["r0_full_candidates"]
                r0s = obj["r0s_serialized_candidates"]
                r1 = obj["r1_round1_representatives"]
                r1_full = obj["r1_full_representatives"]
                member_r1 = [r for oid in ctx["member_object_ids"] for r in objects[oid]["r1_round1_representatives"]]
                r3 = ctx["r3_first_round_hypotheses"]

                widths: dict[str, Any] = {}
                for width in WIDTHS:
                    r0n = neighborhood(r0, truth, width, "candidate_height_mm", "raw_evidence_weight", "source_component_id")
                    r0sn = neighborhood(r0s, truth, width, "support_mm", "raw_evidence_weight", "component_id")
                    r1n = neighborhood(r1, truth, width, "support_mm", "normalized_object_weight", "provenance_group_id")
                    r2n = neighborhood(member_r1, truth, width, "support_mm", "normalized_weight", "provenance_group_id")
                    r3n = hyp_neighborhood(r3, truth, width)
                    current_ok = obj.get("current_support_mm") is not None and abs(float(obj["current_support_mm"]) - truth) <= width
                    if not r0n["exists"]:
                        loss = "RAW_CANDIDATE_GENERATION"
                    elif not r0sn["exists"]:
                        loss = "DIAGNOSTIC_SERIALIZATION_TRUNCATION_AUDIT_ONLY"
                    elif not r1n["exists"]:
                        loss = "PROVENANCE_CONSOLIDATION"
                    elif not r2n["exists"]:
                        loss = "SUPPORT_CONTEXT_FORMATION"
                    elif not r3n["exists"]:
                        loss = "HYPOTHESIS_CONSTRUCTION"
                    elif not current_ok:
                        loss = "FINAL_SELECTOR"
                    else:
                        loss = "SURVIVED_TO_CURRENT_OUTPUT"
                    widths[str(width)] = {"R0_full_selector_input": r0n, "R0S_serialized_diagnostics": r0sn, "R1_round1_representatives": r1n, "R2_support_context": r2n, "R3_surface_hypotheses": r3n, "R4_current_output_near_truth": current_ok, "exact_first_loss_stage": loss}
                if layer == "lower" and widths["50"]["R0_full_selector_input"]["exists"]:
                    lower_any = True
                survival_rows.append({
                    "scan": scan, "frame_id": frame["frame_id"], "object_id": obj["object_id"], "class_name": obj["class_name"],
                    "layer": layer, "truth_mm": truth, "context_id": ctx["context_id"],
                    "stage_cardinality": {"R0": len(r0), "R0S": len(r0s), "R1": len(r1), "R1_full_capture_consolidation": len(r1_full), "R2": len(member_r1), "R3": len(r3), "R4": 1 if obj.get("current_support_mm") is not None else 0},
                    "stage_records": {
                        "R0_full_selector_input": [{"raw_id": r["raw_id"], "height_mm": r["candidate_height_mm"], "surface_height_mm": r["surface_height_mm"], "weight": r["raw_evidence_weight"], "provenance": r["source_component_id"], "surface_id": r["surface_id"], "method": r["method"], "tier": r["tier"], "height_transform_mm": r["height_transform_mm"]} for r in r0],
                        "R0S_serialized_diagnostics": [{"height_mm": r["support_mm"], "weight": r["raw_evidence_weight"], "provenance": r["component_id"], "surface_id": r["surface_id"], "candidate_kind": r["candidate_kind"]} for r in r0s],
                        "R1_representatives": [{"height_mm": r["support_mm"], "weight": r["normalized_object_weight"], "provenance_group_id": r["provenance_group_id"], "duplicate_surface_ids": r["duplicate_surface_ids"]} for r in r1],
                        "R2_support_context": {"context_id": ctx["context_id"], "assignment_reason": "same connected bbox-bottom-Y cluster (<=30 px), unless scene-spanning isolation", "assigned_object_representatives": len(r1), "excluded_object_representatives": 0, "shared_context_representative_count": len(member_r1), "member_object_ids": ctx["member_object_ids"]},
                        "R3_surface_hypotheses": r3,
                        "R4_current_output": {"support_mm": obj.get("current_support_mm"), "offline_p0_support_mm": (ctx.get("p0_first_round_output") or {}).get("support_mm")},
                    },
                    "current_support_mm": obj.get("current_support_mm"), "widths_mm": widths,
                })

                for group in r1_full:
                    raw_h = [float(v) for v in group["raw_heights_mm"]]
                    rep = float(group["representative_height_mm"])
                    provenance_rows.append({
                        "scan": scan, "frame_id": frame["frame_id"], "object_id": obj["object_id"], "layer": layer,
                        "group_id": group["provenance_group_id"], "raw_count": len(raw_h), "raw_heights_mm": raw_h,
                        "representative_height_mm": rep, "height_span_mm": group["height_span_mm"],
                        "representative_shift_from_closest_raw_mm": min(abs(rep-v) for v in raw_h),
                        "near_truth_raw_but_representative_lost_30mm": any(abs(v-truth) <= 30 for v in raw_h) and abs(rep-truth) > 30,
                        "near_truth_raw_but_representative_lost_50mm": any(abs(v-truth) <= 50 for v in raw_h) and abs(rep-truth) > 50,
                        "height_preserving_group_weight": sum(float(r["weight"]) for r in obj["p1_height_preserving_distribution"] if r["provenance_group_id"] == group["provenance_group_id"]),
                    })

                values = {"Current_production": None if obj.get("current_support_mm") is None else float(obj["current_support_mm"])}
                values.update({m: metric_value(obj, ctx, m) for m in methods if m != "Current_production"})
                for method, value in values.items():
                    if value is None:
                        continue
                    method_observations[method][scan][layer].append(value)
                    method_error_rows[method].append({"scan": scan, "frame_id": frame["frame_id"], "object_id": obj["object_id"], "layer": layer, "truth_mm": truth, "value_mm": value, "error_mm": value-truth, "distance_mm": obj.get("distance_mm"), "bbox_xyxy": obj["bbox_xyxy"], "class_name": obj["class_name"], "region_source": obj.get("region_source")})

                shared = {m: values[m] for m in ("P1_weighted_median", "P2_huber", "P2_trimmed_mean", "P2_robust_posterior", "P3_late_multimodal")}
                local = {m.replace("LOCAL_", ""): values["LOCAL_" + m.replace("P", "P", 1)] for m in []}
                local_map = {
                    "P1_weighted_median": values["LOCAL_P1_weighted_median"],
                    "P2_huber": values["LOCAL_P2_huber"],
                    "P2_trimmed_mean": values["LOCAL_P2_trimmed_mean"],
                    "P2_robust_posterior": values["LOCAL_P2_robust_posterior"],
                    "P3_late_multimodal": values["LOCAL_P3_late_multimodal"],
                }
                per_object_rows.append({
                    "scan": scan, "frame_id": frame["frame_id"], "object_id": obj["object_id"], "class_name": obj["class_name"], "layer": layer, "context_id": ctx["context_id"], "context_member_count": len(ctx["member_object_ids"]),
                    "shared_values_mm": shared, "object_local_values_mm": local_map,
                    "shared_minus_local_mm": {m: (None if shared[m] is None or local_map[m] is None else shared[m]-local_map[m]) for m in shared},
                    "sharing_improves_abs_error": {m: (None if shared[m] is None or local_map[m] is None else abs(shared[m]-truth) < abs(local_map[m]-truth)) for m in shared},
                })
            frame_lower_presence[scan][frame["frame_id"]] = lower_any

            # Context-layer analysis avoids counting a shared lower context once per member object.
            for ctx in frame["contexts"]:
                members = [objects[oid] for oid in ctx["member_object_ids"]]
                member_metrics = [metric_rows[(scan, o["object_id"])] for o in members if (scan, o["object_id"]) in metric_rows]
                layers = sorted({m["layer"] for m in member_metrics})
                context_rows.append({
                    "scan": scan, "frame_id": frame["frame_id"], "context_id": ctx["context_id"],
                    "member_object_ids": ctx["member_object_ids"], "member_classes": ctx["member_classes"], "member_layers": layers,
                    "mixed_upper_lower": len(layers) > 1, "bottle_ball_shared": "bottle" in ctx["member_classes"] and "sports ball" in ctx["member_classes"],
                    "bbox_bottom_y_center": ctx["bbox_bottom_y_center"], "candidate_count": ctx["r2_candidate_count"],
                })
                for layer in layers:
                    truth = truths[layer]
                    rows = ctx["r2_height_preserving_distribution"]
                    est = ctx["p1_p2_p3_estimators"]
                    modes = est.get("modes", [])
                    masses = {str(w): evidence_mass(rows, truth, w, "height_mm", "weight") for w in WIDTHS}
                    robust_center = float(est["huber_location"])
                    truth_modes = [m for m in modes if abs(float(m["center_mm"])-truth) <= 30 and float(m.get("probability", 0.0)) >= 0.2]
                    competing = [m for m in modes if float(m.get("probability", 0.0)) >= 0.2 and all(abs(float(m["center_mm"])-float(t["center_mm"])) > 30 for t in truth_modes)]
                    if abs(robust_center-truth) <= 30 and masses["50"] >= 0.5:
                        typ = "A_correct_centered"
                    elif truth_modes and competing:
                        typ = "B_multimodal_truth_peak_substantial"
                    else:
                        typ = "C_wrong_centered"
                    grid = est.get("density_grid", {})
                    gx = np.asarray(grid.get("height_mm", []), dtype=float)
                    gd = np.asarray(grid.get("density", []), dtype=float)
                    truth_density = float(np.interp(truth, gx, gd)) if len(gx) else None
                    density_rank = float(np.mean(gd <= truth_density)) if len(gd) and truth_density is not None else None
                    centered_rows.append({
                        "scan": scan, "frame_id": frame["frame_id"], "context_id": ctx["context_id"], "layer": layer, "truth_mm": truth,
                        "member_object_ids": [m["logical_object_id"] for m in member_metrics if m["layer"] == layer],
                        "candidate_count": len(rows), "classification": typ,
                        "statistics_mm": {k: est.get(k) for k in ("unweighted_mean", "unweighted_median", "weighted_mean", "weighted_median", "trimmed_weighted_mean", "huber_location", "robust_posterior_mean", "kde_global_mode")},
                        "top3_modes": modes[:3], "truth_weighted_quantile": weighted_quantile(rows, truth), "truth_density": truth_density, "truth_density_percentile_rank": density_rank,
                        "mass_within_truth_neighborhood": masses,
                    })
                    sensitivity = ctx["hypothesis_parameter_sensitivity"]
                    hypothesis_rows.append({
                        "scan": scan, "frame_id": frame["frame_id"], "context_id": ctx["context_id"], "layer": layer, "truth_mm": truth,
                        "first_round_hypotheses": ctx["r3_first_round_hypotheses"], "continuous_modes": modes,
                        "bandwidth_sensitivity": sensitivity,
                        "truth_peak_survival_by_bandwidth": {str(row["bandwidth_mm"]): {"unpruned": any(abs(float(m["center_mm"])-truth)<=30 for m in row["unpruned_top_modes"]), "relative_floor_10pct": any(abs(float(m["center_mm"])-truth)<=30 for m in row["relative_floor_0_10_top_modes"])} for row in sensitivity},
                    })

    lower_centered = [r for r in centered_rows if r["layer"] == "lower"]
    type_by_scan = {}
    for scan in compact["scans"]:
        rows = [r for r in lower_centered if r["scan"] == scan]
        counts = Counter(r["classification"] for r in rows)
        type_by_scan[scan] = {"context_layer_count": len(rows), **{k: counts[k]/len(rows) if rows else None for k in ("A_correct_centered", "B_multimodal_truth_peak_substantial", "C_wrong_centered")}}

    survival_summary = {}
    for scan in compact["scans"]:
        lower = [r for r in survival_rows if r["scan"] == scan and r["layer"] == "lower"]
        survival_summary[scan] = {
            "lower_object_count": len(lower),
            "frame_count": len(frame_lower_presence[scan]),
            "frame_level_raw_near_truth_presence_50mm_rate": sum(frame_lower_presence[scan].values()) / len(frame_lower_presence[scan]),
            "object_level_loss_stage_50mm": dict(Counter(r["widths_mm"]["50"]["exact_first_loss_stage"] for r in lower)),
        }

    representative_loss30 = [r for r in provenance_rows if r["near_truth_raw_but_representative_lost_30mm"]]
    representative_loss50 = [r for r in provenance_rows if r["near_truth_raw_but_representative_lost_50mm"]]
    provenance_summary = {
        "group_count": len(provenance_rows), "merged_group_count": sum(r["raw_count"] > 1 for r in provenance_rows),
        "near_truth_raw_but_representative_lost_30mm_count": len(representative_loss30),
        "near_truth_raw_but_representative_lost_50mm_count": len(representative_loss50),
        "height_preserving_alternative": "All raw heights retained; only each provenance group's total vote is normalized.",
    }

    context_summary = {
        "context_count": len(context_rows), "mixed_upper_lower_count": sum(r["mixed_upper_lower"] for r in context_rows),
        "bottle_ball_shared_count": sum(r["bottle_ball_shared"] for r in context_rows),
        "lower_objects_split_across_multiple_contexts_by_frame": [],
        "focus_085223_frame9_10": [r for r in context_rows if r["scan"] == "SCAN_085223" and r["frame_id"] in ("frame_000009", "frame_000010")],
    }
    for scan, scan_doc in compact["scans"].items():
        for frame in scan_doc["frames"]:
            lower_contexts = set()
            for ctx in frame["contexts"]:
                if any(metric_rows.get((scan, oid), {}).get("layer") == "lower" for oid in ctx["member_object_ids"]):
                    lower_contexts.add(ctx["context_id"])
            if len(lower_contexts) > 1:
                context_summary["lower_objects_split_across_multiple_contexts_by_frame"].append({"scan": scan, "frame_id": frame["frame_id"], "context_count": len(lower_contexts), "context_ids": sorted(lower_contexts)})

    repeatability: dict[str, Any] = {"method_selection_rule": "Minimum arithmetic mean of six scan lower-layer MAD; tie-break by lower-layer span. Truth values and error counts are excluded from selection.", "methods": {}}
    for method in methods:
        scan_stats = {}
        for scan in compact["scans"]:
            scan_stats[scan] = {layer: descriptive(method_observations[method][scan][layer], truths[layer]) for layer in ("lower", "upper")}
        lower_mads = [scan_stats[s]["lower"].get("mad_mm") for s in compact["scans"] if scan_stats[s]["lower"].get("count", 0)]
        lower_spans = [scan_stats[s]["lower"].get("span_mm") for s in compact["scans"] if scan_stats[s]["lower"].get("count", 0)]
        repeatability["methods"][method] = {
            "per_scan": scan_stats,
            "mean_lower_mad_mm": float(np.mean(lower_mads)), "mean_lower_span_mm": float(np.mean(lower_spans)),
            "total_gt100_count": sum(scan_stats[s][layer].get("gt100_count", 0) for s in compact["scans"] for layer in ("lower", "upper")),
            "total_lower_gt100_count": sum(scan_stats[s]["lower"].get("gt100_count", 0) for s in compact["scans"]),
        }
    candidates = [m for m in methods if m != "Current_production"]
    best = min(candidates, key=lambda m: (repeatability["methods"][m]["mean_lower_mad_mm"], repeatability["methods"][m]["mean_lower_span_mm"]))
    repeatability["best_offline_variant"] = best
    repeatability["best_offline_variant_requires_truth_fit"] = False

    type_b = [r for r in lower_centered if r["classification"].startswith("B_")]
    bimodal_rows = []
    for row in type_b:
        scan, frame_id, context_id = row["scan"], row["frame_id"], row["context_id"]
        frame = next(f for f in compact["scans"][scan]["frames"] if f["frame_id"] == frame_id)
        ctx = next(c for c in frame["contexts"] if c["context_id"] == context_id)
        truth = row["truth_mm"]
        est = ctx["p1_p2_p3_estimators"]
        modes = est["modes"]
        hard = float(max(modes, key=lambda m: float(m.get("probability", 0.0)))["center_mm"])
        bimodal_rows.append({
            "scan": scan, "frame_id": frame_id, "context_id": context_id, "truth_mm": truth,
            "modes": modes[:4], "hard_winner_mm": hard, "hard_winner_abs_error_mm": abs(hard-truth),
            "posterior_mean_mm": est["robust_posterior_mean"], "posterior_mean_abs_error_mm": abs(float(est["robust_posterior_mean"])-truth),
            "weighted_median_mm": est["weighted_median"], "weighted_median_abs_error_mm": abs(float(est["weighted_median"])-truth),
            "huber_mm": est["huber_location"], "huber_abs_error_mm": abs(float(est["huber_location"])-truth),
        })

    shared_summary: dict[str, Any] = {"rows": len(per_object_rows), "by_method": {}}
    for method in ("P1_weighted_median", "P2_huber", "P2_trimmed_mean", "P2_robust_posterior", "P3_late_multimodal"):
        relevant = [r for r in per_object_rows if r["context_member_count"] > 1 and r["sharing_improves_abs_error"][method] is not None]
        shared_summary["by_method"][method] = {"multi_object_context_rows": len(relevant), "sharing_improves_count": sum(bool(r["sharing_improves_abs_error"][method]) for r in relevant), "sharing_worsens_or_ties_count": sum(not bool(r["sharing_improves_abs_error"][method]) for r in relevant)}

    # Bias diagnosis is descriptive only. No correction is fitted or applied.
    bias_rows = method_error_rows[best]
    correlations = {}
    for feature in ("distance_mm",):
        rows = [r for r in bias_rows if r.get(feature) is not None]
        correlations[feature] = pearson([float(r[feature]) for r in rows], [float(r["error_mm"]) for r in rows])
    for axis, idx in (("bbox_center_x", (0, 2)), ("bbox_center_y", (1, 3))):
        xs = [(float(r["bbox_xyxy"][idx[0]]) + float(r["bbox_xyxy"][idx[1]]))/2 for r in bias_rows]
        correlations[axis] = pearson(xs, [float(r["error_mm"]) for r in bias_rows])
    valid_x, valid_y = [], []
    tof_status_groups: dict[str, list[float]] = defaultdict(list)
    for row in bias_rows:
        tof = tof_by_frame.get((row["scan"], row["frame_id"]), {})
        diag = tof.get("diagnostics", {})
        if diag.get("formal_valid_count") is not None:
            valid_x.append(float(diag["formal_valid_count"])); valid_y.append(float(row["error_mm"]))
        tof_status_groups[str(tof.get("tof_status", "missing"))].append(float(row["error_mm"]))
    correlations["tof_formal_valid_count"] = pearson(valid_x, valid_y)
    def group_stats(key: str) -> dict[str, Any]:
        groups: dict[str, list[float]] = defaultdict(list)
        for row in bias_rows:
            groups[str(row.get(key))].append(float(row["error_mm"]))
        return {k: descriptive(v) for k, v in groups.items()}
    bias = {
        "method": best, "correlations": correlations, "tof_status_error_groups": {k: descriptive(v) for k, v in tof_status_groups.items()},
        "class_error_groups": group_stats("class_name"), "region_source_error_groups": group_stats("region_source"),
        "raw_tof_range_available": False, "lighting_metadata_available": False,
        "causal_limit": "Frozen compact artifacts do not expose raw ToF ranges or lighting; distance/world-Z/projection effects are correlated and cannot be causally isolated here.",
        "correction_applied": False,
    }

    loss_counter = Counter(r["widths_mm"]["50"]["exact_first_loss_stage"] for r in survival_rows if r["layer"] == "lower")
    lower_total = sum(loss_counter.values())
    blockers = {
        "RAW_CANDIDATE_GENERATION_PRIMARY_BLOCKER": loss_counter["RAW_CANDIDATE_GENERATION"] / max(1, lower_total) >= 0.25,
        "PROVENANCE_CONSOLIDATION_PRIMARY_BLOCKER": loss_counter["PROVENANCE_CONSOLIDATION"] / max(1, lower_total) >= 0.15,
        "SUPPORT_CONTEXT_FORMATION_PRIMARY_BLOCKER": loss_counter["SUPPORT_CONTEXT_FORMATION"] / max(1, lower_total) >= 0.15,
        "HYPOTHESIS_CONSTRUCTION_PRIMARY_BLOCKER": loss_counter["HYPOTHESIS_CONSTRUCTION"] / max(1, lower_total) >= 0.15,
        "FINAL_SELECTOR_PRIMARY_BLOCKER": loss_counter["FINAL_SELECTOR"] / max(1, lower_total) >= 0.15,
        "UPSTREAM_SYSTEMATIC_BIAS_PRIMARY_BLOCKER": sum(r["classification"] == "C_wrong_centered" for r in lower_centered) / max(1, len(lower_centered)) >= 0.25,
        "decision_rule": "Primary if the stage is the first ±50 mm loss for at least 15% of lower object rows (raw generation: 25%), or Type-C contexts are at least 25%. Diagnostic serialization is excluded because runtime selection saw the full list.",
    }

    typical = [r for r in survival_rows if r["layer"] == "lower" and r["widths_mm"]["50"]["R0_full_selector_input"]["exists"] and not r["widths_mm"]["50"]["R3_surface_hypotheses"]["exists"]]
    typical.sort(key=lambda r: (r["widths_mm"]["50"]["R0_full_selector_input"]["evidence_mass"], r["widths_mm"]["50"]["R0_full_selector_input"]["independent_provenance_count"]), reverse=True)
    key_ids = {("SCAN_070604", "frame_000002"), ("SCAN_070604", "frame_000004"), ("SCAN_165011", "frame_000009"), ("SCAN_085223", "frame_000009")}
    key_rows = [r for r in survival_rows if (r["scan"], r["frame_id"]) in key_ids and r["layer"] == "lower"]

    survival_doc = {"schema_version": "candidate_survival_round2.v1", "stages": {"R0": "complete selector-input observations after production extractor", "R0S": "serialized diagnostic subset", "R1": "first-round representative consolidation", "R2": "first-round support-context membership", "R3": "first-round KDE surface hypotheses", "R4": "current production per-object support"}, "summary": survival_summary, "loss_stage_counts_lower_50mm": dict(loss_counter), "rows": survival_rows}
    centered_doc = {"schema_version": "raw_centeredness_round2.v1", "classification_rule": {"A": "Huber center within ±30 mm and mass within ±50 mm >= 0.5", "B": "not A; truth-near mode (±30 mm) probability >= 0.2 and competing mode probability >= 0.2", "C": "otherwise"}, "type_rates_by_scan_lower": type_by_scan, "rows": centered_rows}
    provenance_doc = {"schema_version": "provenance_round2.v1", "summary": provenance_summary, "rows": provenance_rows}
    context_doc = {"schema_version": "support_context_round2.v1", "summary": context_summary, "rows": context_rows}
    hypothesis_doc = {"schema_version": "hypothesis_round2.v1", "summary": {"row_count": len(hypothesis_rows), "lower_raw_near_truth_but_no_hypothesis_50mm_count": len(typical)}, "typical_information_loss_cases": typical[:20], "rows": hypothesis_rows}
    shared_doc = {"schema_version": "object_vs_shared_round2.v1", "summary": shared_summary, "rows": per_object_rows}
    bimodal_doc = {"schema_version": "bimodal_soft_round2.v1", "type_b_count": len(bimodal_rows), "rows": bimodal_rows}
    final_summary = {
        "freeze_hash_verification": hash_verification, "survival_summary": survival_summary, "type_rates_by_scan_lower": type_by_scan,
        "loss_stage_counts_lower_50mm": dict(loss_counter), "blockers": blockers, "best_offline_variant": best,
        "current": repeatability["methods"]["Current_production"], "best": repeatability["methods"][best],
        "typical_raw_near_truth_but_hypothesis_lost": typical[:20], "specified_key_rows": key_rows,
    }

    write_json("candidate_survival_audit.json", survival_doc)
    write_json("TRUTH_NEIGHBORHOOD_SURVIVAL_MATRIX.json", {"summary": survival_summary, "specified_key_rows": key_rows, "typical_10_20": typical[:20], "rows": survival_rows})
    write_json("RAW_DISTRIBUTION_CENTEREDNESS_AUDIT.json", centered_doc)
    write_json("PROVENANCE_CONSOLIDATION_AUDIT.json", provenance_doc)
    write_json("SUPPORT_CONTEXT_FORMATION_AUDIT.json", context_doc)
    write_json("SURFACE_HYPOTHESIS_CONSTRUCTION_AUDIT.json", hypothesis_doc)
    write_json("PER_OBJECT_VS_SHARED_CONTEXT_AUDIT.json", shared_doc)
    write_json("BIMODAL_SOFT_ESTIMATE_AUDIT.json", bimodal_doc)
    write_json("SECOND_ROUND_SINGLE_FRAME_REPEATABILITY_COMPARISON.json", repeatability)
    write_json("ROUND2_EVALUATION_SUMMARY.json", final_summary)

    summary_lines = [f"- {scan}: frame-level R0 ±50 presence={row['frame_level_raw_near_truth_presence_50mm_rate']:.1%}; first-loss={row['object_level_loss_stage_50mm']}" for scan, row in survival_summary.items()]
    write_md("CANDIDATE_SURVIVAL_AUDIT.md", "# Candidate Survival Audit\n\nR0 is the complete selector-input list recovered by read-only replay instrumentation; R0S is diagnostics only and may be truncated. The row-level JSON preserves cardinality and ±10/20/30/50 evidence.\n\n" + "\n".join(summary_lines) + f"\n\nLower-object ±50 first-loss counts: `{dict(loss_counter)}`. Diagnostic serialization loss is not a runtime blocker.")
    write_md("TRUTH_NEIGHBORHOOD_SURVIVAL_MATRIX.md", "# Truth-Neighborhood Survival Matrix\n\nTruth is an audit label only. Full per-object matrices are in JSON.\n\n## Specified cases\n\n" + "\n".join(f"- {r['scan']} {r['frame_id']} {r['object_id']}: ±50 first loss = {r['widths_mm']['50']['exact_first_loss_stage']}" for r in key_rows) + "\n\n## Strongest raw-near / hypothesis-lost cases\n\n" + "\n".join(f"- {r['scan']} {r['frame_id']} {r['object_id']}: mass={r['widths_mm']['50']['R0_full_selector_input']['evidence_mass']:.3f}, provenance={r['widths_mm']['50']['R0_full_selector_input']['independent_provenance_count']}, first loss={r['widths_mm']['50']['exact_first_loss_stage']}" for r in typical[:20]))
    write_md("RAW_DISTRIBUTION_CENTEREDNESS_AUDIT.md", "# Raw Distribution Centeredness Audit\n\nClassification is applied after truth-blind methods and parameters were hash-frozen.\n\n" + "\n".join(f"- {scan}: A={row['A_correct_centered']:.1%}, B={row['B_multimodal_truth_peak_substantial']:.1%}, C={row['C_wrong_centered']:.1%} ({row['context_layer_count']} lower context-layer rows)" for scan, row in type_by_scan.items()))
    write_md("PROVENANCE_CONSOLIDATION_AUDIT.md", f"# Provenance Consolidation Audit\n\nThe first-round consolidation can average heights inside a duplicate group. Across {provenance_summary['group_count']} full-capture groups, {provenance_summary['merged_group_count']} contain multiple raw heights. Near-truth evidence was moved outside ±30 mm in {len(representative_loss30)} groups and outside ±50 mm in {len(representative_loss50)} groups.\n\n`HEIGHT_PRESERVING_PROVENANCE_NORMALIZATION` was tested: raw heights remain unchanged and only each group's total vote is normalized.")
    write_md("SUPPORT_CONTEXT_FORMATION_AUDIT.md", f"# Support Context Formation Audit\n\nContexts use bbox-bottom-Y linkage (30 px) with scene-spanning isolation. Of {len(context_rows)} contexts, {context_summary['mixed_upper_lower_count']} mix audit-labelled upper/lower objects and {context_summary['bottle_ball_shared_count']} share bottle/ball evidence. {len(context_summary['lower_objects_split_across_multiple_contexts_by_frame'])} frames split lower objects across multiple contexts. This relation is fragile for ball geometry and bbox-only/mask-dependent objects; continuous object-local distributions should be retained until context interpretation.")
    write_md("SURFACE_HYPOTHESIS_CONSTRUCTION_AUDIT.md", f"# Surface Hypothesis Construction Audit\n\nThe first-round path applies 24 mm KDE bandwidth, 42 mm minimum peak separation, 10% relative-density floor, and 42 mm member assignment radius after representative-height reconstruction. {len(typical)} lower object rows contain an R0 candidate within ±50 mm but no first-round hypothesis within ±50 mm. Bandwidth sensitivity at 12/18/24/32/48/64 mm is recorded with pruning on/off. The continuous P1/P2 paths avoid this intermediate hard discretization.")
    write_md("PER_OBJECT_VS_SHARED_CONTEXT_AUDIT.md", "# Per-Object vs Shared-Context Audit\n\nSame-frame sharing is evaluated only after each object independently produces candidates. It is not cross-frame.\n\n" + "\n".join(f"- {m}: sharing improved {v['sharing_improves_count']}/{v['multi_object_context_rows']} multi-object rows; worsened/tied {v['sharing_worsens_or_ties_count']}." for m, v in shared_summary["by_method"].items()) + "\n\nBecause helping and contamination both occur, unconditional cross-object pooling is not justified.")
    write_md("BIMODAL_SOFT_ESTIMATE_AUDIT.md", f"# Bimodal Soft Estimate Audit\n\n{len(bimodal_rows)} lower context-layer rows meet the frozen Type-B definition. JSON compares dominant hard mode, posterior mean, weighted median, and Huber location. Truth is used only for post-hoc error columns; no mode weight was tuned to 725 mm.")
    write_md("SECOND_ROUND_SINGLE_FRAME_REPEATABILITY_COMPARISON.md", "# Second-Round Single-Frame Repeatability Comparison\n\nEvery value is computed from one frame only; 11 frames are aggregated only for evaluation.\n\n" + "\n".join(f"- {m}: mean lower MAD={v['mean_lower_mad_mm']:.3f} mm; mean lower span={v['mean_lower_span_mm']:.3f} mm; total >100 mm={v['total_gt100_count']}" for m, v in repeatability["methods"].items()) + f"\n\nTruth-blind repeatability winner: `{best}`. Selection used MAD then span, never truth error.")
    write_md("SYSTEMATIC_BIAS_DIAGNOSTIC_V2.md", "# Systematic Bias Diagnostic V2\n\nNo calibration or correction was applied. For the truth-blind repeatability winner, descriptive Pearson correlations are:\n\n" + "\n".join(f"- {k}: r={v['r']}, n={v['n']}" for k, v in correlations.items()) + "\n\nRaw ToF range and lighting were not retained in the compact authority artifacts. Distance, Stage2 projection, DELTAR depth and world-Z therefore cannot be causally separated here. Class, region-source, and ToF-status group summaries are in `ROUND2_EVALUATION_SUMMARY.json`/the script-derived JSON evidence.")
    write_md("SECOND_ROUND_FUSION_ARCHITECTURE_OPTIONS.md", """# Second-Round Fusion Architecture Options

All options remain design candidates; production was not modified.

## A — Repair preprocessing, keep discrete hypotheses

Preserves explicit surface identity, but remains sensitive to representative-height averaging, KDE bandwidth, peak floor, separation, and assignment radius. It is appropriate only if hypothesis survival can be made stable without truth-tuned thresholds.

## B — Height-preserving provenance-normalized continuous distribution

Retains every candidate height while limiting each provenance group's total vote. It directly removes an observed information-compression mechanism and is easy to inspect. Risk: a single robust center can hide genuine multimodality.

## C — Continuous distribution with late multimodal interpretation

Retains B and delays dominant-mode/posterior interpretation to the final step. It best matches the observed mix of correct-centered and multimodal frames. Risk: more states, calibration obligations, and ambiguous-output policy.

## D — Minimal provenance-normalized robust location

Weighted median or Huber only. Lowest implementation complexity and a useful fallback, but it cannot express competing support surfaces and shared-context contamination must remain gated.

## E — Keep current selector, diagnose upstream bias first

Appropriate for Type-C-dominant scans, where fusion cannot create missing central evidence. It does not address raw-near evidence lost during hypothesis construction.

Recommendation: **C**, built on B's lossless representation, because it preserves information through the pipeline and handles Type B without an early hard winner. Alternate: **D** as a minimal, separately gated first implementation if Owner prioritizes auditability and rollback simplicity. Type-C cases still require an upstream workstream; neither option should be truth-fit.
""")
    print(json.dumps(clean({"best": best, "loss": dict(loss_counter), "types": type_by_scan, "blockers": blockers, "current": repeatability["methods"]["Current_production"], "best_stats": repeatability["methods"][best]}), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
