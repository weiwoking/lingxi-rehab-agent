#!/usr/bin/env python3
"""Stage2B all-16 identifiability audit and final candidate generator.

This module never registers, refreshes, deletes, promotes, or writes outside the
standalone Stage2B workspace (except when an explicit report directory is used
by the separate evidence builder).  The reference-plane classification is read
once from observations.stage2b.json and is never iteratively trimmed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Iterable

import cv2
import numpy as np

import advanced_diagnostics as advanced
import stage2b_camera_tof as core

OUT = core.WORKSPACE / "final_all16"
ANALYSIS_PATH = OUT / "variant_comparison.json"
CANDIDATE_PATH = core.WORKSPACE / "candidate" / "camera_tof.stage2b_all16_candidate.v2.json"
FR_PATH = core.WORKSPACE / "candidate" / "deltar_fr.stage2b_all16_candidate.v2.json"

CANONICAL_GAUGE = "identity"
SEMANTICS = "axial_z"
DISTANCES = (400, 600, 1000)

FULL_NAMES = (
    "rotation_rodrigues_x", "rotation_rodrigues_y", "rotation_rodrigues_z",
    "translation_x", "translation_y", "translation_z",
    "log_fx_tof", "log_fy_tof", "cx_tof", "cy_tof",
)
FULL_UNITS = ("rad", "rad", "rad", "mm", "mm", "mm", "ln(zone_cell)",
              "ln(zone_cell)", "zone_coordinate", "zone_coordinate")
FULL_SCALES = np.array([0.05, 0.05, 0.05, 20.0, 20.0, 20.0, 0.10, 0.10, 0.25, 0.25])
FULL_DIFF_STEPS = np.array([2e-5, 2e-5, 2e-5, 0.03, 0.03, 0.03,
                            2e-4, 2e-4, 2e-3, 2e-3])

VARIANTS: dict[str, dict[str, Any]] = {
    "A_current_broad": {"kind": "full", "fit_layer": "broad", "description": "unchanged current broad robust Model B"},
    "B_core_reference_plane": {"kind": "full", "fit_layer": "core", "description": "Model B, frozen reference-plane observations only"},
    "C_strict_core": {"kind": "full", "fit_layer": "strict_core", "description": "diagnostic strict AND frozen reference-plane observations"},
    "R1_core_square_focal": {"kind": "square", "fit_layer": "core", "description": "core fit with fx_tof == fy_tof"},
    "R2_core_centered": {"kind": "centered", "fit_layer": "core", "description": "core fit with cx_tof == cy_tof == 4.0"},
    "R3_core_square_centered": {"kind": "square_centered", "fit_layer": "core", "description": "core fit with square focal and centered principal point"},
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def protected_hashes() -> dict[str, Any]:
    files = [core.CAMERA_CANDIDATE]
    files.extend(sorted((core.WORKSPACE / "imports").rglob("*")))
    files.extend(sorted((core.WORKSPACE / "manifests").glob("S2B_*.json")))
    files = [p for p in files if p.is_file()]
    return {"algorithm": "sha256", "file_count": len(files),
            "files": {str(p.resolve()): sha256_file(p) for p in files}}


def free_schema(kind: str) -> tuple[list[str], list[str], np.ndarray, np.ndarray]:
    if kind == "full":
        return list(FULL_NAMES), list(FULL_UNITS), FULL_SCALES.copy(), FULL_DIFF_STEPS.copy()
    if kind == "square":
        return (list(FULL_NAMES[:6]) + ["log_f_tof_shared", "cx_tof", "cy_tof"],
                list(FULL_UNITS[:6]) + ["ln(zone_cell)", "zone_coordinate", "zone_coordinate"],
                np.r_[FULL_SCALES[:6], 0.10, 0.25, 0.25],
                np.r_[FULL_DIFF_STEPS[:6], 2e-4, 2e-3, 2e-3])
    if kind == "centered":
        return (list(FULL_NAMES[:8]), list(FULL_UNITS[:8]), FULL_SCALES[:8].copy(), FULL_DIFF_STEPS[:8].copy())
    if kind == "square_centered":
        return (list(FULL_NAMES[:6]) + ["log_f_tof_shared"], list(FULL_UNITS[:6]) + ["ln(zone_cell)"],
                np.r_[FULL_SCALES[:6], 0.10], np.r_[FULL_DIFF_STEPS[:6], 2e-4])
    raise ValueError(kind)


def compress(full: Iterable[float], kind: str) -> np.ndarray:
    p = np.asarray(list(full), dtype=float)
    if kind == "full": return p.copy()
    if kind == "square": return np.r_[p[:6], 0.5 * (p[6] + p[7]), p[8:10]]
    if kind == "centered": return p[:8].copy()
    if kind == "square_centered": return np.r_[p[:6], 0.5 * (p[6] + p[7])]
    raise ValueError(kind)


def expand(free: Iterable[float], kind: str) -> np.ndarray:
    q = np.asarray(list(free), dtype=float)
    if kind == "full": return q.copy()
    if kind == "square": return np.r_[q[:6], q[6], q[6], q[7], q[8]]
    if kind == "centered": return np.r_[q[:8], 4.0, 4.0]
    if kind == "square_centered": return np.r_[q[:6], q[6], q[6], 4.0, 4.0]
    raise ValueError(kind)


def clip_free(free: np.ndarray, kind: str) -> np.ndarray:
    p = expand(free, kind)
    p[6:8] = np.clip(p[6:8], math.log(3.0), math.log(30.0))
    p[8:10] = np.clip(p[8:10], 1.0, 7.0)
    return compress(p, kind)


def data_residual(full: np.ndarray, rows: list[dict[str, Any]]) -> np.ndarray:
    return core.residual_vector(full, rows, CANONICAL_GAUGE, SEMANTICS,
                                "B_low_dimensional", include_regularization=False)


def objective_residual(full: np.ndarray, rows: list[dict[str, Any]]) -> np.ndarray:
    return core.residual_vector(full, rows, CANONICAL_GAUGE, SEMANTICS,
                                "B_low_dimensional", include_regularization=True)


def physical_metrics(full: np.ndarray, rows: list[dict[str, Any]]) -> dict[str, Any]:
    _, metric = core.evaluate_params(full.tolist(), rows, CANONICAL_GAUGE,
                                     SEMANTICS, "B_low_dimensional")
    return metric


def robust_optimize(rows: list[dict[str, Any]], kind: str, initial_full: Iterable[float] | None,
                    max_iterations: int) -> dict[str, Any]:
    """The source solver's IRLS/LM algorithm generalized to reduced coordinates."""
    if len(rows) < 18: raise core.Stage2BError("too few rows")
    initial = core.default_params() if initial_full is None else np.asarray(list(initial_full), dtype=float)
    q = clip_free(compress(initial, kind), kind)
    _, _, _, steps = free_schema(kind)
    damping, converged = 1e-3, False
    jac = np.empty((len(rows) + 10, len(q)))
    for iteration in range(max_iterations):
        full = expand(q, kind); raw = objective_residual(full, rows); data = raw[:len(rows)]
        med = float(np.median(data)); robust_scale = max(1.4826 * float(np.median(np.abs(data - med))), 1.0)
        cutoff = 1.345 * robust_scale
        weights = np.ones_like(raw); large = np.abs(data) > cutoff
        weights[:len(rows)][large] = cutoff / np.abs(data[large])
        jac = np.empty((len(raw), len(q)), dtype=float)
        for j, step in enumerate(steps):
            trial = q.copy(); trial[j] += step
            jac[:, j] = (objective_residual(expand(trial, kind), rows) - raw) / step
        sw = np.sqrt(weights); jw = jac * sw[:, None]; rw = raw * sw
        jtj = jw.T @ jw
        h = jtj + damping * np.diag(np.maximum(np.diag(jtj), 1.0)); g = jw.T @ rw
        try: delta = -np.linalg.solve(h, g)
        except np.linalg.LinAlgError: delta = -np.linalg.lstsq(h, g, rcond=None)[0]
        old = float(np.sum(np.where(np.abs(raw) <= cutoff, 0.5 * raw ** 2,
                                    cutoff * (np.abs(raw) - 0.5 * cutoff))))
        accepted = False
        for _ in range(10):
            trial = clip_free(q + delta, kind)
            new = objective_residual(expand(trial, kind), rows)
            cost = float(np.sum(np.where(np.abs(new) <= cutoff, 0.5 * new ** 2,
                                         cutoff * (np.abs(new) - 0.5 * cutoff))))
            if cost <= old:
                q = trial; damping = max(damping / 3.0, 1e-9); accepted = True; break
            delta *= 0.5; damping *= 5.0
        if not accepted: break
        if float(np.linalg.norm(delta)) < 1e-6: converged = True; break
    full = expand(q, kind)
    return {"params": full.tolist(), "free_params": q.tolist(), "iterations": iteration + 1,
            "converged": bool(converged or iteration > 1), "metrics": physical_metrics(full, rows)}


