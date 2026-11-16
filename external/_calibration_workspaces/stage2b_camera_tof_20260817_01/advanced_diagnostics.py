#!/usr/bin/env python3
"""Read-only Stage2B gauge, semantics, provenance and mapping diagnostics."""
from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np

import stage2b_camera_tof as core

DISTANCES = (400, 600, 1000)
PARAMETERS = (
    ("rotation_rodrigues_x", "rad"), ("rotation_rodrigues_y", "rad"),
    ("rotation_rodrigues_z", "rad"), ("translation_x", "mm"),
    ("translation_y", "mm"), ("translation_z", "mm"),
    ("log_fx_tof", "ln(zone_cell)"), ("log_fy_tof", "ln(zone_cell)"),
    ("cx_tof", "zone_coordinate"), ("cy_tof", "zone_coordinate"),
)


def quantiles(values: Iterable[float]) -> dict[str, Any]:
    a = np.asarray(list(values), dtype=float)
    if not len(a):
        return {"count": 0, "mean": None, "p50": None, "p95": None, "max": None}
    return {"count": int(len(a)), "mean": float(np.mean(a)), "p50": float(np.percentile(a, 50)),
            "p95": float(np.percentile(a, 95)), "max": float(np.max(a))}


def distribution(values: Iterable[float]) -> dict[str, Any]:
    a = np.asarray(list(values), dtype=float)
    if not len(a):
        return {"min": None, "p5": None, "p50": None, "p95": None, "max": None}
    return {"min": float(np.min(a)), "p5": float(np.percentile(a, 5)),
            "p50": float(np.percentile(a, 50)), "p95": float(np.percentile(a, 95)),
            "max": float(np.max(a))}


def winner(axial: float, radial: float, tolerance: float = 1e-9) -> str:
    if abs(axial - radial) <= tolerance:
        return "tie"
    return "axial_z" if axial < radial else "radial_range"


def parameter_document(params: Iterable[float]) -> list[dict[str, Any]]:
    return [{"name": name, "unit": unit, "value": float(value)}
            for (name, unit), value in zip(PARAMETERS, params)]


def inverse_orientation(orientation: str) -> np.ndarray:
    inverse = np.empty(64, dtype=np.int64)
    inverse[core.d4_map(orientation)] = np.arange(64)
    return inverse


def raw_rays(solution: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    params = np.asarray(solution["params"], dtype=float)
    raw_tof = core.tof_rays(params)[inverse_orientation(solution["orientation"])]
    rotation, _ = core.params_to_transform(params.tolist())
    raw_camera = raw_tof @ rotation.T
    raw_camera /= np.linalg.norm(raw_camera, axis=1, keepdims=True)
    return raw_tof, raw_camera


def project_raw(solution: dict[str, Any], distance_mm: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    params = np.asarray(solution["params"], dtype=float)
    rays = core.tof_rays(params); boundaries = np.asarray(core.ray_boundaries(params.tolist()), dtype=float)
    inverse = inverse_orientation(solution["orientation"])
    rotation, translation = core.params_to_transform(params.tolist()); k, d = core.camera_parameters()
    centers, rectangles, camera_points = [], [], []
    for raw_index in range(64):
        canonical = int(inverse[raw_index]); ray = rays[canonical]
        scale = distance_mm if solution["range_semantics"] == "radial_range" else distance_mm / ray[2]
        camera = rotation @ (ray * scale) + translation
        uv, _ = cv2.projectPoints(camera.reshape(1, 3), np.zeros(3), np.zeros(3), k, d)
        centers.append(uv.reshape(2)); camera_points.append(camera)
        corners = []
        for boundary_ray in boundaries[canonical]:
            scale = distance_mm if solution["range_semantics"] == "radial_range" else distance_mm / boundary_ray[2]
            point = rotation @ (boundary_ray * scale) + translation
            buv, _ = cv2.projectPoints(point.reshape(1, 3), np.zeros(3), np.zeros(3), k, d)
            corners.append(buv.reshape(2))
        a = np.asarray(corners)
        rectangles.append([float(a[:, 1].min()), float(a[:, 0].min()),
                           float(a[:, 1].max()), float(a[:, 0].max())])
    return np.asarray(centers), np.asarray(rectangles), np.asarray(camera_points)


def rectangle_iou(a: np.ndarray, b: np.ndarray) -> float:
    sy, sx, ey, ex = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, ey - sy) * max(0.0, ex - sx)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1]); union = area_a + area_b - intersection
    return float(intersection / union) if union > 0 else 0.0


def quadrant(ray: np.ndarray) -> str:
    return f"{'bottom' if ray[1] >= 0 else 'top'}_{'right' if ray[0] >= 0 else 'left'}"


