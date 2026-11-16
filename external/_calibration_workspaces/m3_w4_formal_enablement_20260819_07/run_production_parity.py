from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np


PIPELINE = Path(r"C:\ec\_deltar_p4_pipeline")
WORKSPACE = Path(__file__).resolve().parent
RESEARCH = Path(r"C:\ec\_calibration_workspaces\m3_peak_weighting_audit_20260819_06")
RESULTS = Path(r"C:\ec\_deltar_scan_service_data\results")
sys.path.insert(0, str(PIPELINE / "src"))

from deltar_p4_pipeline.all_object_geometry import (  # noqa: E402
    load_geometry_profile,
    rebuild_all_object_geometry_from_cache,
)


REPLAYS = {
    "G001": "scan_20260818_092411_numericfirst01",
    "G002": "scan_20260818_092729_numericfirst01",
    "SCAN_161718": "scan_20260818_161718_numericfirst01",
    "SCAN_165011": "scan_20260818_165011_numericfirst01",
    "SCAN_070604": "scan_20260819_070604_numericfirst01",
    "SCAN_085223": "scan_20260819_085223_numericfirst01",
}
CORE = {"cup", "bottle", "sports ball", "sports_ball", "ball"}
TRUTH = {"lower": 725.0, "upper": 1065.0}
TOLERANCE_MM = 1e-9


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def klass(value: str) -> str:
    return str(value).strip().lower().replace("-", " ")


def stats(values: list[float], truth: float) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    array = np.asarray(values, dtype=np.float64)
    error = np.abs(array - truth)
    median = float(np.median(array))
    return {
        "count": int(array.size),
        "min_mm": float(array.min()),
        "max_mm": float(array.max()),
        "median_mm": median,
        "mean_mm": float(array.mean()),
        "mad_mm": float(np.median(np.abs(array - median))),
        "std_mm": float(array.std()),
        "span_mm": float(np.ptp(array)),
        "p05_mm": float(np.percentile(array, 5)),
        "p95_mm": float(np.percentile(array, 95)),
        "median_bias_mm": median - truth,
        "median_abs_error_mm": float(np.median(error)),
        "within20_count": int(np.count_nonzero(error <= 20)),
        "within30_count": int(np.count_nonzero(error <= 30)),
        "within50_count": int(np.count_nonzero(error <= 50)),
        "gt50_count": int(np.count_nonzero(error > 50)),
        "gt75_count": int(np.count_nonzero(error > 75)),
        "gt100_count": int(np.count_nonzero(error > 100)),
    }


def scope_metrics(rows: list[dict[str, Any]], core_only: bool) -> dict[str, Any]:
    values: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        if core_only and klass(row["class_name"]) not in CORE:
            continue
        values.setdefault(row["scan"], {"lower": [], "upper": []})[
            row["layer"]
        ].append(float(row["production_support_mm"]))
    per_scan: dict[str, Any] = {}
    aggregate = {"lower": [], "upper": []}
    for scan in sorted(values):
        per_scan[scan] = {}
        for layer in ("lower", "upper"):
            layer_values = values[scan][layer]
            aggregate[layer].extend(layer_values)
            per_scan[scan][layer] = stats(layer_values, TRUTH[layer])
    aggregate_stats = {
        layer: stats(aggregate[layer], TRUTH[layer]) for layer in ("lower", "upper")
    }
    lower = [row["lower"] for row in per_scan.values() if row["lower"]["count"]]
    return {
        "per_scan": per_scan,
        "aggregate": aggregate_stats,
        "numeric_count": sum(row["count"] for row in aggregate_stats.values()),
        "mean_scan_lower_mad_mm": float(np.mean([row["mad_mm"] for row in lower])),
        "mean_scan_lower_std_mm": float(np.mean([row["std_mm"] for row in lower])),
        "mean_scan_lower_span_mm": float(np.mean([row["span_mm"] for row in lower])),
        "gt50_count": sum(row["gt50_count"] for row in aggregate_stats.values()),
        "gt75_count": sum(row["gt75_count"] for row in aggregate_stats.values()),
        "gt100_count": sum(row["gt100_count"] for row in aggregate_stats.values()),
    }


