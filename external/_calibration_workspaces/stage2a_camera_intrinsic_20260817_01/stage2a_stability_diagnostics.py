#!/usr/bin/env python3
"""Read-only stability diagnostics for the Stage2A Brown-5 camera solution."""
from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path
from typing import Any, Callable, Iterable

import cv2
import numpy as np


PARAMETER_NAMES = ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3")
MIN_SELECTED_POSES = 13  # LOPO must retain at least the calibration minimum of 12 views.
PREEDGE22_POSE_IDS = tuple(
    [f"A{i:02d}" for i in range(1, 5)]
    + [f"B{i:02d}" for i in range(1, 9)]
    + [f"C{i:02d}" for i in range(1, 11)]
)
PRE_SUPPLEMENT25_POSE_IDS = tuple(
    [f"A{i:02d}" for i in range(1, 5)]
    + [f"B{i:02d}" for i in range(1, 9)]
    + [f"C{i:02d}" for i in range(1, 14)]
)
HISTORICAL_PREEDGE22 = {
    "fx": 889.830017075763,
    "fy": 888.6680301952897,
    "cx": 304.67314359604,
    "cy": 186.0800925843809,
    "k1": 0.08353213931667819,
    "k2": -0.12810159944544855,
    "p1": 0.007219338040853551,
    "p2": -0.00532074121598611,
    "k3": -1.037785243663492,
}
PRE_SUPPLEMENT25_ANCHOR = {
    "parameters": {
        "fx": 867.1047481579573, "fy": 866.0458641457095,
        "cx": 302.9457452600097, "cy": 179.38137047375486,
        "k1": 0.05385981260561747, "k2": 0.0783975233935736,
        "p1": 0.00663036010743015, "p2": -0.006245150307360047,
        "k3": -1.5331527308460569,
    },
    "metrics": {
        "train_rms_px": 0.15562182746815148,
        "heldout_rms_px": 0.15678423752751658,
        "heldout_p95_px": 0.2891905650496483,
    },
}


def _finite_float(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"non-finite diagnostic value: {value!r}")
    return result


