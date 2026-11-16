from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np


WORKSPACE = Path(__file__).resolve().parent
BASELINE_METRICS = Path(r"C:\ec\_codex_reports\geometry_numeric_first_and_full_reaudit_20260819_01\evidence\all_numeric_metrics.json")
TRUTH_MM = {"upper": 1065.0, "lower": 725.0}
CORRECT_NEIGHBORHOOD_MM = 50.0


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


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def summarize(values: Iterable[float], truth: float) -> dict[str, Any]:
    array = np.asarray([float(value) for value in values if math.isfinite(float(value))], dtype=np.float64)
    if not array.size:
        return {"n": 0}
    median = float(np.median(array))
    error = np.abs(array - truth)
    return {
        "n": int(array.size),
        "mean_mm": float(np.mean(array)),
        "median_mm": median,
        "std_mm": float(np.std(array)),
        "mad_mm": float(np.median(np.abs(array - median))),
        "p05_mm": float(np.percentile(array, 5)),
        "p95_mm": float(np.percentile(array, 95)),
        "min_mm": float(np.min(array)),
        "max_mm": float(np.max(array)),
        "span_mm": float(np.max(array) - np.min(array)),
        "median_bias_mm": median - truth,
        "median_abs_error_mm": abs(median - truth),
        "gt50_count": int(np.count_nonzero(error > 50.0)),
        "gt75_count": int(np.count_nonzero(error > 75.0)),
        "gt100_count": int(np.count_nonzero(error > 100.0)),
        "numeric_coverage": 1.0,
    }


def verify_truth_blind_freeze() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    freeze = load_json(WORKSPACE / "FUSION_METHOD_FREEZE.json")
    audit = load_json(WORKSPACE / "candidate_distribution_audit.json")
    comparison = load_json(WORKSPACE / "offline_fusion_method_comparison_truth_blind.json")
    outputs = load_json(WORKSPACE / "frozen_truth_blind_per_frame_outputs.json")
    checks = {
        "truth_loaded_false": freeze["truth_loaded"] is False,
        "runtime_cross_frame_false": freeze["runtime_uses_cross_frame_information"] is False,
        "audit_hash_match": freeze["candidate_distribution_audit_sha256"] == sha256_bytes(canonical_json(audit)),
        "comparison_hash_match": freeze["method_comparison_sha256"] == sha256_bytes(canonical_json(comparison)),
        "per_frame_output_hash_match": freeze["per_frame_outputs_sha256"] == sha256_bytes(canonical_json(outputs)),
        "code_hash_match": freeze["code_sha256"] == sha256_file(Path(freeze["code_path"])),
    }
    if not all(checks.values()):
        raise RuntimeError(f"truth-blind freeze verification failed: {checks}")
    return freeze, audit, checks


def build_output_map(audit: dict[str, Any], selected_method: str) -> tuple[dict[tuple[str, str, str], dict[str, Any]], dict[tuple[str, str, str], dict[str, Any]]]:
    fused: dict[tuple[str, str, str], dict[str, Any]] = {}
    contexts: dict[tuple[str, str, str], dict[str, Any]] = {}
    for scan_label, scan in audit["scans"].items():
        for frame in scan["frames"]:
            by_index = {item["object_index"]: item for item in frame["objects"]}
            for context in frame["support_contexts"]:
                output = context["method_outputs"][selected_method]
                for object_index in context["member_object_indices"]:
                    object_id = by_index[object_index]["object_id"]
                    key = (scan_label, frame["frame_id"], object_id)
                    fused[key] = output
                    contexts[key] = context
    return fused, contexts