def compare_mapping(a: dict[str, Any], b: dict[str, Any], *, include_rows: bool = False) -> dict[str, Any]:
    _, camera_a = raw_rays(a); _, camera_b = raw_rays(b)
    angular = np.degrees(np.arccos(np.clip(np.sum(camera_a * camera_b, axis=1), -1.0, 1.0)))
    rows = [{"raw_zone_index": i, "raw_row": i // 8, "raw_col": i % 8,
             "camera_quadrant": quadrant(camera_a[i]), "angular_difference_deg": float(angular[i])}
            for i in range(64)]
    by_distance = {}
    for distance in DISTANCES:
        ca, ra, pa = project_raw(a, distance); cb, rb, pb = project_raw(b, distance)
        pixels = np.linalg.norm(ca - cb, axis=1); lateral = np.linalg.norm(pa[:, :2] - pb[:, :2], axis=1)
        centers_a = np.column_stack(((ra[:, 1] + ra[:, 3]) / 2, (ra[:, 0] + ra[:, 2]) / 2))
        centers_b = np.column_stack(((rb[:, 1] + rb[:, 3]) / 2, (rb[:, 0] + rb[:, 2]) / 2))
        footprint_centers = np.linalg.norm(centers_a - centers_b, axis=1); corner_max, ious = [], []
        for i in range(64):
            ac = np.array([[ra[i, 1], ra[i, 0]], [ra[i, 3], ra[i, 0]], [ra[i, 3], ra[i, 2]], [ra[i, 1], ra[i, 2]]])
            bc = np.array([[rb[i, 1], rb[i, 0]], [rb[i, 3], rb[i, 0]], [rb[i, 3], rb[i, 2]], [rb[i, 1], rb[i, 2]]])
            corner_max.append(float(np.max(np.linalg.norm(ac - bc, axis=1)))); ious.append(rectangle_iou(ra[i], rb[i]))
            rows[i].update({f"lateral_separation_mm_{distance}": float(lateral[i]),
                            f"pixel_displacement_{distance}": float(pixels[i]),
                            f"footprint_center_displacement_px_{distance}": float(footprint_centers[i]),
                            f"footprint_corner_max_displacement_px_{distance}": corner_max[-1],
                            f"footprint_iou_{distance}": ious[-1]})
        by_distance[str(distance)] = {"lateral_separation_mm": quantiles(lateral),
                                      "pixel_displacement": quantiles(pixels),
                                      "footprint_center_displacement_px": quantiles(footprint_centers),
                                      "footprint_corner_max_displacement_px": quantiles(corner_max),
                                      "footprint_iou": {**quantiles(ious), "min": float(np.min(ious))}}
    worst = int(np.argmax(angular))
    result = {"angular_difference_deg": quantiles(angular), "worst_raw_zone": worst,
              "worst_camera_quadrant": quadrant(camera_a[worst]), "by_distance_mm": by_distance}
    if include_rows: result["rows"] = rows
    return result


def fit_solution(rows: list[dict[str, Any]], orientation: str, semantics: str,
                 initial: Iterable[float] | None, iterations: int) -> dict[str, Any]:
    fit = core.robust_lm(rows, orientation, semantics, "B_low_dimensional",
                         initial=None if initial is None else np.asarray(list(initial), dtype=float),
                         max_iterations=iterations)
    return {"orientation": orientation, "range_semantics": semantics, "model": "B_low_dimensional",
            "params": fit["params"], "train": fit["metrics"], "rank": fit["rank"],
            "condition_number": fit["condition_number"]}


def valid_pose_ids(obs: dict[str, Any]) -> list[str]:
    return [p["pose_id"] for p in obs["poses"] if any(f["camera_plane"].get("detected") for f in p["frames"])]


def subset_pose_sets(ids: list[str], fraction: float, trials: int, seed: int) -> list[set[str]]:
    target = max(2, int(math.ceil(len(ids) * fraction))) if ids else 0
    if target >= len(ids): return [set(ids)] if ids else []
    count = math.comb(len(ids), target); requested = min(trials, count); rng = np.random.default_rng(seed)
    if count <= 20000:
        choices = list(itertools.combinations(ids, target)); order = rng.permutation(len(choices))[:requested]
        return [set(choices[int(i)]) for i in order]
    result, seen = [], set()
    while len(result) < requested:
        chosen = tuple(sorted(rng.choice(ids, size=target, replace=False).tolist()))
        if chosen not in seen: seen.add(chosen); result.append(set(chosen))
    return result


def observation_metadata(obs: dict[str, Any]) -> tuple[dict[tuple[str, str, int], dict[str, Any]], set[tuple[str, str, int]]]:
    metadata, core_keys = {}, set()
    for pose in obs["poses"]:
        for frame in pose["frames"]:
            plane = frame["camera_plane"]
            if not plane.get("detected"): continue
            n = np.asarray(plane["plane_normal_camera"], dtype=float)
            angle = math.degrees(math.acos(np.clip(abs(n[2]), -1.0, 1.0)))
            reference_distance = abs(float(plane["plane_offset_camera_mm"]))
            distance_bin = "near" if reference_distance < 550 else ("mid" if reference_distance < 850 else "far")
            angle_bin = "low" if angle < 10 else ("medium" if angle < 25 else "high")
            for zone in frame["tof_zones"]:
                key = (pose["pose_id"], frame["frame_id"], int(zone["raw_index"]))
                metadata[key] = {**zone, "pose_id": pose["pose_id"], "frame_id": frame["frame_id"],
                                 "pair_delta_us": frame["pair_delta_us"], "reference_distance_mm": reference_distance,
                                 "plane_angle_deg": angle, "distance_bin": distance_bin, "angle_bin": angle_bin}
                if zone.get("broad_valid") and zone.get("likely_reference_plane") is True: core_keys.add(key)
    return metadata, core_keys


def row_key(row: dict[str, Any]) -> tuple[str, str, int]:
    return row["pose_id"], row["frame_id"], int(row["raw_index"])


def evaluate_layer(solution: dict[str, Any], rows: list[dict[str, Any]],
                   allowed: set[tuple[str, str, int]] | None = None) -> tuple[np.ndarray, dict[str, Any], list[dict[str, Any]]]:
    selected = rows if allowed is None else [row for row in rows if row_key(row) in allowed]
    if not selected:
        return np.asarray([], dtype=float), core.metrics([]), []
    residuals, metric = core.evaluate_params(solution["params"], selected, solution["orientation"],
                                              solution["range_semantics"], solution["model"])
    return residuals, metric, selected