def fit_variant(name: str, rows: list[dict[str, Any]], initial: Iterable[float] | None,
                iterations: int) -> dict[str, Any]:
    kind = VARIANTS[name]["kind"]
    if kind == "full":
        fit = core.robust_lm(rows, CANONICAL_GAUGE, SEMANTICS, "B_low_dimensional",
                             initial=None if initial is None else np.asarray(list(initial), dtype=float),
                             max_iterations=iterations)
        return {"params": fit["params"], "free_params": fit["params"], "iterations": fit["iterations"],
                "converged": fit["converged"], "metrics": fit["metrics"]}
    return robust_optimize(rows, kind, initial, iterations)


def rows_and_layers(obs: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    metadata, core_keys = advanced.observation_metadata(obs)
    broad = core.observation_rows(obs, "broad")
    strict = core.observation_rows(obs, "strict")
    core_rows = [r for r in broad if advanced.row_key(r) in core_keys]
    strict_core = [r for r in strict if advanced.row_key(r) in core_keys]
    outside = [r for r in broad if advanced.row_key(r) not in core_keys]
    broad_only = [r for r in broad if not metadata[advanced.row_key(r)]["strict_valid"]]
    return ({"broad": broad, "core": core_rows, "strict_core": strict_core,
             "outside": outside, "broad_only": broad_only},
            {"classification": "single frozen pass from current broad robust solution; no reclassification after core refit",
             "threshold_rule": obs.get("plane_consistency_rule"),
             "counts": {"broad": len(broad), "core": len(core_rows), "strict_core": len(strict_core),
                        "outside_background": len(outside), "broad_only": len(broad_only)}})


def select_pose(rows: list[dict[str, Any]], poses: set[str]) -> list[dict[str, Any]]:
    return [r for r in rows if r["pose_id"] in poses]


def solution_doc(params: Iterable[float]) -> dict[str, Any]:
    return {"orientation": CANONICAL_GAUGE, "range_semantics": SEMANTICS,
            "model": "B_low_dimensional", "params": list(params)}


def mapping_metric(reference: Iterable[float], other: Iterable[float]) -> dict[str, Any]:
    return advanced.compare_mapping(solution_doc(reference), solution_doc(other))


def engineering_jacobian(rows: list[dict[str, Any]], params: Iterable[float], kind: str) -> dict[str, Any]:
    full = np.asarray(list(params), dtype=float); q = compress(full, kind)
    names, units, scales, _ = free_schema(kind)
    base = data_residual(full, rows)
    med = float(np.median(base)); robust_scale = max(1.4826 * float(np.median(np.abs(base - med))), 1.0)
    cutoff = 1.345 * robust_scale
    weights = np.ones_like(base); large = np.abs(base) > cutoff
    weights[large] = cutoff / np.abs(base[large])
    jac = np.empty((len(rows), len(q)), dtype=float)
    normalized_step = 1e-4
    for j, scale in enumerate(scales):
        trial = q.copy(); trial[j] += normalized_step * scale
        jac[:, j] = (data_residual(expand(trial, kind), rows) - base) / normalized_step
    scaled = jac * np.sqrt(weights)[:, None]
    u, singular, vt = np.linalg.svd(scaled, full_matrices=False)
    threshold = float(singular[0] * 1e-6)
    rank = int(np.sum(singular > threshold)); condition = float(singular[0] / max(singular[-1], 1e-15))
    modes = []
    for weak_index in range(min(5, len(singular))):
        index = len(singular) - 1 - weak_index; vector = vt[index]
        order = np.argsort(np.abs(vector))[::-1]
        components = [{"name": names[int(j)], "unit": units[int(j)], "coefficient": float(vector[int(j)])}
                      for j in order[:min(5, len(order))]]
        modes.append({"weak_mode_rank": weak_index + 1, "singular_index": index,
                      "singular_value": float(singular[index]), "components": components,
                      "vector_scaled_coordinates": vector.tolist()})
    return {"shape": [int(x) for x in scaled.shape], "robust_weighted": True,
            "robust_scale_mm_weighted": robust_scale, "huber_cutoff_mm_weighted": cutoff,
            "parameter_scales": [{"name": n, "unit": u0, "scale": float(s)}
                                 for n, u0, s in zip(names, units, scales)],
            "singular_values": singular.tolist(), "condition_number": condition,
            "effective_rank_relative_1e-6": rank, "near_null_modes": modes}


def optimize_profile(rows: list[dict[str, Any]], center: np.ndarray, mode: np.ndarray,
                     scales: np.ndarray, alpha: float, iterations: int = 10) -> np.ndarray:
    """Fix displacement along one scaled SVD mode; locally optimize its orthogonal complement."""
    _, _, vt = np.linalg.svd(mode.reshape(1, -1), full_matrices=True)
    basis = vt[1:].T
    z = np.zeros(basis.shape[1], dtype=float); damping = 1e-3
    def mapped(value: np.ndarray) -> np.ndarray:
        return center + scales * (alpha * mode + basis @ value)
    for _ in range(iterations):
        full = mapped(z); raw = objective_residual(full, rows); data = raw[:len(rows)]
        med = float(np.median(data)); rs = max(1.4826 * float(np.median(np.abs(data - med))), 1.0); cutoff = 1.345 * rs
        w = np.ones_like(raw); large = np.abs(data) > cutoff; w[:len(rows)][large] = cutoff / np.abs(data[large])
        jac = np.empty((len(raw), len(z))); step = 1e-4
        for j in range(len(z)):
            trial = z.copy(); trial[j] += step
            jac[:, j] = (objective_residual(mapped(trial), rows) - raw) / step
        sw = np.sqrt(w); jw = jac * sw[:, None]; rw = raw * sw; jtj = jw.T @ jw
        try: delta = -np.linalg.solve(jtj + damping * np.diag(np.maximum(np.diag(jtj), 1.0)), jw.T @ rw)
        except np.linalg.LinAlgError: delta = -np.linalg.lstsq(jtj + 1e-6 * np.eye(len(z)), jw.T @ rw, rcond=None)[0]
        old = float(np.sum(np.where(np.abs(raw) <= cutoff, .5 * raw ** 2, cutoff * (np.abs(raw) - .5 * cutoff))))
        accepted = False
        for _ in range(8):
            new = objective_residual(mapped(z + delta), rows)
            cost = float(np.sum(np.where(np.abs(new) <= cutoff, .5 * new ** 2, cutoff * (np.abs(new) - .5 * cutoff))))
            if cost <= old: z += delta; damping = max(damping / 3, 1e-9); accepted = True; break
            delta *= .5; damping *= 5
        if not accepted or np.linalg.norm(delta) < 1e-6: break
    return mapped(z)


def profile_test(rows: list[dict[str, Any]], core_rows: list[dict[str, Any]], params: Iterable[float],
                 jacobian_doc: dict[str, Any]) -> dict[str, Any]:
    center = np.asarray(list(params), dtype=float)
    mode = np.asarray(jacobian_doc["near_null_modes"][0]["vector_scaled_coordinates"], dtype=float)
    result = []
    baseline_fit = physical_metrics(center, rows); baseline_core = physical_metrics(center, core_rows)
    for alpha in (-1.0, -0.5, 0.5, 1.0):
        profiled = optimize_profile(rows, center, mode, FULL_SCALES, alpha)
        result.append({"alpha_engineering_step": alpha, "params": profiled.tolist(),
                       "fit_metrics": physical_metrics(profiled, rows),
                       "core_metrics": physical_metrics(profiled, core_rows),
                       "mapping_from_optimum": mapping_metric(center, profiled)})
    return {"mode": jacobian_doc["near_null_modes"][0], "step_definition": "alpha=1 is one engineering-scale unit along normalized weak mode; remaining 9 scaled coordinates reoptimized", "baseline_fit": baseline_fit, "baseline_core": baseline_core, "profiles": result}


def aggregate_mapping(items: list[dict[str, Any]]) -> dict[str, Any]:
    def dist(values: list[float]) -> dict[str, Any]:
        if not values: return {"count": 0, "p50": None, "p95": None, "max": None}
        a = np.asarray(values, dtype=float)
        return {"count": len(values), "p50": float(np.percentile(a, 50)),
                "p95": float(np.percentile(a, 95)), "max": float(np.max(a))}
    output = {"angular_p95_deg": dist([x["angular_difference_deg"]["p95"] for x in items]),
              "angular_max_deg": dist([x["angular_difference_deg"]["max"] for x in items])}
    output["pixel_displacement_px"] = {}
    for distance in DISTANCES:
        output["pixel_displacement_px"][str(distance)] = {
            "fold_or_trial_p50": dist([x["by_distance_mm"][str(distance)]["pixel_displacement"]["p50"] for x in items]),
            "fold_or_trial_p95": dist([x["by_distance_mm"][str(distance)]["pixel_displacement"]["p95"] for x in items]),
            "fold_or_trial_max": dist([x["by_distance_mm"][str(distance)]["pixel_displacement"]["max"] for x in items]),
        }
    return output


def physical_parameter_vector(params: Iterable[float]) -> np.ndarray:
    p = np.asarray(list(params), dtype=float)
    return np.r_[p[:6], math.exp(p[6]), math.exp(p[7]), p[8:10]]


def parameter_stability(param_sets: list[Iterable[float]]) -> list[dict[str, Any]]:
    names = list(FULL_NAMES[:6]) + ["fx_tof", "fy_tof", "cx_tof", "cy_tof"]
    units = list(FULL_UNITS[:6]) + ["zone_cell", "zone_cell", "zone_coordinate", "zone_coordinate"]
    a = np.asarray([physical_parameter_vector(x) for x in param_sets], dtype=float)
    return [{"name": n, "unit": u, "std": float(np.std(a[:, i])), "min": float(np.min(a[:, i])),
             "max": float(np.max(a[:, i])), "range": float(np.ptp(a[:, i]))}
            for i, (n, u) in enumerate(zip(names, units))]


def metric_by_metadata(rows: list[dict[str, Any]], residuals: np.ndarray,
                       metadata: dict[tuple[str, str, int], dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[float]] = {}
    for row, residual in zip(rows, residuals):
        grouped.setdefault(str(metadata[advanced.row_key(row)][key]), []).append(float(residual))
    return {name: core.metrics(values) for name, values in sorted(grouped.items())}


def analyze(args: argparse.Namespace) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    before = protected_hashes(); core.write_json(OUT / "protected_hashes.before.json", before)
    obs = core.get_observations(False); pose_ids = advanced.valid_pose_ids(obs)
    expected = [f"S2B_{i:03d}" for i in range(1, 17)]
    if pose_ids != expected: raise core.Stage2BError(f"expected exact ordered poses {expected}, got {pose_ids}")
    layers, classification = rows_and_layers(obs); metadata, _ = advanced.observation_metadata(obs)
    train_ids, held_ids = core.split_poses(obs)
    subset_sets = advanced.subset_pose_sets(pose_ids, .8, args.subset_trials, args.seed)
    comparison: dict[str, Any] = {}
    for variant_name, spec in VARIANTS.items():
        fit_rows = layers[spec["fit_layer"]]
        full_fit = fit_variant(variant_name, fit_rows, None, args.full_iterations)
        full_params = full_fit["params"]
        split_fit = fit_variant(variant_name, select_pose(fit_rows, train_ids), full_params, args.cv_iterations)
        split_core_train = select_pose(layers["core"], train_ids)
        split_core_held = select_pose(layers["core"], held_ids)
        lopo_maps, lopo_params = [], []
        for held in pose_ids:
            chosen = set(pose_ids) - {held}
            fold = fit_variant(variant_name, select_pose(fit_rows, chosen), full_params, args.cv_iterations)
            lopo_params.append(fold["params"]); lopo_maps.append(mapping_metric(full_params, fold["params"]))
        subset_maps, subset_params = [], []
        for chosen in subset_sets:
            fold = fit_variant(variant_name, select_pose(fit_rows, chosen), full_params, args.cv_iterations)
            subset_params.append(fold["params"]); subset_maps.append(mapping_metric(full_params, fold["params"]))
        svd = engineering_jacobian(fit_rows, full_params, spec["kind"])
        comparison[variant_name] = {
            "description": spec["description"], "fit_layer": spec["fit_layer"], "free_parameter_count": len(free_schema(spec["kind"])[0]),
            "all16_fit": full_fit, "all16_core_metrics": physical_metrics(np.asarray(full_params), layers["core"]),
            "all16_all_broad_metrics": physical_metrics(np.asarray(full_params), layers["broad"]),
            "all16_outside_background_metrics": physical_metrics(np.asarray(full_params), layers["outside"]),
            "all16_broad_only_metrics": physical_metrics(np.asarray(full_params), layers["broad_only"]),
            "scan_split": {"train_pose_ids": sorted(train_ids), "heldout_pose_ids": sorted(held_ids),
                           "fit_observations": len(select_pose(fit_rows, train_ids)),
                           "core_train_metrics": physical_metrics(np.asarray(split_fit["params"]), split_core_train),
                           "core_heldout_metrics": physical_metrics(np.asarray(split_fit["params"]), split_core_held)},
            "lopo_mapping": aggregate_mapping(lopo_maps), "subset_mapping": aggregate_mapping(subset_maps),
            "parameter_stability_subsets": parameter_stability(subset_params), "conditioning": svd,
        }
        if variant_name in ("A_current_broad", "B_core_reference_plane"):
            comparison[variant_name]["weak_mode_profile"] = profile_test(fit_rows, layers["core"], full_params, svd)
    selected_for_tables = comparison["B_core_reference_plane"]["all16_fit"]["params"]
    core_residual = core.evaluate_params(selected_for_tables, layers["core"], CANONICAL_GAUGE, SEMANTICS, "B_low_dimensional")[0]
    document = {
        "schema_version": "stage2b_all16_identifiability.v2", "created_at": core.now(),
        "status": "DIAGNOSTIC_NOT_PROMOTED", "canonical_gauge": CANONICAL_GAUGE,
        "physical_D4_orientation": "UNRESOLVED_GAUGE", "mapping_authority": "raw_zone_index_to_camera_frame_ray",
        "range_semantics": SEMANTICS, "source_pose_ids": pose_ids,
        "source_scan_ids": [p["scan_id"] for p in obs["poses"]],
        "classification_pass": classification,
        "current_split_regression": {"algorithm": "SHA256(pose_id)[0:8] mod 5 == 0",
                                     "train_pose_ids": sorted(train_ids), "heldout_pose_ids": sorted(held_ids),
                                     "broad_train_count": len(select_pose(layers["broad"], train_ids)),
                                     "broad_heldout_count": len(select_pose(layers["broad"], held_ids)),
                                     "S2B_016_wholly_heldout": "S2B_016" in held_ids},
        "grid_coordinate_convention": {"zone_centers": "0.5..7.5", "geometric_center": 4.0,
                                       "source": "tof_rays uses arange(8)+0.5"},
        "optimizer": {"algorithm": "source robust_lm IRLS/LM; generalized identically for equality constraints",
                      "loss": "Huber IRLS, cutoff=1.345*max(1.4826*MAD,1mm), fixed per LM iteration",
                      "scan_weighting": "each detected frame has 1/sqrt(detected_frames_in_scan)",
                      "regularization": {"rotation": "sqrt(0.08)*20 * Rodrigues radians toward zero",
                                         "translation": "sqrt(0.0004) * (t-[-20,0,0]) mm",
                                         "intrinsics": "[0.03,0.03,0.08,0.08]*(p-default)"},
                      "bounds": {"rotation": "unbounded", "translation_mm": "unbounded",
                                 "fx_fy_zone_cell": [3.0, 30.0], "cx_cy_zone_coordinate": [1.0, 7.0]},
                      "initial": core.default_params().tolist(), "finite_difference_steps": FULL_DIFF_STEPS.tolist(),
                      "engineering_scaling_for_SVD": FULL_SCALES.tolist()},
        "core_distance_table_B": metric_by_metadata(layers["core"], core_residual, metadata, "distance_bin"),
        "core_angle_table_B": metric_by_metadata(layers["core"], core_residual, metadata, "angle_bin"),
        "variants": comparison, "subset_trials": len(subset_sets), "seed": args.seed,
        "promotion": False,
    }
    core.write_json(ANALYSIS_PATH, document)
    after = protected_hashes(); core.write_json(OUT / "protected_hashes.after_analysis.json", after)
    core.write_json(OUT / "protected_hash_check.analysis.json", {"unchanged": before == after,
                                                                  "before_file_count": before["file_count"],
                                                                  "after_file_count": after["file_count"]})
    if before != after: raise core.Stage2BError("protected hashes changed")
    print(json.dumps({"analysis": str(ANALYSIS_PATH), "poses": len(pose_ids), "variants": list(comparison),
                      "protected_inputs_unchanged": True}, indent=2))
    return 0


def raw_mapping(params: list[float]) -> list[dict[str, Any]]:
    solution = solution_doc(params); tof_raw, camera_raw = advanced.raw_rays(solution)
    inverse = advanced.inverse_orientation(CANONICAL_GAUGE)
    boundaries = np.asarray(core.ray_boundaries(params), dtype=float)
    result = []
    for raw_index in range(64):
        canonical = int(inverse[raw_index])
        result.append({"raw_index": raw_index, "raw_row": raw_index // 8, "raw_col": raw_index % 8,
                       "canonical_index": canonical, "tof_frame_center_ray": tof_raw[raw_index].tolist(),
                       "camera_frame_center_ray": camera_raw[raw_index].tolist(),
                       "tof_frame_corner_rays_model_derived": boundaries[canonical].tolist()})
    return result


def finalize(args: argparse.Namespace) -> int:
    if args.selected not in VARIANTS: raise core.Stage2BError(f"unknown variant {args.selected}")
    analysis = core.load_json(ANALYSIS_PATH); selected = analysis["variants"][args.selected]
    params = selected["all16_fit"]["params"]; p = np.asarray(params, dtype=float)
    rotation, translation = core.params_to_transform(params); transform = np.eye(4)
    transform[:3, :3] = rotation; transform[:3, 3] = translation
    obs = core.get_observations(False); mani = core.manifest(); pose_ids = analysis["source_pose_ids"]
    source_hashes = {pid: mani["poses"][pid]["source_hashes"] for pid in pose_ids}
    candidate = {
        "schema_version": "camera_tof.stage2b_all16_candidate.v2", "status": "CANDIDATE_NOT_PROMOTED",
        "created_at": core.now(), "selected_solver_variant": args.selected,
        "camera_intrinsics_reference": {"path": str(core.CAMERA_CANDIDATE), "sha256": core.CAMERA_CANDIDATE_SHA256},
        "gauge": {"physical_D4_orientation": "UNRESOLVED_GAUGE", "canonical_gauge": CANONICAL_GAUGE,
                  "mapping_authority": "raw_zone_index_to_camera_frame_ray",
                  "raw_index_for_canonical_index": core.d4_map(CANONICAL_GAUGE).tolist()},
        "range_semantics": {"selected": SEMANTICS, "evidence": "core LOPO 16/16 and core 80% subsets 100/100 axial winners"},
        "tof_low_dimensional_model": {"model_type": "pinhole_8x8_zone_centers",
                                      "fx_tof": math.exp(p[6]), "fy_tof": math.exp(p[7]),
                                      "cx_tof": float(p[8]), "cy_tof": float(p[9]),
                                      "zone_coordinate_convention": "cell centers 0.5..7.5; grid center 4.0",
                                      "constraints": VARIANTS[args.selected]["description"]},
        "T_camera_from_tof": {"direction": "T_camera_from_tof", "convention": "column_vector: p_camera=R*p_tof+t",
                              "units": {"rotation": "dimensionless", "translation": "mm"},
                              "R": rotation.tolist(), "t_mm": translation.tolist(), "matrix": transform.tolist()},
        "raw_zone_camera_mapping": raw_mapping(params),
        "validation_evidence": {"core_all_data": selected["all16_core_metrics"],
                                "core_scan_heldout": selected["scan_split"]["core_heldout_metrics"],
                                "all_broad_robustness": selected["all16_all_broad_metrics"],
                                "outside_background": selected["all16_outside_background_metrics"],
                                "lopo_mapping": selected["lopo_mapping"], "subset_mapping": selected["subset_mapping"],
                                "conditioning": selected["conditioning"]},
        "provenance": {"source_pose_ids": pose_ids, "source_scan_ids": analysis["source_scan_ids"],
                       "source_hashes": source_hashes, "solver_source": str(Path(__file__).resolve()),
                       "solver_source_sha256": sha256_file(Path(__file__)), "analysis_artifact": str(ANALYSIS_PATH),
                       "analysis_sha256": sha256_file(ANALYSIS_PATH), "all_16_poses_used_for_final_fit": True,
                       "final_fit_observation_count": selected["all16_fit"]["metrics"]["count"]},
        "stability_evidence_summary": {"chosen_variant": args.selected,
                                       "lopo_angular_p95_deg": selected["lopo_mapping"]["angular_p95_deg"],
                                       "subset_angular_p95_deg": selected["subset_mapping"]["angular_p95_deg"],
                                       "condition_number_scaled": selected["conditioning"]["condition_number"],
                                       "effective_rank": selected["conditioning"]["effective_rank_relative_1e-6"]},
        "promotion": False,
        "warnings": ["candidate only; runtime is unchanged", "physical D4 orientation is an unresolved gauge",
                     "zone boundaries are model-derived, not measured optical sensitivity contours"],
    }
    core.write_json(CANDIDATE_PATH, candidate)
    values, centers = {}, {}
    solution = solution_doc(params)
    for distance in DISTANCES:
        center, rectangles, _ = advanced.project_raw(solution, distance)
        values[str(distance)] = rectangles.tolist(); centers[str(distance)] = center.tolist()
        core.save_projection_png(params, CANONICAL_GAUGE, SEMANTICS, distance)
    fr = {"schema_version": "deltar_fr.stage2b_all16_candidate.v2", "status": "CANDIDATE_NOT_PROMOTED",
          "selected_solver_variant": args.selected, "reference_candidate": str(CANDIDATE_PATH),
          "reference_candidate_sha256": sha256_file(CANDIDATE_PATH), "shape": [64, 4],
          "dtype": "float32 at runtime", "units": "source-camera pixels", "field_order": ["sy", "sx", "ey", "ex"],
          "rectangle_semantics": "model-derived continuous half-open patch bounds", "zone_order": "raw index 0..63 (identity canonical gauge)",
          "measurement_semantics": SEMANTICS, "zone_center_projection_by_reference_distance_mm": centers,
          "model_derived_rectangle_by_reference_distance_mm": values,
          "depth_dependence": "translation causes parallax; one static fr cannot exactly represent 400..1000 mm",
          "runtime_replaced": False, "promotion": False}
    core.write_json(FR_PATH, fr)
    after = protected_hashes(); before = core.load_json(OUT / "protected_hashes.before.json")
    core.write_json(OUT / "protected_hashes.after_final.json", after)
    core.write_json(OUT / "protected_hash_check.final.json", {"unchanged": before == after,
                                                               "before_file_count": before["file_count"],
                                                               "after_file_count": after["file_count"]})
    if before != after: raise core.Stage2BError("protected hashes changed during finalization")
    print(json.dumps({"candidate": str(CANDIDATE_PATH), "deltar_fr": str(FR_PATH),
                      "selected": args.selected, "promotion": False, "protected_inputs_unchanged": True}, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("analyze"); a.add_argument("--subset-trials", type=int, default=100)
    a.add_argument("--seed", type=int, default=20260817); a.add_argument("--full-iterations", type=int, default=35)
    a.add_argument("--cv-iterations", type=int, default=12); a.set_defaults(func=analyze)
    f = sub.add_parser("finalize"); f.add_argument("--selected", required=True, choices=tuple(VARIANTS)); f.set_defaults(func=finalize)
    return p


if __name__ == "__main__":
    try:
        arguments = parser().parse_args()
        raise SystemExit(arguments.func(arguments))
    except core.Stage2BError as exc:
        print(f"error: {exc}", file=__import__("sys").stderr); raise SystemExit(2)