def params_to_camera(parameters: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Convert a Brown-5 parameter mapping to OpenCV K and D arrays."""
    p = {key: _finite_float(parameters[key]) for key in PARAMETER_NAMES}
    K = np.array([[p["fx"], 0.0, p["cx"]], [0.0, p["fy"], p["cy"]], [0.0, 0.0, 1.0]], np.float64)
    D = np.array([p["k1"], p["k2"], p["p1"], p["p2"], p["k3"]], np.float64)
    return K, D


def make_mapping_grid(width: int, height: int, step: int, reference_K: np.ndarray) -> np.ndarray:
    """Build a deterministic grid including extrema, midpoints, centre and principal-point neighbourhood."""
    if step < 1 or step > 16:
        raise ValueError("grid step must be in [1, 16]")
    xs = list(range(0, width, step)) + [width - 1]
    ys = list(range(0, height, step)) + [height - 1]
    points = [(float(x), float(y)) for y in ys for x in xs]
    cx, cy = float(reference_K[0, 2]), float(reference_K[1, 2])
    points.extend([
        (0.0, 0.0), (width - 1.0, 0.0), (0.0, height - 1.0), (width - 1.0, height - 1.0),
        ((width - 1.0) / 2.0, 0.0), ((width - 1.0) / 2.0, height - 1.0),
        (0.0, (height - 1.0) / 2.0), (width - 1.0, (height - 1.0) / 2.0),
        ((width - 1.0) / 2.0, (height - 1.0) / 2.0),
    ])
    for dy in (-16.0, 0.0, 16.0):
        for dx in (-16.0, 0.0, 16.0):
            points.append((min(width - 1.0, max(0.0, cx + dx)), min(height - 1.0, max(0.0, cy + dy))))
    unique = sorted({(round(x, 6), round(y, 6)) for x, y in points}, key=lambda p: (p[1], p[0]))
    return np.asarray(unique, np.float64)


def region_masks(points: np.ndarray, width: int, height: int) -> dict[str, np.ndarray]:
    """Return explicit, overlapping engineering regions for spatial summaries.

    Centre is the middle 50% in each axis. Border is the outer 15% band. Corner
    patches are the outer 20% in both axes. Quadrants split at the image centre.
    """
    x, y = points[:, 0], points[:, 1]
    xm, ym = (width - 1.0) / 2.0, (height - 1.0) / 2.0
    bx, by = 0.15 * width, 0.15 * height
    cx, cy = 0.20 * width, 0.20 * height
    return {
        "full_image": np.ones(len(points), dtype=bool),
        "center_middle_50pct": (x >= 0.25 * width) & (x < 0.75 * width) & (y >= 0.25 * height) & (y < 0.75 * height),
        "border_outer_15pct": (x < bx) | (x >= width - bx) | (y < by) | (y >= height - by),
        "quadrant_top_left": (x <= xm) & (y <= ym),
        "quadrant_top_right": (x > xm) & (y <= ym),
        "quadrant_bottom_left": (x <= xm) & (y > ym),
        "quadrant_bottom_right": (x > xm) & (y > ym),
        "corner_top_left_20pct": (x < cx) & (y < cy),
        "corner_top_right_20pct": (x >= width - cx) & (y < cy),
        "corner_bottom_left_20pct": (x < cx) & (y >= height - cy),
        "corner_bottom_right_20pct": (x >= width - cx) & (y >= height - cy),
    }


def _stats(values: np.ndarray, include_mean: bool = True) -> dict[str, float]:
    result = {
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
        "p99": float(np.percentile(values, 99)),
        "max": float(np.max(values)),
    }
    if include_mean:
        result = {"mean": float(np.mean(values)), **result}
    return result


def compare_camera_solutions(
    reference: dict[str, Any],
    test: dict[str, Any],
    points: np.ndarray,
    width: int = 640,
    height: int = 360,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Compare raw distorted pixel -> normalized ray using one shared rectified plane."""
    ref_K, ref_D = params_to_camera(reference)
    test_K, test_D = params_to_camera(test)
    raw = np.asarray(points, np.float64).reshape(-1, 1, 2)
    ref_xy = cv2.undistortPoints(raw, ref_K, ref_D, P=None).reshape(-1, 2)
    test_xy = cv2.undistortPoints(raw, test_K, test_D, P=None).reshape(-1, 2)
    ref_xyz = np.column_stack((ref_xy, np.ones(len(ref_xy))))
    test_xyz = np.column_stack((test_xy, np.ones(len(test_xy))))
    ref_rays = ref_xyz / np.linalg.norm(ref_xyz, axis=1)[:, None]
    test_rays = test_xyz / np.linalg.norm(test_xyz, axis=1)[:, None]
    dots = np.sum(ref_rays * test_rays, axis=1)
    dots = np.clip(dots, -1.0, 1.0)
    dots[dots > 1.0 - 1e-14] = 1.0
    angle_rad = np.arccos(dots)
    angle_deg = np.degrees(angle_rad)
    rect_ref = ref_xy * np.array([ref_K[0, 0], ref_K[1, 1]]) + np.array([ref_K[0, 2], ref_K[1, 2]])
    rect_test = test_xy * np.array([ref_K[0, 0], ref_K[1, 1]]) + np.array([ref_K[0, 2], ref_K[1, 2]])
    displacement = np.linalg.norm(rect_test - rect_ref, axis=1)
    offset_1m = np.tan(angle_rad) * 1000.0
    offset_2m = np.tan(angle_rad) * 2000.0
    if not all(np.all(np.isfinite(v)) for v in (ref_xy, test_xy, displacement, angle_deg, offset_1m, offset_2m)):
        raise ValueError("mapping comparison produced NaN or infinity")
    masks = region_masks(np.asarray(points), width, height)
    regions: dict[str, Any] = {}
    for name, mask in masks.items():
        regions[name] = {
            "point_count": int(mask.sum()),
            "equivalent_rectified_displacement_px": _stats(displacement[mask]),
            "angular_difference_deg": _stats(angle_deg[mask]),
        }
    worst_index = int(np.argmax(displacement))
    full = {
        "point_count": int(len(points)),
        "equivalent_rectified_displacement_px": _stats(displacement),
        "angular_difference_deg": _stats(angle_deg),
        "equivalent_offset_1m_mm": {k: v for k, v in _stats(offset_1m).items() if k in ("p50", "p95", "max")},
        "equivalent_offset_2m_mm": {k: v for k, v in _stats(offset_2m).items() if k in ("p50", "p95", "max")},
        "worst_raw_pixel": {"x": float(points[worst_index, 0]), "y": float(points[worst_index, 1])},
    }
    return {"full_image": full, "regions": regions}, {
        "points": np.asarray(points), "ref_xy": ref_xy, "test_xy": test_xy,
        "displacement_px": displacement, "angle_deg": angle_deg,
        "offset_1m_mm": offset_1m, "offset_2m_mm": offset_2m,
    }


def deterministic_subsets(pose_count: int, subset_size: int, runs: int, seed: int) -> list[list[int]]:
    if not 1 <= subset_size <= pose_count:
        raise ValueError("invalid subset size")
    rng = random.Random(seed)
    return [sorted(rng.sample(range(pose_count), subset_size)) for _ in range(runs)]


def derive_subset_size(pose_count: int, fraction: float) -> int:
    """Return ceil(N*fraction), ensuring the actual retained fraction is never lower.

    Ceil is deliberate and stable: 25 at 0.8 remains 20, while 28 becomes 23
    (rather than silently retaining the historical 20-view constant).
    """
    if pose_count < 1:
        raise ValueError("pose count must be positive")
    if not 0.0 < fraction < 1.0:
        raise ValueError("subsample fraction must be in (0, 1)")
    return min(pose_count, max(1, int(math.ceil(pose_count * fraction))))


def leave_one_out_selections(selection: list[tuple[str, str]]) -> list[tuple[str, list[tuple[str, str]]]]:
    return [(pose, selection[:index] + selection[index + 1:]) for index, (pose, _) in enumerate(selection)]


def reconstruct_named_subset(
    selection: list[tuple[str, str]], pose_ids: Iterable[str], label: str,
) -> list[tuple[str, str]]:
    """Rebuild a historical subset from an explicit pose-ID allow-list."""
    by_pose: dict[str, str] = {}
    for pose, frame in selection:
        if pose in by_pose:
            raise ValueError(f"duplicate selected pose ID: {pose}")
        by_pose[pose] = frame
    required = tuple(pose_ids)
    missing = [pose for pose in required if pose not in by_pose]
    if missing:
        raise ValueError(f"{label} reconstruction missing selected poses: {', '.join(missing)}")
    return [(pose, by_pose[pose]) for pose in required]


def reconstruct_preedge22(selection: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return reconstruct_named_subset(selection, PREEDGE22_POSE_IDS, "historical pre-edge22")


def reconstruct_pre_supplement25(selection: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return reconstruct_named_subset(selection, PRE_SUPPLEMENT25_POSE_IDS, "pre-supplement25")


def _parameter_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    result = {}
    for name in PARAMETER_NAMES:
        values = np.asarray([float(row[name]) for row in rows], np.float64)
        result[name] = {
            "min": float(values.min()), "p5": float(np.percentile(values, 5)),
            "p50": float(np.percentile(values, 50)), "p95": float(np.percentile(values, 95)),
            "max": float(values.max()), "range": float(values.max() - values.min()),
            "std": float(values.std(ddof=1)) if len(values) > 1 else 0.0,
        }
    return result


def _flatten_run_row(run_id: int, pose_ids: list[str], fit: dict[str, Any], metrics: dict[str, Any], reference: dict[str, float]) -> dict[str, Any]:
    params = {key: float(value) for key, value in fit["parameters"].items()}
    full = metrics["full_image"]
    disp, angle = full["equivalent_rectified_displacement_px"], full["angular_difference_deg"]
    row: dict[str, Any] = {"run_id": run_id, "included_pose_ids": ";".join(pose_ids), "calibration_rms_px": fit["rms"]}
    row.update(params)
    row.update({f"delta_{key}": params[key] - reference[key] for key in PARAMETER_NAMES})
    row.update({
        "mapping_p50_px": disp["p50"], "mapping_p95_px": disp["p95"], "mapping_p99_px": disp["p99"], "mapping_max_px": disp["max"],
        "angular_p50_deg": angle["p50"], "angular_p95_deg": angle["p95"], "angular_p99_deg": angle["p99"], "angular_max_deg": angle["max"],
        "offset_1m_p95_mm": full["equivalent_offset_1m_mm"]["p95"], "offset_1m_max_mm": full["equivalent_offset_1m_mm"]["max"],
        "offset_2m_p95_mm": full["equivalent_offset_2m_mm"]["p95"], "offset_2m_max_mm": full["equivalent_offset_2m_mm"]["max"],
        "worst_x": full["worst_raw_pixel"]["x"], "worst_y": full["worst_raw_pixel"]["y"],
    })
    return row


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else [])
        if rows:
            writer.writeheader(); writer.writerows(rows)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    temp.replace(path)


def _heatmap(path: Path, reference: dict[str, Any], test: dict[str, Any], width: int, height: int, title: str) -> None:
    yy, xx = np.mgrid[0:height, 0:width]
    points = np.column_stack((xx.ravel(), yy.ravel())).astype(np.float64)
    _, raw = compare_camera_solutions(reference, test, points, width, height)
    values = raw["displacement_px"].reshape(height, width)
    scale = float(values.max()) or 1.0
    colored = cv2.applyColorMap(np.clip(values / scale * 255.0, 0, 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    canvas = np.full((height + 72, width, 3), 255, np.uint8)
    canvas[:height] = colored
    cv2.putText(canvas, title[:82], (8, height + 24), cv2.FONT_HERSHEY_SIMPLEX, .48, (20, 20, 20), 1, cv2.LINE_AA)
    cv2.putText(canvas, f"Equivalent rectified displacement: 0 .. {scale:.6f} px", (8, height + 50), cv2.FONT_HERSHEY_SIMPLEX, .48, (20, 20, 20), 1, cv2.LINE_AA)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), canvas):
        raise OSError(f"failed to write {path}")


def _distribution(values: Iterable[float]) -> dict[str, float]:
    a = np.asarray(list(values), np.float64)
    return {"min": float(a.min()), "p5": float(np.percentile(a, 5)), "p50": float(np.percentile(a, 50)), "p95": float(np.percentile(a, 95)), "max": float(a.max())}


def _comparison_doc(name: str, reference: dict[str, float], test: dict[str, float], metrics: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "stage2a_mapping_comparison.v1", "comparison": name,
        "reference_parameters": reference, "test_parameters": test,
        "parameter_deltas": {key: test[key] - reference[key] for key in PARAMETER_NAMES},
        "mapping": metrics, **(extra or {}),
    }


def _spatial_worst(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    accum: dict[str, list[float]] = {}
    for doc in comparisons:
        for region, value in doc["mapping"]["regions"].items():
            if region == "full_image":
                continue
            accum.setdefault(region, []).append(value["equivalent_rectified_displacement_px"]["p95"])
    medians = {name: float(np.median(values)) for name, values in accum.items()}
    ordered = sorted(medians.items(), key=lambda item: item[1], reverse=True)
    return {"ranking_by_median_comparison_p95_px": [{"region": k, "median_p95_px": v} for k, v in ordered], "worst_region": ordered[0][0] if ordered else None}


def _fmt_metrics(doc: dict[str, Any]) -> str:
    full = doc["mapping"]["full_image"]
    d, a, o = full["equivalent_rectified_displacement_px"], full["angular_difference_deg"], full["equivalent_offset_2m_mm"]
    w = full["worst_raw_pixel"]
    return (f"p50/p95/p99/max = {d['p50']:.6f}/{d['p95']:.6f}/{d['p99']:.6f}/{d['max']:.6f} px; "
            f"angle p95/max = {a['p95']:.6f}/{a['max']:.6f} deg; 2 m p95/max = {o['p95']:.3f}/{o['max']:.3f} mm; "
            f"worst raw pixel = ({w['x']:.1f}, {w['y']:.1f}).")


def _top2_selection(primary: list[tuple[str, str]], candidate: dict[str, Any]) -> list[tuple[str, str]]:
    heldout = {row["pose_id"]: row["frame_id"] for row in candidate.get("heldout_frames", [])}
    result: list[tuple[str, str]] = []
    for pose, frame in primary:
        result.append((pose, frame))
        if pose in heldout:
            result.append((pose, heldout[pose]))
    return result


def _fit(tool: Any, selection: list[tuple[str, str]]) -> tuple[dict[str, Any], dict[str, float]]:
    raw = tool.calibrate_set(selection)
    return raw, tool.parameter_dict(raw)


def _change(before: float, after: float) -> dict[str, float | None]:
    absolute = after - before
    return {
        "before": before, "after": after, "absolute_change": absolute,
        "relative_change": absolute / before if before else None,
        "relative_change_percent": absolute / before * 100.0 if before else None,
    }


def _run_lopo(
    tool: Any, selection: list[tuple[str, str]], reference: dict[str, float], grid: np.ndarray,
    output_dir: Path, label: str, progress: Callable[[str], None],
) -> tuple[dict[str, Any], dict[str, dict[str, float]], list[dict[str, Any]]]:
    progress(f"{label} LOPO: calibrating {len(selection)} leave-one-pose-out fits")
    rows: list[dict[str, Any]] = []
    fits: dict[str, dict[str, float]] = {}
    for index, (removed, subset) in enumerate(leave_one_out_selections(selection), 1):
        raw, params = _fit(tool, subset)
        metrics, _ = compare_camera_solutions(reference, params, grid, tool.WIDTH, tool.HEIGHT)
        flat = _flatten_run_row(index, [p for p, _ in subset], {"rms": raw["rms"], "parameters": params}, metrics, reference)
        rows.append({"removed_pose_id": removed, **{k: v for k, v in flat.items() if k not in ("run_id", "included_pose_ids")}})
        fits[removed] = params
        if index % 5 == 0 or index == len(selection):
            progress(f"{label} LOPO progress: {index}/{len(selection)}")
    by_p95 = sorted(rows, key=lambda row: row["mapping_p95_px"], reverse=True)
    by_max = sorted(rows, key=lambda row: row["mapping_max_px"], reverse=True)
    b08 = next((row for row in rows if row["removed_pose_id"] == "B08"), None)
    distribution = _distribution(row["mapping_p95_px"] for row in rows)
    summary = {
        "schema_version": "stage2a_lopo_summary.v2", "dataset_label": label,
        "pose_count": len(selection), "run_count": len(rows), "runs": rows,
        "ranking_by_mapping_p95": [row["removed_pose_id"] for row in by_p95],
        "ranking_by_mapping_max": [row["removed_pose_id"] for row in by_max],
        "worst_by_mapping_p95": by_p95[0], "worst_by_mapping_max": by_max[0],
        "mapping_p95_distribution": distribution, "parameter_distribution": _parameter_summary(rows),
        "worst_over_median_ratio": by_p95[0]["mapping_p95_px"] / max(distribution["p50"], 1e-15),
        "B08": b08,
        "B08_rank_by_mapping_p95": (next(i for i, row in enumerate(by_p95, 1) if row["removed_pose_id"] == "B08") if b08 else None),
        "interpretation": "LOPO ranks influence only; it does not authorize removal or trimming.",
    }
    _write_csv(output_dir / "lopo_per_pose.csv", rows)
    _write_json(output_dir / "lopo_summary.json", summary)
    worst_pose = by_p95[0]["removed_pose_id"]
    _heatmap(output_dir / "worst_lopo_mapping.png", reference, fits[worst_pose], tool.WIDTH, tool.HEIGHT, f"{label}: remove {worst_pose}")
    if b08 and worst_pose != "B08":
        _heatmap(output_dir / "remove_B08_mapping.png", reference, fits["B08"], tool.WIDTH, tool.HEIGHT, f"{label}: remove B08")
    return summary, fits, rows


def _run_subsamples(
    tool: Any, selection: list[tuple[str, str]], reference: dict[str, float], grid: np.ndarray,
    output_dir: Path, label: str, runs: int, seed: int, fraction: float,
    progress: Callable[[str], None],
) -> tuple[dict[str, Any], dict[int, dict[str, float]], list[dict[str, Any]]]:
    subset_size = derive_subset_size(len(selection), fraction)
    sequences = deterministic_subsets(len(selection), subset_size, runs, seed)
    progress(f"{label} subsets: calibrating {runs} deterministic {subset_size}/{len(selection)} fits (seed={seed})")
    rows: list[dict[str, Any]] = []
    fits: dict[int, dict[str, float]] = {}
    for run_id, indices in enumerate(sequences, 1):
        subset = [selection[index] for index in indices]
        raw, params = _fit(tool, subset)
        metrics, _ = compare_camera_solutions(reference, params, grid, tool.WIDTH, tool.HEIGHT)
        rows.append(_flatten_run_row(run_id, [p for p, _ in subset], {"rms": raw["rms"], "parameters": params}, metrics, reference))
        fits[run_id] = params
        if run_id % 10 == 0 or run_id == runs:
            progress(f"{label} subset progress: {run_id}/{runs}")
    ranked = sorted(rows, key=lambda row: row["mapping_p95_px"], reverse=True)
    worst = ranked[0]
    pose_ids = [pose for pose, _ in selection]
    top_four = ranked[:4]
    omitted_sets = [set(pose_ids) - set(row["included_pose_ids"].split(";")) for row in top_four]
    influence = []
    for pose in pose_ids:
        omitted = [row["mapping_p95_px"] for row in rows if pose not in row["included_pose_ids"].split(";")]
        included = [row["mapping_p95_px"] for row in rows if pose in row["included_pose_ids"].split(";")]
        omitted_mean = float(np.mean(omitted)) if omitted else None
        included_mean = float(np.mean(included)) if included else None
        influence.append({
            "pose_id": pose, "omitted_run_count": len(omitted),
            "mean_p95_when_omitted_px": omitted_mean, "mean_p95_when_included_px": included_mean,
            "omitted_minus_included_mean_p95_px": (omitted_mean - included_mean if omitted_mean is not None and included_mean is not None else None),
        })
    influence.sort(key=lambda row: row["omitted_minus_included_mean_p95_px"] if row["omitted_minus_included_mean_p95_px"] is not None else -math.inf, reverse=True)
    distribution = _distribution(row["mapping_p95_px"] for row in rows)
    summary = {
        "schema_version": "stage2a_subsample_summary.v2", "dataset_label": label, "seed": seed,
        "run_count": runs, "pose_count": len(selection), "requested_fraction": fraction,
        "subset_size_rule": "ceil(N * requested_fraction)", "subset_size": subset_size,
        "actual_fraction": subset_size / len(selection), "without_replacement_within_each_run": True,
        "mapping_p95_distribution": distribution,
        "mapping_max_distribution": _distribution(row["mapping_max_px"] for row in rows),
        "angular_p95_distribution": _distribution(row["angular_p95_deg"] for row in rows),
        "parameter_distribution": _parameter_summary(rows), "worst_run": worst,
        "worst_over_median_ratio": worst["mapping_p95_px"] / max(distribution["p50"], 1e-15),
        "worst_run_omitted_pose_ids": [pose for pose in pose_ids if pose not in worst["included_pose_ids"].split(";")],
        "top_four_runs_by_mapping_p95": [{"run_id": row["run_id"], "mapping_p95_px": row["mapping_p95_px"]} for row in top_four],
        "top_four_common_omitted_pose_ids": sorted(set.intersection(*omitted_sets)) if omitted_sets else [],
        "pose_omission_influence_ranking": influence,
    }
    _write_csv(output_dir / "runs.csv", rows)
    _write_json(output_dir / "summary.json", summary)
    _heatmap(output_dir / "worst_mapping.png", reference, fits[int(worst["run_id"])], tool.WIDTH, tool.HEIGHT, f"{label}: worst subset {worst['run_id']}")
    return summary, fits, rows


def _anchor_check(tool: Any, fit: dict[str, Any], parameters: dict[str, float], candidate: dict[str, Any]) -> dict[str, Any]:
    parameter_deltas = {key: parameters[key] - PRE_SUPPLEMENT25_ANCHOR["parameters"][key] for key in PARAMETER_NAMES}
    observed_metrics: dict[str, float | None] = {"train_rms_px": float(fit["rms"]), "heldout_rms_px": None, "heldout_p95_px": None}
    if hasattr(tool, "heldout_metrics"):
        heldout_by_pose = {row["pose_id"]: row["frame_id"] for row in candidate.get("heldout_frames", [])}
        heldout = [(pose, heldout_by_pose[pose]) for pose in PRE_SUPPLEMENT25_POSE_IDS if pose in heldout_by_pose]
        metrics = tool.heldout_metrics(fit["K"], fit["dist"], heldout)
        observed_metrics.update({"heldout_rms_px": metrics["rms_px"], "heldout_p95_px": metrics["p95_px"]})
    metric_deltas = {
        key: (None if observed_metrics[key] is None else observed_metrics[key] - value)
        for key, value in PRE_SUPPLEMENT25_ANCHOR["metrics"].items()
    }
    comparable = all(value is not None for value in metric_deltas.values())
    consistent = max(abs(value) for value in parameter_deltas.values()) <= 1e-6 and (
        not comparable or max(abs(float(value)) for value in metric_deltas.values() if value is not None) <= 1e-6
    )
    return {
        "anchor": PRE_SUPPLEMENT25_ANCHOR, "reconstructed_parameters": parameters,
        "reconstructed_metrics": observed_metrics, "parameter_deltas": parameter_deltas,
        "metric_deltas": metric_deltas, "max_absolute_parameter_delta": max(abs(value) for value in parameter_deltas.values()),
        "verdict": "CONSISTENT" if consistent else "DIFFERS_REVIEW_EXPLANATION_REQUIRED",
        "explanation_if_different": (None if consistent else "The explicit A01-A04/B01-B08/C01-C13 rank-1 reconstruction differs from the supplied prior anchor; inspect parameter and metric deltas above."),
    }


def _dist_text(value: dict[str, float]) -> str:
    return "/".join(f"{value[key]:.6f}" for key in ("min", "p5", "p50", "p95", "max"))


def _percent_text(change: dict[str, float | None]) -> str:
    value = change["relative_change_percent"]
    return "n/a (zero baseline)" if value is None else f"{value:+.2f}%"


def _optional_number(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "n/a (no sampled runs)"
    return f"{value:+.6f}" if signed else f"{value:.6f}"


def _report_markdown(summary: dict[str, Any]) -> str:
    current, pre = summary["current"], summary["pre_supplement25"]
    cur_lopo, pre_lopo = current["lopo"], pre["lopo"]
    cur_sub, pre_sub = current["subsample"], pre["subsample"]
    cur_b08, pre_b08 = cur_lopo["B08"], pre_lopo["B08"]
    change = summary["before_after"]
    omissions = {row["pose_id"]: row for row in cur_sub["pose_omission_influence_ranking"]}
    lines = [
        "# Stage2A Camera Intrinsic Stability Diagnostics", "",
        f"Generated: `{summary['generated_at']}`", "",
        "Read-only diagnostic evidence only: no pose removal, candidate replacement, promotion, GO/FAIL gate, or Stage2B action was performed.", "",
        "## Dynamic dataset model", "",
        f"Current selected primary poses: **{current['pose_count']}**; LOPO runs: **{cur_lopo['run_count']}**.", "",
        f"Requested subset fraction: `{cur_sub['requested_fraction']}`; rule: `{cur_sub['subset_size_rule']}`; subset size: **{cur_sub['subset_size']}**; actual fraction: `{cur_sub['actual_fraction']:.6f}`.", "",
        "Historical pre-edge22 and pre-supplement25 are explicit pose-ID allow-lists, independent of the current N. Future selected poses are included in current full/top2/filtered/LOPO/subsample analysis without source edits.", "",
        "## Current full solution comparisons", "",
        f"Primary vs Top-2: {_fmt_metrics(current['comparisons']['primary_vs_top2'])}", "",
        f"Primary vs no-A04 diagnostic fit: {_fmt_metrics(current['comparisons']['primary_vs_filtered_A04'])}", "",
        f"Primary vs historical pre-edge22 subset: {_fmt_metrics(current['comparisons']['primary_vs_preedge22'])}", "",
        f"Primary vs pre-supplement25 subset: {_fmt_metrics(current['comparisons']['primary_vs_pre_supplement25'])}", "",
        "## C14/C15/C16 before/after", "",
        f"Pre25 anchor reconstruction verdict: **{pre['anchor_check']['verdict']}**; max absolute parameter delta `{pre['anchor_check']['max_absolute_parameter_delta']:.3g}`.", "",
        f"Pre25 Primary→Top2: {_fmt_metrics(pre['primary_vs_top2'])}", "",
        f"Current Primary→Top2: {_fmt_metrics(current['comparisons']['primary_vs_top2'])}", "",
        f"Top2 mapping p95 change: `{change['primary_top2_mapping_p95_px']['before']:.6f}` → `{change['primary_top2_mapping_p95_px']['after']:.6f}` px; absolute `{change['primary_top2_mapping_p95_px']['absolute_change']:+.6f}` px, relative `{_percent_text(change['primary_top2_mapping_p95_px'])}`.", "",
        f"Pre25 remove-B08: p50/p95/p99/max `{pre_b08['mapping_p50_px']:.6f}/{pre_b08['mapping_p95_px']:.6f}/{pre_b08['mapping_p99_px']:.6f}/{pre_b08['mapping_max_px']:.6f}` px; angle p95/max `{pre_b08['angular_p95_deg']:.6f}/{pre_b08['angular_max_deg']:.6f}` deg; 2m p95/max `{pre_b08['offset_2m_p95_mm']:.3f}/{pre_b08['offset_2m_max_mm']:.3f}` mm; worst `({pre_b08['worst_x']:.1f}, {pre_b08['worst_y']:.1f})`.", "",
        f"Current remove-B08: p50/p95/p99/max `{cur_b08['mapping_p50_px']:.6f}/{cur_b08['mapping_p95_px']:.6f}/{cur_b08['mapping_p99_px']:.6f}/{cur_b08['mapping_max_px']:.6f}` px; angle p95/max `{cur_b08['angular_p95_deg']:.6f}/{cur_b08['angular_max_deg']:.6f}` deg; 2m p95/max `{cur_b08['offset_2m_p95_mm']:.3f}/{cur_b08['offset_2m_max_mm']:.3f}` mm; worst `({cur_b08['worst_x']:.1f}, {cur_b08['worst_y']:.1f})`.", "",
        f"B08 p95 change: `{change['B08_lopo_mapping_p95_px']['absolute_change']:+.6f}` px (`{_percent_text(change['B08_lopo_mapping_p95_px'])}`); max change: `{change['B08_lopo_mapping_max_px']['absolute_change']:+.6f}` px (`{_percent_text(change['B08_lopo_mapping_max_px'])}`).", "",
        "## LOPO distributions", "",
        f"Pre25 min/p5/p50/p95/max: `{_dist_text(pre_lopo['mapping_p95_distribution'])}` px; worst `{pre_lopo['worst_by_mapping_p95']['removed_pose_id']}`; B08 rank `{pre_lopo['B08_rank_by_mapping_p95']}`; worst/median `{pre_lopo['worst_over_median_ratio']:.2f}x`.", "",
        f"Current min/p5/p50/p95/max: `{_dist_text(cur_lopo['mapping_p95_distribution'])}` px; worst `{cur_lopo['worst_by_mapping_p95']['removed_pose_id']}`; B08 rank `{cur_lopo['B08_rank_by_mapping_p95']}`; worst/median `{cur_lopo['worst_over_median_ratio']:.2f}x`.", "",
        "## Deterministic subset distributions", "",
        f"Pre25: N=25, size={pre_sub['subset_size']}, actual={pre_sub['actual_fraction']:.6f}, mapping p95 min/p5/p50/p95/max `{_dist_text(pre_sub['mapping_p95_distribution'])}` px, angular-p95 distribution `{_dist_text(pre_sub['angular_p95_distribution'])}` deg, worst run `{pre_sub['worst_run']['run_id']}`, worst/median `{pre_sub['worst_over_median_ratio']:.2f}x`.", "",
        f"Current: N={current['pose_count']}, size={cur_sub['subset_size']}, actual={cur_sub['actual_fraction']:.6f}, mapping p95 min/p5/p50/p95/max `{_dist_text(cur_sub['mapping_p95_distribution'])}` px, angular-p95 distribution `{_dist_text(cur_sub['angular_p95_distribution'])}` deg, worst run `{cur_sub['worst_run']['run_id']}`, worst/median `{cur_sub['worst_over_median_ratio']:.2f}x`.", "",
        f"Current B08 missing-vs-present mean p95: `{_optional_number(omissions['B08']['mean_p95_when_omitted_px'])}` vs `{_optional_number(omissions['B08']['mean_p95_when_included_px'])}` px (difference `{_optional_number(omissions['B08']['omitted_minus_included_mean_p95_px'], signed=True)}`).", "",
    ]
    for pose in ("C14", "C15", "C16"):
        row = omissions.get(pose)
        if row:
            lines += [f"{pose} missing-vs-present mean p95: `{_optional_number(row['mean_p95_when_omitted_px'])}` vs `{_optional_number(row['mean_p95_when_included_px'])}` px (difference `{_optional_number(row['omitted_minus_included_mean_p95_px'], signed=True)}`).", ""]
    lines += [
        "## Spatial sensitivity", "",
        f"Pre25 worst regional aggregate: `{pre['spatial']['worst_region']}`. Current worst regional aggregate: `{current['spatial']['worst_region']}`. Exact rankings and heatmaps are in the JSON/output folders.", "",
        "## Read-only regression protection", "",
        f"Manifest before/after: `{summary['protected_files']['workspace_manifest.json']['before']}` / `{summary['protected_files']['workspace_manifest.json']['after']}`.", "",
        f"Ranking before/after: `{summary['protected_files']['pose_frame_ranking.csv']['before']}` / `{summary['protected_files']['pose_frame_ranking.csv']['after']}`.", "",
        f"Candidate before/after: `{summary['protected_files']['candidate']['before']}` / `{summary['protected_files']['candidate']['after']}` (protected target `{summary['candidate_expected_sha256']}`).", "",
        f"All protected files byte-for-byte unchanged: **{summary['protected_files_unchanged']}**.", "",
    ]
    return "\n".join(lines)


def run_diagnostics(
    tool: Any, output_dir: Path, subsample_runs: int = 100, seed: int = 20260817,
    grid_step: int = 16, subsample_fraction: float = 0.8,
    progress: Callable[[str], None] = print, expected_candidate_sha256: str | None = None,
) -> dict[str, Any]:
    """Run dynamic current-N and explicit historical-subset diagnostics."""
    if subsample_runs < 1:
        raise tool.Stage2AError("--subsample-runs must be at least 1")
    try:
        derive_subset_size(1, subsample_fraction)
    except ValueError as exc:
        raise tool.Stage2AError(str(exc)) from exc
    protected = {
        "workspace_manifest.json": tool.MANIFEST_PATH,
        "pose_frame_ranking.csv": tool.WORKSPACE / "pose_frame_ranking.csv",
        "candidate": tool.CANDIDATE_PATH,
    }
    before = {name: tool.sha256_file(path) for name, path in protected.items()}
    protected_candidate_sha = expected_candidate_sha256 or before["candidate"]
    candidate = tool.load_json(tool.CANDIDATE_PATH)
    errors = tool.validate_candidate(candidate)
    if errors:
        raise tool.Stage2AError("candidate invalid: " + ", ".join(errors))
    manifest = tool.load_json(tool.MANIFEST_PATH)
    primary = [(row["pose_id"], row["frame_id"]) for row in candidate["selected_frames"]]
    unique_poses = {pose for pose, _ in primary}
    if len(unique_poses) != len(primary):
        raise tool.Stage2AError(f"selected primary poses must be unique: entries={len(primary)}, unique={len(unique_poses)}")
    if len(primary) < MIN_SELECTED_POSES:
        raise tool.Stage2AError(f"stability diagnostics require at least {MIN_SELECTED_POSES} selected poses for LOPO; found {len(primary)}")
    try:
        manifest_primary = [(pose, manifest["poses"][pose].get("selected_frame")) for pose, _ in primary]
    except KeyError as exc:
        raise tool.Stage2AError(f"candidate selected pose missing from manifest: {exc.args[0]}") from exc
    if primary != manifest_primary:
        raise tool.Stage2AError("candidate selected frames do not match current manifest/ranking selection")
    with protected["pose_frame_ranking.csv"].open("r", encoding="utf-8-sig", newline="") as stream:
        ranking_rows = list(csv.DictReader(stream))
    rank1 = {(row["pose_id"], row["frame_id"]) for row in ranking_rows if row.get("rank") == "1"}
    missing_rank1 = [item for item in primary if item not in rank1]
    if missing_rank1:
        raise tool.Stage2AError(f"selected primary frames are not rank 1 in ranking CSV: {missing_rank1}")
    try:
        preedge_selection = reconstruct_preedge22(primary)
        pre25_selection = reconstruct_pre_supplement25(primary)
    except ValueError as exc:
        raise tool.Stage2AError(str(exc)) from exc

    current_subset_size = derive_subset_size(len(primary), subsample_fraction)
    progress(f"selected poses: {len(primary)}")
    progress(f"LOPO runs: {len(primary)}")
    progress(f"subsample fraction requested: {subsample_fraction}")
    progress(f"subsample size: {current_subset_size} (actual fraction {current_subset_size / len(primary):.6f})")

    reference = {key: float(candidate["strategy_stability"]["primary"][key]) for key in PARAMETER_NAMES}
    ref_K, _ = params_to_camera(reference)
    grid = make_mapping_grid(tool.WIDTH, tool.HEIGHT, grid_step, ref_K)
    output_dir.mkdir(parents=True, exist_ok=True)
    masks = region_masks(grid, tool.WIDTH, tool.HEIGHT)
    _write_csv(output_dir / "mapping_grid.csv", [
        {"point_index": index, "raw_x": x, "raw_y": y, "regions": ";".join(name for name, mask in masks.items() if mask[index])}
        for index, (x, y) in enumerate(grid)
    ])

    progress("current direct comparisons: top2, no-A04, historical pre-edge22, pre-supplement25")
    top2 = {key: float(candidate["strategy_stability"]["top2"][key]) for key in PARAMETER_NAMES}
    filtered = {key: float(candidate["strategy_stability"]["small_outlier_filtered"][key]) for key in PARAMETER_NAMES}
    preedge_fit, preedge = _fit(tool, preedge_selection)
    pre25_fit, pre25 = _fit(tool, pre25_selection)
    direct_specs = {
        "primary_vs_top2": ("current_primary_vs_current_top2", top2, {}),
        "primary_vs_filtered_A04": ("current_primary_vs_filtered_without_A04", filtered, {
            "diagnostic_only_excluded": candidate["outlier_diagnostics"]["excluded_for_diagnostic_only"], "candidate_replaced": False,
        }),
        "primary_vs_preedge22": ("current_primary_vs_historical_preedge22", preedge, {
            "comparison_type": "historical subset comparison", "explicit_pose_ids": list(PREEDGE22_POSE_IDS),
            "selection": [{"pose_id": p, "frame_id": f} for p, f in preedge_selection], "calibration_rms_px": preedge_fit["rms"],
            "historical_parameter_check": {
                "historical_values": HISTORICAL_PREEDGE22,
                "reconstructed_minus_historical": {key: preedge[key] - HISTORICAL_PREEDGE22[key] for key in PARAMETER_NAMES},
                "max_absolute_delta": max(abs(preedge[key] - HISTORICAL_PREEDGE22[key]) for key in PARAMETER_NAMES),
            },
        }),
        "primary_vs_pre_supplement25": ("current_primary_vs_pre_supplement25", pre25, {
            "comparison_type": "before/after explicit subset comparison", "explicit_pose_ids": list(PRE_SUPPLEMENT25_POSE_IDS),
            "excluded_pose_ids": [pose for pose, _ in primary if pose not in PRE_SUPPLEMENT25_POSE_IDS],
            "calibration_rms_px": pre25_fit["rms"],
        }),
    }
    comparisons: dict[str, Any] = {}
    for key, (name, params, extra) in direct_specs.items():
        metrics, _ = compare_camera_solutions(reference, params, grid, tool.WIDTH, tool.HEIGHT)
        comparisons[key] = _comparison_doc(name, reference, params, metrics, extra)
        _write_json(output_dir / "comparisons" / f"{key}.json", comparisons[key])
        _heatmap(output_dir / "comparisons" / f"{key}_mapping.png", reference, params, tool.WIDTH, tool.HEIGHT, name)

    current_lopo, current_lopo_fits, _ = _run_lopo(tool, primary, reference, grid, output_dir / "lopo", "current", progress)
    current_sub, current_sub_fits, _ = _run_subsamples(
        tool, primary, reference, grid, output_dir / "subsample80", "current", subsample_runs, seed, subsample_fraction, progress,
    )

    progress("pre-supplement25 direct Top2 reconstruction")
    pre25_top2_fit, pre25_top2 = _fit(tool, _top2_selection(pre25_selection, candidate))
    pre25_top2_metrics, _ = compare_camera_solutions(pre25, pre25_top2, grid, tool.WIDTH, tool.HEIGHT)
    pre25_top2_doc = _comparison_doc("pre_supplement25_primary_vs_top2", pre25, pre25_top2, pre25_top2_metrics, {
        "primary_fit_rms_px": pre25_fit["rms"], "top2_fit_rms_px": pre25_top2_fit["rms"],
    })
    pre_dir = output_dir / "pre_supplement25"
    _write_json(pre_dir / "primary_vs_top2.json", pre25_top2_doc)
    _heatmap(pre_dir / "primary_vs_top2_mapping.png", pre25, pre25_top2, tool.WIDTH, tool.HEIGHT, "pre25 primary vs top2")
    pre_lopo, pre_lopo_fits, _ = _run_lopo(tool, pre25_selection, pre25, grid, pre_dir / "lopo", "pre25", progress)
    pre_sub, pre_sub_fits, _ = _run_subsamples(
        tool, pre25_selection, pre25, grid, pre_dir / "subsample80", "pre25", subsample_runs, seed, subsample_fraction, progress,
    )

    def spatial_docs(ref: dict[str, float], direct: list[dict[str, Any]], lopo: dict[str, Any], lopo_fits: dict[str, dict[str, float]], sub: dict[str, Any], sub_fits: dict[int, dict[str, float]]) -> list[dict[str, Any]]:
        worst_pose = lopo["worst_by_mapping_p95"]["removed_pose_id"]
        worst_run = int(sub["worst_run"]["run_id"])
        lopo_metrics, _ = compare_camera_solutions(ref, lopo_fits[worst_pose], grid, tool.WIDTH, tool.HEIGHT)
        sub_metrics, _ = compare_camera_solutions(ref, sub_fits[worst_run], grid, tool.WIDTH, tool.HEIGHT)
        return direct + [_comparison_doc("worst_lopo", ref, lopo_fits[worst_pose], lopo_metrics), _comparison_doc("worst_subset", ref, sub_fits[worst_run], sub_metrics)]

    pre_spatial = _spatial_worst(spatial_docs(pre25, [pre25_top2_doc], pre_lopo, pre_lopo_fits, pre_sub, pre_sub_fits))
    current_spatial = _spatial_worst(spatial_docs(reference, list(comparisons.values()), current_lopo, current_lopo_fits, current_sub, current_sub_fits))
    pre_anchor = _anchor_check(tool, pre25_fit, pre25, candidate)
    pre_top2_p95 = pre25_top2_doc["mapping"]["full_image"]["equivalent_rectified_displacement_px"]["p95"]
    cur_top2_p95 = comparisons["primary_vs_top2"]["mapping"]["full_image"]["equivalent_rectified_displacement_px"]["p95"]
    before_after = {
        "primary_top2_mapping_p95_px": _change(pre_top2_p95, cur_top2_p95),
        "B08_lopo_mapping_p95_px": _change(pre_lopo["B08"]["mapping_p95_px"], current_lopo["B08"]["mapping_p95_px"]),
        "B08_lopo_mapping_max_px": _change(pre_lopo["B08"]["mapping_max_px"], current_lopo["B08"]["mapping_max_px"]),
        "lopo_mapping_p95_distribution": {key: _change(pre_lopo["mapping_p95_distribution"][key], current_lopo["mapping_p95_distribution"][key]) for key in ("min", "p5", "p50", "p95", "max")},
        "subsample_mapping_p95_distribution": {key: _change(pre_sub["mapping_p95_distribution"][key], current_sub["mapping_p95_distribution"][key]) for key in ("min", "p5", "p50", "p95", "max")},
    }
    before_after_metrics, _ = compare_camera_solutions(pre25, reference, grid, tool.WIDTH, tool.HEIGHT)
    before_after["pre_supplement25_vs_current_primary"] = _comparison_doc("pre_supplement25_vs_current_primary", pre25, reference, before_after_metrics)
    _write_json(output_dir / "pre25_vs_current28_summary.json", before_after)

    selected = set(primary)
    selected_coverage = {
        row["pose_id"]: {key: float(row.get(key) or 0.0) for key in ("board_center_x", "board_center_y", "board_coverage", "viewing_angle_proxy_deg")}
        for row in ranking_rows if (row["pose_id"], row["frame_id"]) in selected
    }
    after = {name: tool.sha256_file(path) for name, path in protected.items()}
    protected_doc = {name: {"path": str(path), "before": before[name], "after": after[name], "unchanged": before[name] == after[name]} for name, path in protected.items()}
    summary = {
        "schema_version": "stage2a_stability_summary.v2", "generated_at": tool.now(),
        "scope": f"current physical camera's dynamic {len(primary)}-pose selection plus explicit historical subsets; old Stage7A excluded",
        "grid": {"width": tool.WIDTH, "height": tool.HEIGHT, "step_px": grid_step, "point_count": len(grid),
                 "region_definitions": "centre middle 50%; border outer 15%; quadrants split at centre; corners outer 20% in both axes"},
        "selection_validation": {"pose_count": len(primary), "minimum_pose_count": MIN_SELECTED_POSES, "candidate_matches_manifest": True,
                                 "selected_frames_are_rank1": True, "selected_frames": [{"pose_id": p, "frame_id": f} for p, f in primary]},
        "selected_coverage": selected_coverage,
        "current": {"pose_count": len(primary), "reference_parameters": reference, "comparisons": comparisons,
                    "lopo": current_lopo, "subsample": current_sub, "spatial": current_spatial},
        "pre_supplement25": {"pose_count": len(pre25_selection), "explicit_pose_ids": list(PRE_SUPPLEMENT25_POSE_IDS),
                             "reference_parameters": pre25, "anchor_check": pre_anchor, "primary_vs_top2": pre25_top2_doc,
                             "lopo": pre_lopo, "subsample": pre_sub, "spatial": pre_spatial},
        "historical_preedge22": {"comparison_type": "historical subset comparison", "explicit_pose_ids": list(PREEDGE22_POSE_IDS),
                                 "pose_count": len(preedge_selection), "parameters": preedge},
        "comparisons": comparisons, "lopo": current_lopo, "subsample80": current_sub, "spatial": current_spatial,
        "before_after": before_after, "protected_files": protected_doc,
        "protected_files_unchanged": all(item["unchanged"] for item in protected_doc.values()),
        "candidate_expected_sha256": protected_candidate_sha,
        "automatic_gate_or_pose_removal": False, "promotion_performed": False,
    }
    _write_json(output_dir / "stability_summary.json", summary)
    (output_dir / "stability_summary.md").write_text(_report_markdown(summary), encoding="utf-8", newline="\n")
    if not summary["protected_files_unchanged"]:
        raise tool.Stage2AError("read-only regression failed: protected input changed during diagnostics")
    if after["candidate"] != protected_candidate_sha:
        raise tool.Stage2AError(f"candidate SHA-256 changed/unexpected: {after['candidate']}")
    progress(f"diagnostics complete: {output_dir}")
    return summary