def scan_split(obs: dict[str, Any], pose_filter: set[str] | None = None) -> tuple[set[str], set[str]]:
    ids = [pid for pid in valid_pose_ids(obs) if pose_filter is None or pid in pose_filter]
    held = {pid for pid in ids if int(hashlib.sha256(pid.encode()).hexdigest()[:8], 16) % 5 == 0}
    if len(ids) >= 4 and not held: held = {ids[-1]}
    if len(held) >= len(ids): held = {ids[-1]}
    return set(ids) - held, held


def transform_document(params: Iterable[float]) -> dict[str, Any]:
    p = list(params); rotation, translation = core.params_to_transform(p)
    matrix = np.eye(4); matrix[:3, :3] = rotation; matrix[:3, 3] = translation
    return {"direction": "T_camera_from_tof", "rotation": rotation.tolist(),
            "translation_mm": translation.tolist(), "matrix": matrix.tolist()}


def pairwise_for(rows: list[dict[str, Any]], a: str, b: str) -> dict[str, Any]:
    for row in rows:
        if {row["orientation_a"], row["orientation_b"]} == {a, b}: return row
    raise core.Stage2BError(f"missing gauge pair {a} vs {b}")


def gauge_diagnostics(obs: dict[str, Any], selected: dict[str, Any], iterations: int,
                      pose_filter: set[str] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    train_ids, held_ids = scan_split(obs, pose_filter); train = core.observation_rows(obs, "broad", train_ids)
    held = core.observation_rows(obs, "broad", held_ids) or train; solutions, ranking = {}, []
    for orientation in core.D4:
        # Identical initialization and convergence budget are essential: giving
        # the currently selected gauge a warm start would bias an equivalence test.
        solution = fit_solution(train, orientation, selected["range_semantics"], None, max(35, iterations))
        _, held_metrics = core.evaluate_params(solution["params"], held, orientation, selected["range_semantics"], "B_low_dimensional")
        score = float(held_metrics["rms_mm"] + .15 * solution["train"]["rms_mm"])
        solution.update({"heldout": held_metrics, "data_only_score": score,
                         "low_dimensional_tof_params": parameter_document(solution["params"]),
                         "T_camera_from_tof": transform_document(solution["params"])})
        tof, camera = raw_rays(solution); solution["raw_zone_center_rays_tof"] = tof.tolist()
        solution["raw_zone_center_rays_camera"] = camera.tolist(); solutions[orientation] = solution
        ranking.append({"orientation": orientation, "data_only_score": score,
                        "train": solution["train"], "heldout": held_metrics})
    ranking.sort(key=lambda row: row["data_only_score"])
    pairs = [("identity", "rotate_90_cw"), ("identity", "rotate_180"), ("rotate_90_cw", "rotate_180"),
             (ranking[0]["orientation"], ranking[1]["orientation"]), (ranking[0]["orientation"], "rotate_270_cw")]
    unique = []
    for pair in pairs:
        if pair[0] != pair[1] and pair not in unique and (pair[1], pair[0]) not in unique: unique.append(pair)
    csv_rows, pairwise = [], []
    for left, right in unique:
        comparison = compare_mapping(solutions[left], solutions[right], include_rows=True)
        csv_rows.extend({"orientation_a": left, "orientation_b": right, **row} for row in comparison.pop("rows"))
        pairwise.append({"orientation_a": left, "orientation_b": right, **comparison})
    top = pairwise_for(pairwise, ranking[0]["orientation"], ranking[1]["orientation"])
    max_pixel = max(top["by_distance_mm"][str(d)]["pixel_displacement"]["max"] for d in DISTANCES)
    equivalent = bool(top["angular_difference_deg"]["p95"] <= .10 and top["angular_difference_deg"]["max"] <= .30 and max_pixel <= 2.0)
    doc = {"schema_version": "stage2b_gauge_equivalence.v1", "pose_count": len(train_ids | held_ids),
           "range_semantics": selected["range_semantics"], "model": "B_low_dimensional",
           "ranking_data_only": ranking, "solutions": solutions, "pairwise": pairwise,
           "engineering_equivalence_rule": "top pair angular p95 <= 0.10 deg, max <= 0.30 deg, max center displacement <= 2 px",
           "top_pair_engineering_equivalent": equivalent, "physical_D4_orientation": "UNRESOLVED_GAUGE",
           "canonical_gauge": "identity" if equivalent else ranking[0]["orientation"],
           "camera_frame_raw_zone_ray_mapping": "OBSERVABLE_AUTHORITY" if equivalent else "D4_DEPENDENT_NOT_EQUIVALENT",
           "physical_orientation_claim": False}
    return doc, csv_rows, solutions


def count_winners(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = {name: sum(row["winner"] == name for row in rows) for name in ("axial_z", "radial_range", "tie")}
    total = len(rows); count.update({"total": total, "axial_win_rate": count["axial_z"] / total if total else None,
                                     "radial_win_rate": count["radial_range"] / total if total else None})
    return count


def compare_semantics_groups(pooled: dict[str, list[tuple[dict[str, Any], float]]], key_fn) -> dict[str, Any]:
    values: dict[str, dict[str, list[float]]] = {}
    for semantics, pairs in pooled.items():
        for row, residual in pairs: values.setdefault(str(key_fn(row)), {}).setdefault(semantics, []).append(residual)
    result = {}
    for key, item in sorted(values.items()):
        axial = core.metrics(item.get("axial_z", [])); radial = core.metrics(item.get("radial_range", []))
        a, r = float(axial["rms_mm"]), float(radial["rms_mm"])
        result[key] = {"axial_z": axial, "radial_range": radial,
                       "radial_minus_axial_gap_mm": r - a, "winner": winner(a, r)}
    return result


def semantics_diagnostics(obs: dict[str, Any], selected: dict[str, Any], iterations: int,
                          subset_trials: int, seed: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    ids = valid_pose_ids(obs); all_ids = set(ids); metadata, core_keys = observation_metadata(obs)
    all_rows = core.observation_rows(obs, "broad", all_ids); train_ids, held_ids = scan_split(obs)
    train_rows = core.observation_rows(obs, "broad", train_ids); held_rows = core.observation_rows(obs, "broad", held_ids) or train_rows
    full_solutions, split_results = {}, {}
    for semantics in ("axial_z", "radial_range"):
        full_solutions[semantics] = fit_solution(all_rows, selected["orientation"], semantics, selected["params"], iterations)
        split = fit_solution(train_rows, selected["orientation"], semantics, selected["params"], iterations)
        _, broad_metric, _ = evaluate_layer(split, held_rows); _, core_metric, _ = evaluate_layer(split, held_rows, core_keys)
        split_results[semantics] = {"train": split["train"], "scan_level_heldout": broad_metric,
                                    "reference_plane_only_heldout": core_metric, "all_broad_heldout": broad_metric}
    scores = {sem: float(split_results[sem]["scan_level_heldout"]["rms_mm"]) for sem in split_results}
    full_winner = winner(scores["axial_z"], scores["radial_range"])
    lopo_rows, pooled, pooled_core, lopo_solutions = [], {"axial_z": [], "radial_range": []}, {"axial_z": [], "radial_range": []}, {}
    for held in ids:
        training = core.observation_rows(obs, "broad", all_ids - {held}); testing = core.observation_rows(obs, "broad", {held})
        scores, core_scores, metrics_by_sem, core_metrics_by_sem, params_by_sem = {}, {}, {}, {}, {}
        for semantics in ("axial_z", "radial_range"):
            fit = fit_solution(training, selected["orientation"], semantics, selected["params"], iterations)
            residuals, metric, used = evaluate_layer(fit, testing); scores[semantics] = float(metric["rms_mm"])
            core_residuals, core_metric, core_used = evaluate_layer(fit, testing, core_keys)
            core_scores[semantics] = float(core_metric["rms_mm"] if core_metric["rms_mm"] is not None else metric["rms_mm"])
            core_metrics_by_sem[semantics] = core_metric
            metrics_by_sem[semantics] = metric; params_by_sem[semantics] = fit["params"]
            pooled[semantics].extend(zip(used, residuals.tolist()))
            pooled_core[semantics].extend(zip(core_used, core_residuals.tolist()))
        fold_winner = winner(scores["axial_z"], scores["radial_range"])
        core_winner = winner(core_scores["axial_z"], core_scores["radial_range"])
        lopo_rows.append({"heldout_pose_id": held, "axial_score_rms_mm": scores["axial_z"],
                          "radial_score_rms_mm": scores["radial_range"],
                          "radial_minus_axial_gap_mm": scores["radial_range"] - scores["axial_z"],
                          "winner": fold_winner, "winner_flipped_from_full": fold_winner not in (full_winner, "tie"),
                          "core_axial_score_rms_mm": core_scores["axial_z"],
                          "core_radial_score_rms_mm": core_scores["radial_range"],
                          "core_radial_minus_axial_gap_mm": core_scores["radial_range"] - core_scores["axial_z"],
                          "core_winner": core_winner, "axial_metrics": metrics_by_sem["axial_z"],
                          "radial_metrics": metrics_by_sem["radial_range"],
                          "core_axial_metrics": core_metrics_by_sem["axial_z"],
                          "core_radial_metrics": core_metrics_by_sem["radial_range"]})
        lopo_solutions[held] = {"orientation": selected["orientation"], "range_semantics": selected["range_semantics"],
                                "model": "B_low_dimensional", "params": params_by_sem[selected["range_semantics"]]}
    subset_rows, subset_solutions = [], {}
    for trial, chosen in enumerate(subset_pose_sets(ids, .8, subset_trials, seed)):
        missing = all_ids - chosen; training = core.observation_rows(obs, "broad", chosen)
        testing = core.observation_rows(obs, "broad", missing) or training; scores, core_scores, params_by_sem = {}, {}, {}
        for semantics in ("axial_z", "radial_range"):
            fit = fit_solution(training, selected["orientation"], semantics, selected["params"], iterations)
            _, metric, _ = evaluate_layer(fit, testing); scores[semantics] = float(metric["rms_mm"]); params_by_sem[semantics] = fit["params"]
            _, core_metric, _ = evaluate_layer(fit, testing, core_keys)
            core_scores[semantics] = float(core_metric["rms_mm"] if core_metric["rms_mm"] is not None else metric["rms_mm"])
        subset_rows.append({"trial": trial, "pose_ids": sorted(chosen), "missing_pose_ids": sorted(missing),
                            "axial_score_rms_mm": scores["axial_z"], "radial_score_rms_mm": scores["radial_range"],
                            "radial_minus_axial_gap_mm": scores["radial_range"] - scores["axial_z"],
                            "winner": winner(scores["axial_z"], scores["radial_range"]),
                            "core_axial_score_rms_mm": core_scores["axial_z"],
                            "core_radial_score_rms_mm": core_scores["radial_range"],
                            "core_radial_minus_axial_gap_mm": core_scores["radial_range"] - core_scores["axial_z"],
                            "core_winner": winner(core_scores["axial_z"], core_scores["radial_range"])})
        subset_solutions[str(trial)] = {"orientation": selected["orientation"], "range_semantics": selected["range_semantics"],
                                        "model": "B_low_dimensional", "params": params_by_sem[selected["range_semantics"]],
                                        "missing_pose_ids": sorted(missing)}
    by_distance = compare_semantics_groups(pooled, lambda row: metadata[row_key(row)]["distance_bin"])
    by_angle = compare_semantics_groups(pooled, lambda row: metadata[row_key(row)]["angle_bin"])
    core_by_distance = compare_semantics_groups(pooled_core, lambda row: metadata[row_key(row)]["distance_bin"])
    core_by_angle = compare_semantics_groups(pooled_core, lambda row: metadata[row_key(row)]["angle_bin"])
    core_lopo_rows = [{"winner": row["core_winner"]} for row in lopo_rows]
    core_subset_rows = [{"winner": row["core_winner"]} for row in subset_rows]
    doc = {"schema_version": "stage2b_semantics_stability.v1", "registered_N": len(ids),
           "full_and_heldout": split_results, "full_winner": full_winner, "lopo_summary": count_winners(lopo_rows),
           "lopo_core_reference_plane_summary": count_winners(core_lopo_rows),
           "lopo_gap_distribution_mm": distribution(row["radial_minus_axial_gap_mm"] for row in lopo_rows),
           "lopo_core_gap_distribution_mm": distribution(row["core_radial_minus_axial_gap_mm"] for row in lopo_rows),
           "winner_flips": [row["heldout_pose_id"] for row in lopo_rows if row["winner_flipped_from_full"]],
           "subset_summary": count_winners(subset_rows),
           "subset_core_reference_plane_summary": count_winners(core_subset_rows),
           "subset_gap_distribution_mm": distribution(row["radial_minus_axial_gap_mm"] for row in subset_rows),
           "subset_core_gap_distribution_mm": distribution(row["core_radial_minus_axial_gap_mm"] for row in subset_rows),
           "subset_fraction": .8, "subset_trial_count": len(subset_rows), "seed": seed,
           "most_unstable_subset": min(subset_rows, key=lambda row: abs(row["radial_minus_axial_gap_mm"])) if subset_rows else None,
           "distance_bins_lopo": by_distance, "angle_bins_lopo": by_angle,
           "core_reference_plane_distance_bins_lopo": core_by_distance,
           "core_reference_plane_angle_bins_lopo": core_by_angle,
           "axial_cross_distance_winner": {"all_broad": all(value["winner"] == "axial_z" for value in by_distance.values()),
                                            "core_reference_plane": all(value["winner"] == "axial_z" for value in core_by_distance.values())},
           "score_definition": "out-of-pose all-broad RMS in mm; positive gap favors axial_z"}
    return doc, lopo_rows, subset_rows, {"full": full_solutions, "lopo": lopo_solutions, "subsets": subset_solutions}


def stats_for(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return core.metrics([row["residual_mm"] for row in rows])


def grouped_stats(rows: list[dict[str, Any]], key_fn) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows: groups.setdefault(str(key_fn(row)), []).append(row)
    return {key: stats_for(value) for key, value in sorted(groups.items())}


def ratio(rows: list[dict[str, Any]], key: str) -> float | None:
    return sum(row[key] is True for row in rows) / len(rows) if rows else None


def value_counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows: result[str(row[key])] = result.get(str(row[key]), 0) + 1
    return result


def provenance_diagnostics(obs: dict[str, Any], solution: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    metadata, _ = observation_metadata(obs); rows = core.observation_rows(obs, "broad")
    residuals, _ = core.evaluate_params(solution["params"], rows, solution["orientation"], solution["range_semantics"], solution["model"])
    output = []
    for row, residual in zip(rows, residuals):
        meta = metadata[row_key(row)]
        output.append({"pose_id": row["pose_id"], "frame_id": row["frame_id"], "raw_zone_index": int(row["raw_index"]),
                       "raw_row": int(meta["raw_row"]), "raw_col": int(meta["raw_col"]), "distance_mm": float(meta["distance_mm"]),
                       "range_sigma_mm": float(meta["range_sigma_mm"]), "target_status": int(meta["target_status"]),
                       "nb_target_detected": int(meta["target_count"]), "broad_valid": bool(meta["broad_valid"]),
                       "strict_valid": bool(meta["strict_valid"]), "broad_only": bool(meta["broad_valid"] and not meta["strict_valid"]),
                       "likely_reference_plane": meta.get("likely_reference_plane"),
                       "likely_outside_background": meta.get("likely_outside_or_background"),
                       "reference_distance_mm": float(meta["reference_distance_mm"]), "distance_bin": meta["distance_bin"],
                       "plane_angle_deg": float(meta["plane_angle_deg"]), "angle_bin": meta["angle_bin"],
                       "pair_delta_us": int(meta["pair_delta_us"]), "residual_mm": float(residual),
                       "abs_residual_mm": abs(float(residual))})
    sigma = np.asarray([row["range_sigma_mm"] for row in output], dtype=float)
    q = np.quantile(sigma, [.25, .5, .75]) if len(sigma) else np.zeros(3)
    for row in output:
        v = row["range_sigma_mm"]; row["sigma_quantile"] = "q1" if v <= q[0] else ("q2" if v <= q[1] else ("q3" if v <= q[2] else "q4"))
    layers = {"core_reference_plane_metrics": stats_for([r for r in output if r["likely_reference_plane"] is True]),
              "all_broad_robustness_metrics": stats_for(output),
              "outside_background": stats_for([r for r in output if r["likely_outside_background"] is True]),
              "strict_valid": stats_for([r for r in output if r["strict_valid"]]),
              "broad_valid_but_not_strict": stats_for([r for r in output if r["broad_only"]])}
    strata = {"reference_plane": grouped_stats(output, lambda r: r["likely_reference_plane"]),
              "outside_background": grouped_stats(output, lambda r: r["likely_outside_background"]),
              "validity": grouped_stats(output, lambda r: "strict" if r["strict_valid"] else "broad_only"),
              "target_status": grouped_stats(output, lambda r: r["target_status"]),
              "sigma_quantiles": grouped_stats(output, lambda r: r["sigma_quantile"]),
              "pose": grouped_stats(output, lambda r: r["pose_id"]), "raw_row": grouped_stats(output, lambda r: r["raw_row"]),
              "raw_col": grouped_stats(output, lambda r: r["raw_col"]), "raw_zone": grouped_stats(output, lambda r: r["raw_zone_index"]),
              "distance_bin": grouped_stats(output, lambda r: r["distance_bin"]), "angle_bin": grouped_stats(output, lambda r: r["angle_bin"])}
    zone_rows = []
    for zone in range(64):
        selected = [r for r in output if r["raw_zone_index"] == zone]; metric = stats_for(selected)
        zone_rows.append({"raw_zone_index": zone, "raw_row": zone // 8, "raw_col": zone % 8, **metric,
                          "outside_background_ratio": ratio(selected, "likely_outside_background"),
                          "broad_only_ratio": ratio(selected, "broad_only"),
                          "sigma_median_mm": float(np.median([r["range_sigma_mm"] for r in selected])) if selected else None,
                          "status_counts": value_counts(selected, "target_status"), "pose_ids": sorted({r["pose_id"] for r in selected})})
    automatic = [row["raw_zone_index"] for row in sorted(zone_rows, key=lambda r: r["rms_mm"] or -1, reverse=True)[:10]]
    focus = sorted(set([7, 14, 15, 17, 23, 31, 55] + automatic))
    ranking = sorted(({"layer": key, **value} for key, value in layers.items() if value["count"]),
                     key=lambda row: row["rms_mm"], reverse=True)
    doc = {"schema_version": "stage2b_residual_provenance.v1", "residual_count": len(output), "metric_layers": layers,
           "strata": strata, "sigma_quantile_edges_mm": q.tolist(), "focus_raw_zones": focus,
           "focus_zone_summaries": [row for row in zone_rows if row["raw_zone_index"] in focus],
           "observation_usage_audit": {"solver_fit": "observation_rows(policy): current solution policy is broad",
               "validate_lopo": "observation_rows(solution policy): current solution policy is broad",
               "current_per_zone": "broad observations grouped by canonical index (not raw index)",
               "advanced_diagnostics": "raw-index provenance; core reference-plane and all-broad robustness separated",
               "outside_background_in_current_core_accuracy": any(r["likely_outside_background"] is True for r in output),
               "runtime_broad_policy_modified": False, "automatic_blacklist": False},
           "long_tail_source_ranking": ranking}
    return doc, output, zone_rows


def mapping_rows(full: dict[str, Any], solutions: dict[str, dict[str, Any]], id_name: str) -> list[dict[str, Any]]:
    rows = []
    for identifier, solution in solutions.items():
        comparison = compare_mapping(full, solution)
        row = {id_name: identifier, "missing_pose_ids": solution.get("missing_pose_ids"),
               "angular_p50_deg": comparison["angular_difference_deg"]["p50"],
               "angular_p95_deg": comparison["angular_difference_deg"]["p95"],
               "angular_max_deg": comparison["angular_difference_deg"]["max"], "worst_raw_zone": comparison["worst_raw_zone"]}
        for distance in DISTANCES:
            metric = comparison["by_distance_mm"][str(distance)]["pixel_displacement"]
            row.update({f"pixel_p50_{distance}": metric["p50"], f"pixel_p95_{distance}": metric["p95"], f"pixel_max_{distance}": metric["max"]})
        rows.append(row)
    return rows


def aggregate_mapping_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows: return {"count": 0}
    result = {"count": len(rows)}
    for key in ("angular_p50_deg", "angular_p95_deg", "angular_max_deg"): result[key] = quantiles(row[key] for row in rows)
    for distance in DISTANCES:
        for prefix in ("pixel_p50", "pixel_p95", "pixel_max"):
            key = f"{prefix}_{distance}"; result[key] = quantiles(row[key] for row in rows)
    return result


def mapping_stability(full: dict[str, Any], lopo: dict[str, dict[str, Any]], subsets: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    lopo_rows = mapping_rows(full, lopo, "heldout_pose_id"); subset_rows = mapping_rows(full, subsets, "trial")
    params = np.asarray([solution["params"] for solution in subsets.values()], dtype=float); std = params.std(axis=0) if len(params) else np.zeros(10)
    doc = {"schema_version": "stage2b_mapping_stability.v1",
           "full_solution": {"orientation": full["orientation"], "range_semantics": full["range_semantics"], "params": parameter_document(full["params"])},
           "lopo_summary": aggregate_mapping_rows(lopo_rows), "subset_summary": aggregate_mapping_rows(subset_rows),
           "worst_subset": max(subset_rows, key=lambda row: row["angular_max_deg"]) if subset_rows else None,
           "parameter_std": [{"name": name, "unit": unit, "std": float(value)} for (name, unit), value in zip(PARAMETERS, std)],
           "raw_zone_key_authority": True}
    return doc, lopo_rows, subset_rows


def pre14_comparison(obs: dict[str, Any], selected: dict[str, Any], iterations: int,
                     current_gauge: dict[str, Any], current_full: dict[str, Any], current_provenance: dict[str, Any]) -> dict[str, Any]:
    ids = valid_pose_ids(obs); pre_ids = {pid for pid in ids if pid != "S2B_015"}
    if "S2B_015" not in ids: return {"status": "S2B_015_NOT_PRESENT"}
    pre_rows = core.observation_rows(obs, "broad", pre_ids)
    pre_full = fit_solution(pre_rows, selected["orientation"], selected["range_semantics"], selected["params"], iterations)
    train14, held14 = scan_split(obs, pre_ids); split_rows = core.observation_rows(obs, "broad", train14)
    held_rows = core.observation_rows(obs, "broad", held14) or split_rows
    pre_split = fit_solution(split_rows, selected["orientation"], selected["range_semantics"], selected["params"], iterations)
    _, held_metric = core.evaluate_params(pre_split["params"], held_rows, selected["orientation"], selected["range_semantics"], "B_low_dimensional")
    pre_gauge, _, _ = gauge_diagnostics(obs, selected, iterations, pre_ids); pre_semantics = {}
    for semantics in ("axial_z", "radial_range"):
        fit = fit_solution(split_rows, selected["orientation"], semantics, selected["params"], iterations)
        _, metric = core.evaluate_params(fit["params"], held_rows, selected["orientation"], semantics, "B_low_dimensional")
        pre_semantics[semantics] = metric
    train15, held15 = scan_split(obs); current_train = core.observation_rows(obs, "broad", train15)
    current_held = core.observation_rows(obs, "broad", held15) or current_train
    current_split = fit_solution(current_train, selected["orientation"], selected["range_semantics"], selected["params"], iterations)
    _, current_held_metric = core.evaluate_params(current_split["params"], current_held, selected["orientation"], selected["range_semantics"], "B_low_dimensional")
    pre_obs = {**obs, "poses": [p for p in obs["poses"] if p["pose_id"] in pre_ids]}
    pre_provenance, _, _ = provenance_diagnostics(pre_obs, pre_full); mapping = compare_mapping(pre_full, current_full)
    current_rank = current_gauge["ranking_data_only"]; pre_rank = pre_gauge["ranking_data_only"]
    current_gap = current_rank[1]["data_only_score"] - current_rank[0]["data_only_score"]
    pre_gap = pre_rank[1]["data_only_score"] - pre_rank[0]["data_only_score"]
    return {"schema_version": "stage2b_pre14_vs_current15.v1",
            "pre14": {"pose_count": len(pre_ids), "heldout": held_metric, "D4_data_only_gap_mm": pre_gap,
                      "rank": pre_full["rank"], "condition_number": pre_full["condition_number"],
                      "semantics_heldout": pre_semantics, "long_tail": pre_provenance["metric_layers"]},
            "current15": {"pose_count": len(ids), "heldout": current_held_metric, "D4_data_only_gap_mm": current_gap,
                          "rank": current_full["rank"], "condition_number": current_full["condition_number"],
                          "long_tail": current_provenance["metric_layers"]},
            "pre14_to_current15_camera_mapping": mapping,
            "S2B_015_inference": "improves geometric constraints but does not resolve physical D4 gauge" if current_gap <= pre_gap else "changes constraints; D4 gauge remains unresolved"}


def protected_hashes() -> dict[str, str]:
    paths = [core.MANIFEST_PATH, core.OBS_PATH]
    paths.extend(sorted((core.WORKSPACE / "manifests").glob("*.json")))
    paths.extend(sorted((core.WORKSPACE / "imports").rglob("*.json")))
    paths.extend(sorted((core.WORKSPACE / "candidate").glob("*")))
    return {str(path.relative_to(core.WORKSPACE)).replace("\\", "/"): core.sha256_file(path) for path in paths if path.is_file()}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flat = [{key: json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else value
             for key, value in row.items()} for row in rows]
    fields = list(dict.fromkeys(key for row in flat for key in row)); temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(flat)
    temp.replace(path)


def write_pair(path: Path, document: dict[str, Any], markdown: str) -> None:
    core.write_json(path.with_suffix(".json"), document); core.write_text(path.with_suffix(".md"), markdown)


def gauge_markdown(doc: dict[str, Any]) -> str:
    top = doc["ranking_data_only"][:2]; pair = pairwise_for(doc["pairwise"], top[0]["orientation"], top[1]["orientation"])
    lines = ["# D4 gauge equivalence", "", f"Physical D4 orientation: **{doc['physical_D4_orientation']}**.",
             f"Canonical gauge: `{doc['canonical_gauge']}`; raw-zone Camera-ray authority: `{doc['camera_frame_raw_zone_ray_mapping']}`.", "",
             f"Top pair `{top[0]['orientation']}` vs `{top[1]['orientation']}` angular p95/max: {pair['angular_difference_deg']['p95']:.6f}/{pair['angular_difference_deg']['max']:.6f} deg.", "",
             "| distance mm | center pixel p95/max | footprint corner p95/max | IoU min |", "|---:|---:|---:|---:|"]
    for distance in DISTANCES:
        row = pair["by_distance_mm"][str(distance)]
        lines.append(f"| {distance} | {row['pixel_displacement']['p95']:.4f}/{row['pixel_displacement']['max']:.4f} | {row['footprint_corner_max_displacement_px']['p95']:.4f}/{row['footprint_corner_max_displacement_px']['max']:.4f} | {row['footprint_iou']['min']:.6f} |")
    return "\n".join(lines + ["", "`rotate_90_cw` is not asserted as a verified physical orientation.", ""])


def semantics_markdown(doc: dict[str, Any]) -> str:
    lines = ["# Range semantics stability", "", f"Full winner: **{doc['full_winner']}**. All-broad LOPO axial/radial/tie = {doc['lopo_summary']['axial_z']}/{doc['lopo_summary']['radial_range']}/{doc['lopo_summary']['tie']}; subsets = {doc['subset_summary']['axial_z']}/{doc['subset_summary']['radial_range']}/{doc['subset_summary']['tie']}.",
             f"Core-reference-plane LOPO axial/radial/tie = {doc['lopo_core_reference_plane_summary']['axial_z']}/{doc['lopo_core_reference_plane_summary']['radial_range']}/{doc['lopo_core_reference_plane_summary']['tie']}; subsets = {doc['subset_core_reference_plane_summary']['axial_z']}/{doc['subset_core_reference_plane_summary']['radial_range']}/{doc['subset_core_reference_plane_summary']['tie']}.", "",
             "| distance | winner | radial - axial RMS mm |", "|---|---|---:|"]
    for key, value in doc["distance_bins_lopo"].items(): lines.append(f"| {key} | {value['winner']} | {value['radial_minus_axial_gap_mm']:.6f} |")
    return "\n".join(lines + [""])


def provenance_markdown(doc: dict[str, Any]) -> str:
    lines = ["# Residual provenance", "", "Broad solver/validation metrics can include observations marked outside/background; this output preserves two explicit layers.", "",
             "| layer | count | median | p95 | RMS | max |", "|---|---:|---:|---:|---:|---:|"]
    for key, value in doc["metric_layers"].items():
        lines.append(f"| {key} | {value['count']} | {value['median_mm'] or 0:.4f} | {value['p95_mm'] or 0:.4f} | {value['rms_mm'] or 0:.4f} | {value['max_mm'] or 0:.4f} |")
    return "\n".join(lines + ["", "No automatic blacklist; runtime broad policy unchanged.", ""])


def mapping_markdown(doc: dict[str, Any]) -> str:
    lopo, subset = doc["lopo_summary"], doc["subset_summary"]
    return ("# Camera-frame mapping stability\n\n"
            f"LOPO fold angular-p95 distribution p50/p95/max: {lopo['angular_p95_deg']['p50']:.6f}/{lopo['angular_p95_deg']['p95']:.6f}/{lopo['angular_p95_deg']['max']:.6f} deg.\n\n"
            f"Subset angular-p95 distribution p50/p95/max: {subset['angular_p95_deg']['p50']:.6f}/{subset['angular_p95_deg']['p95']:.6f}/{subset['angular_p95_deg']['max']:.6f} deg.\n")


def pre14_markdown(doc: dict[str, Any]) -> str:
    if "pre14" not in doc: return "# pre14 vs current15\n\nS2B_015 not present.\n"
    return ("# pre14 vs current15\n\n"
            f"Heldout median/p95: pre14 {doc['pre14']['heldout']['median_mm']:.4f}/{doc['pre14']['heldout']['p95_mm']:.4f} mm; current15 {doc['current15']['heldout']['median_mm']:.4f}/{doc['current15']['heldout']['p95_mm']:.4f} mm.\n\n"
            f"D4 data-only gap: pre14 {doc['pre14']['D4_data_only_gap_mm']:.9f}; current15 {doc['current15']['D4_data_only_gap_mm']:.9f} mm.\n\nInference: {doc['S2B_015_inference']}.\n")


def run(args: Any) -> int:
    output = core.WORKSPACE / "advanced_diagnostics"; before = protected_hashes()
    obs = core.get_observations(refresh=False); selected = core.load_json(core.SOLUTION_PATH)["selected"]
    gauge, gauge_rows, _ = gauge_diagnostics(obs, selected, args.max_iterations)
    semantics, lopo_rows, subset_rows, solutions = semantics_diagnostics(obs, selected, args.max_iterations, args.subset_trials, args.seed)
    full = solutions["full"][selected["range_semantics"]]; provenance, residual_rows, zone_rows = provenance_diagnostics(obs, full)
    mapping, lopo_mapping, subset_mapping = mapping_stability(full, solutions["lopo"], solutions["subsets"])
    pre14 = pre14_comparison(obs, selected, args.max_iterations, gauge, full, provenance); output.mkdir(parents=True, exist_ok=True)
    write_pair(output / "gauge_equivalence_summary", gauge, gauge_markdown(gauge)); write_csv(output / "d4_camera_ray_pairwise.csv", gauge_rows)
    write_pair(output / "semantics_stability", semantics, semantics_markdown(semantics)); write_csv(output / "semantics_lopo.csv", lopo_rows); write_csv(output / "semantics_subsets.csv", subset_rows)
    write_pair(output / "residual_provenance_summary", provenance, provenance_markdown(provenance)); write_csv(output / "residual_provenance.csv", residual_rows); write_csv(output / "zone_outlier_summary.csv", zone_rows)
    write_pair(output / "mapping_stability", mapping, mapping_markdown(mapping)); write_csv(output / "lopo_mapping.csv", lopo_mapping); write_csv(output / "subset_mapping.csv", subset_mapping)
    write_pair(output / "pre14_vs_current15", pre14, pre14_markdown(pre14))
    after = protected_hashes(); hashes = {"schema_version": "stage2b_protected_hashes.v1", "before": before, "after": after,
        "unchanged": before == after, "protected_file_count": len(before),
        "protected_scopes": ["workspace_manifest.json", "manifests/*.json", "imports/**/*.json", "observations/observations.stage2b.json", "candidate/*"]}
    core.write_json(output / "protected_hashes.json", hashes)
    if before != after: raise core.Stage2BError("advanced diagnostics changed a protected input")
    print(json.dumps({"output": str(output), "pose_count": len(valid_pose_ids(obs)), "subset_trials": len(subset_rows),
                      "protected_inputs_unchanged": True, "physical_D4_orientation": gauge["physical_D4_orientation"],
                      "mapping_authority": gauge["camera_frame_raw_zone_ray_mapping"],
                      "semantics_lopo": semantics["lopo_summary"], "semantics_subsets": semantics["subset_summary"]}, indent=2))
    return 0