def main() -> int:
    freeze = json.loads((RESEARCH / "PEAK_WEIGHTING_FREEZE.json").read_text(encoding="utf-8"))
    evaluation = json.loads(
        (RESEARCH / "PEAK_WEIGHTING_TRUTH_EVALUATION.json").read_text(encoding="utf-8")
    )
    profile = load_geometry_profile(PIPELINE / "profiles" / "formal_geometry_region_profile.json")
    replay_root = WORKSPACE / "production_replays"
    replay_root.mkdir(parents=True, exist_ok=True)
    documents: dict[str, dict[str, Any]] = {}
    replay_manifest: dict[str, Any] = {}
    for scan, result_name in REPLAYS.items():
        destination = replay_root / f"{scan}.stage8b_all_object_geometry.v1.json"
        document, _ = rebuild_all_object_geometry_from_cache(
            RESULTS / result_name, profile, destination=destination
        )
        documents[scan] = document
        replay_manifest[scan] = {
            "source_result_root": str((RESULTS / result_name).resolve()),
            "production_output": str(destination.resolve()),
            "sha256": sha256(destination),
            "frame_count": len(document["frames"]),
            "summary": document["summary"],
        }

    production_objects: dict[tuple[str, str, str], dict[str, Any]] = {}
    production_contexts: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = {}
    all_object_checks = {
        "object_count": 0,
        "numeric_count": 0,
        "non_finite_count": 0,
        "non_positive_extent_count": 0,
        "missing_m3_w4_diagnostic_count": 0,
        "unavailable_count": 0,
    }
    for scan, document in documents.items():
        for frame in document["frames"]:
            for obj in frame["objects"]:
                all_object_checks["object_count"] += 1
                key = (scan, frame["frame_id"], obj["logical_object_id"])
                production_objects[key] = obj
                if not obj["numeric_available"]:
                    all_object_checks["unavailable_count"] += 1
                    continue
                all_object_checks["numeric_count"] += 1
                geometry = obj["geometry"]
                numbers = [
                    geometry["support_height_mm"],
                    geometry["top_height_above_floor_mm"],
                    geometry["object_height_mm"],
                ]
                if not all(math.isfinite(float(value)) for value in numbers):
                    all_object_checks["non_finite_count"] += 1
                if float(geometry["object_height_mm"]) <= 0.0:
                    all_object_checks["non_positive_extent_count"] += 1
                diagnostic = (
                    obj.get("diagnostics", {})
                    .get("generic_support", {})
                    .get("diagnostics", {})
                    .get("m3_w4")
                )
                if not diagnostic:
                    all_object_checks["missing_m3_w4_diagnostic_count"] += 1
                    continue
                context_key = (
                    scan,
                    frame["frame_id"],
                    tuple(diagnostic["member_object_ids"]),
                )
                production_contexts.setdefault(context_key, diagnostic)

    context_rows = []
    maxima = {
        "mode_center_abs_diff_mm": 0.0,
        "mode_probability_abs_diff": 0.0,
        "diagnostic_support_abs_diff_mm": 0.0,
        "public_support_abs_diff_mm": 0.0,
        "single_mode_identity_abs_diff_mm": 0.0,
    }
    mismatch_count = 0
    for expected in freeze["contexts"]:
        key = (
            expected["scan"],
            expected["frame_id"],
            tuple(expected["member_object_ids"]),
        )
        actual = production_contexts.get(key)
        row: dict[str, Any] = {
            "scan": expected["scan"],
            "frame_id": expected["frame_id"],
            "member_object_ids": expected["member_object_ids"],
            "expected_mode_count": expected["mode_count"],
            "actual_mode_count": actual["retained_mode_count"] if actual else None,
            "expected_support_mm": expected["outputs_mm"]["W4"],
            "actual_support_mm": actual["support_mm"] if actual else None,
            "pass": False,
        }
        if actual is None or actual["retained_mode_count"] != expected["mode_count"]:
            mismatch_count += 1
            context_rows.append(row)
            continue
        center_diff = max(
            abs(float(left["center_mm"]) - float(right["center_mm"]))
            for left, right in zip(actual["retained_modes"], expected["modes"])
        )
        probability_diff = max(
            abs(float(left["probability"]) - float(right["probability"]))
            for left, right in zip(actual["retained_modes"], expected["modes"])
        )
        support_diff = abs(float(actual["support_mm"]) - float(expected["outputs_mm"]["W4"]))
        public_diffs = [
            abs(
                float(
                    production_objects[(expected["scan"], expected["frame_id"], object_id)][
                        "geometry"
                    ]["support_height_mm"]
                )
                - float(expected["outputs_mm"]["W4"])
            )
            for object_id in expected["member_object_ids"]
        ]
        public_diff = max(public_diffs)
        identity_diff = (
            abs(float(actual["support_mm"]) - float(actual["retained_modes"][0]["center_mm"]))
            if expected["mode_count"] == 1
            else 0.0
        )
        maxima["mode_center_abs_diff_mm"] = max(maxima["mode_center_abs_diff_mm"], center_diff)
        maxima["mode_probability_abs_diff"] = max(maxima["mode_probability_abs_diff"], probability_diff)
        maxima["diagnostic_support_abs_diff_mm"] = max(maxima["diagnostic_support_abs_diff_mm"], support_diff)
        maxima["public_support_abs_diff_mm"] = max(maxima["public_support_abs_diff_mm"], public_diff)
        maxima["single_mode_identity_abs_diff_mm"] = max(maxima["single_mode_identity_abs_diff_mm"], identity_diff)
        row.update(
            {
                "mode_center_max_abs_diff_mm": center_diff,
                "mode_probability_max_abs_diff": probability_diff,
                "support_abs_diff_mm": support_diff,
                "public_support_max_abs_diff_mm": public_diff,
                "single_mode_identity_abs_diff_mm": identity_diff,
                "pass": max(center_diff, probability_diff, support_diff, public_diff, identity_diff)
                <= TOLERANCE_MM,
            }
        )
        mismatch_count += int(not row["pass"])
        context_rows.append(row)

    object_rows = []
    object_mismatch_count = 0
    class_mismatch_count = 0
    for expected in evaluation["object_rows"]:
        obj = production_objects.get(
            (expected["scan"], expected["frame_id"], expected["object_id"])
        )
        actual = (
            float(obj["geometry"]["support_height_mm"])
            if obj is not None and obj["numeric_available"]
            else None
        )
        difference = (
            abs(actual - float(expected["outputs_mm"]["W4"])) if actual is not None else None
        )
        class_match = obj is not None and obj["class_name"] == expected["class_name"]
        passed = difference is not None and difference <= TOLERANCE_MM and class_match
        object_mismatch_count += int(not passed)
        class_mismatch_count += int(not class_match)
        object_rows.append(
            {
                **{key: expected[key] for key in ("scan", "frame_id", "object_id", "class_name", "layer")},
                "expected_w4_support_mm": expected["outputs_mm"]["W4"],
                "production_support_mm": actual,
                "abs_diff_mm": difference,
                "class_match": class_match,
                "pass": passed,
            }
        )

    metrics = {
        "CORE_OBJECTS": scope_metrics(object_rows, True),
        "ALL_OBJECTS": scope_metrics(object_rows, False),
    }
    frozen_core = evaluation["scopes"]["CORE_OBJECTS"]["W4"]
    metric_diffs = {
        name: abs(float(metrics["CORE_OBJECTS"][name]) - float(frozen_core[name]))
        for name in (
            "mean_scan_lower_mad_mm",
            "mean_scan_lower_std_mm",
            "mean_scan_lower_span_mm",
            "gt50_count",
            "gt75_count",
            "gt100_count",
            "numeric_count",
        )
    }
    metrics_match = max(metric_diffs.values()) <= TOLERANCE_MM

    focus = []
    for frame in documents["SCAN_085223"]["frames"]:
        matching = []
        for obj in frame["objects"]:
            diagnostic = (
                obj.get("diagnostics", {})
                .get("generic_support", {})
                .get("diagnostics", {})
                .get("m3_w4")
            )
            if diagnostic and "sports ball" in diagnostic["member_classes"]:
                matching.append((obj, diagnostic))
        obj, diagnostic = matching[0]
        focus.append(
            {
                "sequence": frame["sequence"],
                "frame_id": frame["frame_id"],
                "support_mm": diagnostic["support_mm"],
                "mode_count": diagnostic["retained_mode_count"],
                "mode_centers_mm": [row["center_mm"] for row in diagnostic["retained_modes"]],
                "mode_probabilities": [row["probability"] for row in diagnostic["retained_modes"]],
                "geometry_status": obj["geometry_status"],
                "numeric_available": obj["numeric_available"],
            }
        )
    focus.sort(key=lambda row: row["sequence"])
    focus_stats = stats([row["support_mm"] for row in focus], 725.0)

    parity_pass = (
        mismatch_count == 0
        and object_mismatch_count == 0
        and len(production_contexts) == len(freeze["contexts"])
        and all_object_checks["unavailable_count"] == 0
        and all_object_checks["non_finite_count"] == 0
        and all_object_checks["non_positive_extent_count"] == 0
        and all_object_checks["missing_m3_w4_diagnostic_count"] == 0
        and metrics_match
    )
    parity = {
        "schema_version": "m3_w4_research_to_production_parity.v1",
        "tolerance_mm": TOLERANCE_MM,
        "fresh_production_replay_count": len(documents),
        "expected_context_count": len(freeze["contexts"]),
        "production_context_count": len(production_contexts),
        "context_mismatch_count": mismatch_count,
        "expected_object_count": len(evaluation["object_rows"]),
        "object_mismatch_count": object_mismatch_count,
        "class_mismatch_count": class_mismatch_count,
        "maxima": maxima,
        "all_object_checks": all_object_checks,
        "core_metric_differences": metric_diffs,
        "m3_mode_generation_research_parity": mismatch_count == 0,
        "w4_research_parity": object_mismatch_count == 0,
        "core_metrics_match_frozen_w4": metrics_match,
        "six_scan_production_replay_pass": parity_pass,
        "contexts": context_rows,
        "objects": object_rows,
    }
    write_json(WORKSPACE / "PRODUCTION_REPLAY_MANIFEST.json", replay_manifest)
    write_json(WORKSPACE / "M3_W4_RESEARCH_TO_PRODUCTION_PARITY.json", parity)
    write_json(WORKSPACE / "SIX_SCAN_METRICS.json", metrics)
    write_json(WORKSPACE / "SCAN_085223_11_FRAME_TABLE.json", {"frames": focus, "stats": focus_stats})

    (WORKSPACE / "M3_W4_RESEARCH_TO_PRODUCTION_PARITY.md").write_text(
        "# M3 + W4 research-to-production parity\n\n"
        f"- Fresh production replays: {len(documents)}\n"
        f"- Contexts: {len(production_contexts)}/{len(freeze['contexts'])}\n"
        f"- Context mismatches: {mismatch_count}\n"
        f"- Object mismatches: {object_mismatch_count}\n"
        f"- Maximum mode-center difference: {maxima['mode_center_abs_diff_mm']:.12g} mm\n"
        f"- Maximum mode-probability difference: {maxima['mode_probability_abs_diff']:.12g}\n"
        f"- Maximum W4/public-support difference: {maxima['public_support_abs_diff_mm']:.12g} mm\n"
        f"- Strict tolerance: {TOLERANCE_MM:g} mm\n"
        f"- M3 mode parity: **{mismatch_count == 0}**\n"
        f"- W4 output parity: **{object_mismatch_count == 0}**\n"
        f"- Six-scan replay gate: **{parity_pass}**\n",
        encoding="utf-8",
    )
    lines = [
        "# SCAN_085223 production M3 + W4, 11-frame focus",
        "",
        "| seq | frame | modes | support mm | status |",
        "|---:|---|---:|---:|---|",
    ]
    lines.extend(
        f"| {row['sequence']} | {row['frame_id']} | {row['mode_count']} | {row['support_mm']:.6f} | {row['geometry_status']} |"
        for row in focus
    )
    lines.extend(
        [
            "",
            f"Median: {focus_stats['median_mm']:.6f} mm; MAD: {focus_stats['mad_mm']:.6f} mm; "
            f"span: {focus_stats['span_mm']:.6f} mm; >100: {focus_stats['gt100_count']}.",
        ]
    )
    (WORKSPACE / "SCAN_085223_11_FRAME_TABLE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "pass": parity_pass,
                "context_mismatches": mismatch_count,
                "object_mismatches": object_mismatch_count,
                "maxima": maxima,
                "core": {
                    key: metrics["CORE_OBJECTS"][key]
                    for key in (
                        "mean_scan_lower_mad_mm",
                        "mean_scan_lower_span_mm",
                        "gt50_count",
                        "gt75_count",
                        "gt100_count",
                        "numeric_count",
                    )
                },
                "focus_085223": focus_stats,
            },
            indent=2,
        )
    )
    return 0 if parity_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