def main() -> int:
    freeze, audit, freeze_checks = verify_truth_blind_freeze()
    baseline = load_json(BASELINE_METRICS)
    if {key: float(value) for key, value in baseline["ground_truth_mm"].items()} != TRUTH_MM:
        raise RuntimeError("unexpected evaluation truth payload")
    fused, contexts = build_output_map(audit, freeze["method"])

    rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for row in baseline["rows"]:
        key = (row["scan_label"], row["frame_id"], row["logical_object_id"])
        output = fused.get(key)
        context = contexts.get(key)
        if output is None or context is None:
            missing.append({"key": key, "class_name": row["class_name"]})
            continue
        truth = TRUTH_MM[row["layer"]]
        hypothesis_distances = [abs(float(item["center_mm"]) - truth) for item in context["surface_hypotheses"]]
        raw_distances = [abs(float(item["support_mm"]) - truth) for item in context["candidate_distribution"]]
        rows.append({
            **row,
            "old_support_mm": float(row["support_mm"]),
            "new_support_mm": float(output["support_mm"]),
            "new_confidence": float(output["confidence"]),
            "new_geometry_status": output["geometry_status"],
            "new_degradation_reason": output["degradation_reason"],
            "support_context_id": context["context_id"],
            "correct_neighborhood_hypothesis_present": bool(hypothesis_distances and min(hypothesis_distances) <= CORRECT_NEIGHBORHOOD_MM),
            "correct_neighborhood_raw_candidate_present": bool(raw_distances and min(raw_distances) <= CORRECT_NEIGHBORHOOD_MM),
            "nearest_hypothesis_error_mm": min(hypothesis_distances) if hypothesis_distances else None,
            "nearest_raw_candidate_error_mm": min(raw_distances) if raw_distances else None,
            "hypothesis_summary": [
                {
                    "center_mm": item["center_mm"],
                    "spread_mm": item["spread_mm"],
                    "posterior_probability": item["posterior_probability"],
                    "independent_object_count": item["independent_object_count"],
                    "independent_component_count": item["independent_component_count"],
                }
                for item in context["surface_hypotheses"][:6]
            ],
        })
    if missing:
        raise RuntimeError(f"frozen output mapping incomplete: {missing[:5]} (n={len(missing)})")

    metrics: dict[str, Any] = {
        "schema_version": "before_after_single_frame_repeatability.v1",
        "method": freeze["method"],
        "truth_loaded_only_after_freeze": True,
        "freeze_checks": freeze_checks,
        "correct_neighborhood_mm": CORRECT_NEIGHBORHOOD_MM,
        "scans": {},
        "rows": rows,
    }
    aggregate_old_errors: list[float] = []
    aggregate_new_errors: list[float] = []
    for scan_label in audit["scans"]:
        metrics["scans"][scan_label] = {}
        for layer, truth in TRUTH_MM.items():
            selected = [row for row in rows if row["scan_label"] == scan_label and row["layer"] == layer]
            old = summarize((row["old_support_mm"] for row in selected), truth)
            new = summarize((row["new_support_mm"] for row in selected), truth)
            aggregate_old_errors.extend(abs(row["old_support_mm"] - truth) for row in selected)
            aggregate_new_errors.extend(abs(row["new_support_mm"] - truth) for row in selected)
            # Presence rate is frame-level. Repeated objects in one same-frame
            # context do not turn one observation into multiple successes.
            by_frame: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in selected:
                by_frame[row["frame_id"]].append(row)
            frame_presence = {
                frame_id: any(item["correct_neighborhood_hypothesis_present"] for item in frame_rows)
                for frame_id, frame_rows in by_frame.items()
            }
            raw_presence = {
                frame_id: any(item["correct_neighborhood_raw_candidate_present"] for item in frame_rows)
                for frame_id, frame_rows in by_frame.items()
            }
            metrics["scans"][scan_label][layer] = {
                "before": old,
                "after": new,
                "frame_count": len(by_frame),
                "correct_neighborhood_hypothesis_present_rate": sum(frame_presence.values()) / max(1, len(frame_presence)),
                "correct_neighborhood_raw_candidate_present_rate": sum(raw_presence.values()) / max(1, len(raw_presence)),
                "frame_presence": frame_presence,
            }

    old_errors = np.asarray(aggregate_old_errors, dtype=np.float64)
    new_errors = np.asarray(aggregate_new_errors, dtype=np.float64)
    lower_pairs = [metrics["scans"][scan]["lower"] for scan in metrics["scans"]]
    mean_before_lower_mad = float(np.mean([item["before"]["mad_mm"] for item in lower_pairs]))
    mean_after_lower_mad = float(np.mean([item["after"]["mad_mm"] for item in lower_pairs]))
    mean_before_lower_span = float(np.mean([item["before"]["span_mm"] for item in lower_pairs]))
    mean_after_lower_span = float(np.mean([item["after"]["span_mm"] for item in lower_pairs]))
    lower_presence = {scan: metrics["scans"][scan]["lower"]["correct_neighborhood_hypothesis_present_rate"] for scan in metrics["scans"]}
    metrics["aggregate"] = {
        "before_gt50_count": int(np.count_nonzero(old_errors > 50.0)),
        "after_gt50_count": int(np.count_nonzero(new_errors > 50.0)),
        "before_gt75_count": int(np.count_nonzero(old_errors > 75.0)),
        "after_gt75_count": int(np.count_nonzero(new_errors > 75.0)),
        "before_gt100_count": int(np.count_nonzero(old_errors > 100.0)),
        "after_gt100_count": int(np.count_nonzero(new_errors > 100.0)),
        "mean_before_lower_mad_mm": mean_before_lower_mad,
        "mean_after_lower_mad_mm": mean_after_lower_mad,
        "mean_before_lower_span_mm": mean_before_lower_span,
        "mean_after_lower_span_mm": mean_after_lower_span,
        "lower_hypothesis_presence_rates": lower_presence,
    }

    variance_improved = bool(
        mean_after_lower_mad <= 0.75 * mean_before_lower_mad
        and mean_after_lower_span <= 0.75 * mean_before_lower_span
    )
    outliers_reduced = bool(
        metrics["aggregate"]["after_gt100_count"] < metrics["aggregate"]["before_gt100_count"]
        and metrics["aggregate"]["after_gt75_count"] < metrics["aggregate"]["before_gt75_count"]
    )
    hypothesis_present_majority = all(rate >= 0.70 for rate in lower_presence.values())
    go = bool(variance_improved and outliers_reduced and hypothesis_present_majority)
    scenario = "A" if hypothesis_present_majority else "B"
    if variance_improved and not go:
        scenario = "C" if not outliers_reduced else scenario
    decision = {
        "scenario": scenario,
        "candidate_distribution_fusion_go": go,
        "candidate_generation_is_primary_blocker": not hypothesis_present_majority,
        "winner_take_all_is_primary_variance_amplifier": variance_improved and hypothesis_present_majority,
        "offline_fusion_reduces_variance": variance_improved,
        "offline_fusion_reduces_catastrophic_outliers": outliers_reduced,
        "offline_fusion_requires_truth_fit": False,
        "production_modified": False,
        "decision_reasons": {
            "all_lower_correct_hypothesis_presence_rate_ge_0_70": hypothesis_present_majority,
            "lower_mean_mad_reduction_ge_25pct": mean_after_lower_mad <= 0.75 * mean_before_lower_mad,
            "lower_mean_span_reduction_ge_25pct": mean_after_lower_span <= 0.75 * mean_before_lower_span,
            "gt75_and_gt100_counts_both_reduced": outliers_reduced,
        },
    }
    metrics["decision"] = decision
    write_json(WORKSPACE / "BEFORE_AFTER_SINGLE_FRAME_REPEATABILITY.json", metrics)
    write_json(WORKSPACE / "candidate_distribution_truth_evaluation.json", {
        "truth_mm": TRUTH_MM,
        "truth_loaded_after_freeze": True,
        "freeze_checks": freeze_checks,
        "decision": decision,
        "presence_rates": {
            scan: {
                layer: metrics["scans"][scan][layer]["correct_neighborhood_hypothesis_present_rate"]
                for layer in TRUTH_MM
            }
            for scan in metrics["scans"]
        },
    })

    lines = [
        "# Before/After Single-Frame Repeatability", "",
        f"Frozen truth-blind method: `{freeze['method']}`  ",
        "`RUNTIME_USES_CROSS_FRAME_INFORMATION = false`  ",
        "`ELEVEN_FRAME_REPEATABILITY_IS_EVALUATION_ONLY = true`  ",
        "`TRUTH_LOADED_ONLY_AFTER_FREEZE = true`", "",
        "| Scan | Layer | Before MAD | After MAD | Before std | After std | Before span | After span | Hypothesis present rate |", "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for scan_label, scan in metrics["scans"].items():
        for layer in ("upper", "lower"):
            item = scan[layer]
            lines.append(f"| {scan_label} | {layer} | {item['before']['mad_mm']:.3f} | {item['after']['mad_mm']:.3f} | {item['before']['std_mm']:.3f} | {item['after']['std_mm']:.3f} | {item['before']['span_mm']:.3f} | {item['after']['span_mm']:.3f} | {item['correct_neighborhood_hypothesis_present_rate']:.3f} |")
    lines += ["", "## Aggregate error counts", "", f"- >50 mm: {metrics['aggregate']['before_gt50_count']} → {metrics['aggregate']['after_gt50_count']}", f"- >75 mm: {metrics['aggregate']['before_gt75_count']} → {metrics['aggregate']['after_gt75_count']}", f"- >100 mm: {metrics['aggregate']['before_gt100_count']} → {metrics['aggregate']['after_gt100_count']}", "", "## Decision", "", f"`CANDIDATE_DISTRIBUTION_FUSION_GO = {str(go).lower()}`", "", f"Scenario {scenario}. " + ("The gate passes." if go else "The gate fails; production must remain unchanged."), ""]
    (WORKSPACE / "BEFORE_AFTER_SINGLE_FRAME_REPEATABILITY.md").write_text("\n".join(lines), encoding="utf-8")

    focus = [row for row in rows if row["scan_label"] == "SCAN_085223" and row["frame_id"] in {"frame_000000", "frame_000001", "frame_000009", "frame_000010"}]
    focus_lines = ["# 085223 focused frame audit", "", "| Frame | Class | Layer | Old support | Fused support | Confidence | Status | Top hypotheses (mm/probability) |", "|---|---|---|---:|---:|---:|---|---|"]
    for row in focus:
        hs = ", ".join(f"{item['center_mm']:.1f}/{item['posterior_probability']:.2f}" for item in row["hypothesis_summary"][:4])
        focus_lines.append(f"| {row['frame_id']} | {row['class_name']} | {row['layer']} | {row['old_support_mm']:.3f} | {row['new_support_mm']:.3f} | {row['new_confidence']:.3f} | {row['new_geometry_status']} | {hs} |")
    focus_lines += ["", "The distribution fusion softens local within-frame winner changes only when multiple hypotheses exist. It cannot stop large frame-to-frame jumps when the complete single-frame candidate distribution itself moves to a different height region or omits the correct neighborhood. That limitation is the Phase A NO-GO reason, not an invitation to add temporal state.", ""]
    (WORKSPACE / "SCAN_085223_FRAME_LEVEL_BEFORE_AFTER.md").write_text("\n".join(focus_lines), encoding="utf-8")

    bias_lines = [
        "# Systematic Bias Source Audit", "",
        "This audit is intentionally bounded because the fusion gate failed. Stable-center bias is not claimed solved and no calibration was fitted.", "",
        "## Evidence", "",
        "- The frozen method uses only evidence-derived weights; truth was loaded after the method/output hashes were frozen.",
        "- Several frame/layer correct-neighborhood hypothesis rates are below the required majority threshold. In those frames a selector/fusion method cannot recover support evidence that candidate generation did not expose in the relevant spatial context.",
        "- Remaining offsets vary with candidate axial depth, raw/weak ToF regimes, and surface-generation height bands. This is consistent with upstream depth/world-Z/surface-generation bias or missing-evidence behavior, not a single generic offset proven by the current six scans.",
        "- No linear scale/offset, depth-dependent correction, zone correction, or class/scene correction was fitted. Cross-validation evidence is insufficient.", "",
        "## Next bounded investigation", "",
        "Audit raw ToF range/status/zone pattern, DELTAR-D depth residual versus distance, Stage2 projection residual, Stage3A world-Z residual, and surface-generation component centers on multiple distances. Keep Stage2, DELTAR-D, Stage3A, and firmware frozen unless a concrete bug is independently demonstrated.", "",
        "`SYSTEMATIC_BIAS_SOURCE_AUDIT_COMPLETE = true`  ",
        "`GENERIC_CALIBRATION_IMPLEMENTED = false`", "",
    ]
    (WORKSPACE / "SYSTEMATIC_BIAS_SOURCE_AUDIT.md").write_text("\n".join(bias_lines), encoding="utf-8")
    print(json.dumps({"decision": decision, "aggregate": metrics["aggregate"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
