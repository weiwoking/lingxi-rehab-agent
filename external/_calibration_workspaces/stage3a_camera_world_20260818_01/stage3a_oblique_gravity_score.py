from __future__ import annotations

import argparse
import inspect
import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

import stage3a_straightness_score as base


WORKSPACE = Path(__file__).resolve().parent
OUT = WORKSPACE / "diagnostics" / "oblique_gravity_20260818"
CORE_METHODS = ("ITERATIVE", "IPPE", "SQPNP")
METHOD_FLAGS = {
    "ITERATIVE": cv2.SOLVEPNP_ITERATIVE,
    "IPPE": cv2.SOLVEPNP_IPPE,
    "SQPNP": cv2.SOLVEPNP_SQPNP,
    "EPNP": cv2.SOLVEPNP_EPNP,
}

# Conservative image-shape envelope corresponding to the broad tested plateau.
# These are internal image proxies; no field-angle target is exposed to the user.
GOOD_MIN_OBLIQUITY_PROXY = 0.30
GOOD_MAX_OBLIQUITY_PROXY = 0.78
MIN_OUTER_EDGE_PX = 50.0
MIN_MARGIN_PX = 12.0
MAX_CORE_SOLVER_DISAGREEMENT_DEG = 0.75
MIN_CORNERS = 6


def normalize(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float64).reshape(3)
    norm = float(np.linalg.norm(value))
    if norm <= 0:
        raise ValueError("zero vector")
    return value / norm


def angular_distance_deg(first: np.ndarray, second: np.ndarray) -> float:
    return math.degrees(math.acos(float(np.clip(np.dot(normalize(first), normalize(second)), -1.0, 1.0))))


def rotation_distance_deg(first: np.ndarray, second: np.ndarray) -> float:
    cosine = np.clip((np.trace(first.T @ second) - 1.0) / 2.0, -1.0, 1.0)
    return math.degrees(math.acos(float(cosine)))


def vector_mean(vectors: list[np.ndarray]) -> np.ndarray:
    if not vectors:
        raise ValueError("empty vector collection")
    return normalize(np.sum(np.asarray(vectors, dtype=np.float64), axis=0))


def distribution(values: list[float]) -> dict[str, float | int]:
    data = np.asarray(values, dtype=np.float64)
    if data.size == 0:
        return {"count": 0}
    return {
        "count": int(data.size),
        "min": float(np.min(data)),
        "p05": float(np.percentile(data, 5)),
        "median": float(np.median(data)),
        "p95": float(np.percentile(data, 95)),
        "max": float(np.max(data)),
        "rms": float(np.sqrt(np.mean(data**2))),
    }


def gravity_from_R_camera_from_board(R_camera_from_board: np.ndarray) -> np.ndarray:
    return -normalize(np.asarray(R_camera_from_board, dtype=np.float64)[:, 1])


def gravity_yaw_invariance_numeric() -> dict[str, Any]:
    gravity = normalize(np.asarray([0.006, -0.9998, 0.017], dtype=np.float64))
    down = -gravity
    reference_x = np.asarray([1.0, 0.0, 0.0])
    reference_x -= down * float(np.dot(reference_x, down))
    reference_x = normalize(reference_x)
    rows = []
    for yaw in (-170, -90, -45, 0, 45, 90, 170):
        x_axis = Rotation.from_rotvec(gravity * math.radians(yaw)).apply(reference_x)
        x_axis = normalize(x_axis - down * float(np.dot(x_axis, down)))
        R_cb = np.column_stack((x_axis, down, normalize(np.cross(x_axis, down))))
        recovered = gravity_from_R_camera_from_board(R_cb)
        rows.append({"internal_yaw_deg": yaw, "gravity_error_deg": angular_distance_deg(gravity, recovered)})
    return {
        "formula": "u_up_camera = -normalize(R_camera_from_board[:,1])",
        "proof": "right-multiplying board/world orientation by a yaw about world-up leaves the world-up axis fixed; expressed in the yawing camera, pure yaw also leaves up coordinates fixed",
        "rows": rows,
        "max_numeric_error_deg": max(row["gravity_error_deg"] for row in rows),
        "pass": max(row["gravity_error_deg"] for row in rows) < 1e-9,
    }


def solve_frame(frame: base.DetectedFrame, K: np.ndarray, D: np.ndarray) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for method, flag in METHOD_FLAGS.items():
        try:
            result = cv2.solvePnPGeneric(frame.object_points, frame.image_points, K, D, flags=flag)
        except cv2.error:
            output[method] = []
            continue
        solutions = []
        if result[0]:
            for rvec, tvec in zip(result[1], result[2]):
                R_cb, _ = cv2.Rodrigues(rvec)
                t = np.asarray(tvec, dtype=np.float64).reshape(3)
                projected, _ = cv2.projectPoints(frame.object_points, rvec, t, K, D)
                projected = projected.reshape(-1, 2)
                rms = float(np.sqrt(np.mean(np.sum((projected - frame.image_points) ** 2, axis=1))))
                depth = (R_cb @ frame.object_points.T).T[:, 2] + t[2]
                solutions.append(
                    {
                        "R_cb": R_cb,
                        "tvec_m": t,
                        "gravity": gravity_from_R_camera_from_board(R_cb),
                        "rms_px": rms,
                        "positive_depth": bool(np.all(depth > 0)),
                    }
                )
        output[method] = sorted(solutions, key=lambda item: (not item["positive_depth"], item["rms_px"]))
    return output


def selected_solution(solutions: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((item for item in solutions if item["positive_depth"]), None)


def shape_metrics(frame: base.DetectedFrame, K: np.ndarray, D: np.ndarray) -> dict[str, Any]:
    geometry = base.frame_geometry(frame, K, D)
    edges = [
        geometry["left_edge_length_px"],
        geometry["right_edge_length_px"],
        geometry["top_edge_length_px"],
        geometry["bottom_edge_length_px"],
    ]
    return {
        "obliquity_proxy": abs(float(geometry["normalized_homography_perspective_x"])),
        "signed_obliquity_proxy": float(geometry["normalized_homography_perspective_x"]),
        "trapezoid_asymmetry": float(geometry["quadrilateral_horizontal_trapezoid_asymmetry"]),
        "minimum_outer_edge_px": float(min(edges)),
        "minimum_margin_px": float(geometry["minimum_image_edge_margin_px"]),
        "board_center_x_px": float(geometry["board_center_x_px"]),
        "corner_count": int(geometry["corner_count"]),
        "homography_rms_px": float(geometry["homography_rms_px"]),
    }


def frame_analysis(frame: base.DetectedFrame, K: np.ndarray, D: np.ndarray) -> dict[str, Any]:
    shape = shape_metrics(frame, K, D)
    methods = solve_frame(frame, K, D)
    selected = {name: selected_solution(rows) for name, rows in methods.items()}
    core = [selected[name]["gravity"] for name in CORE_METHODS if selected[name] is not None]
    disagreement = (
        max(angular_distance_deg(first, second) for index, first in enumerate(core) for second in core[index + 1 :])
        if len(core) >= 2
        else float("inf")
    )
    ippe = methods["IPPE"]
    ippe_branch = {
        "solution_count": len(ippe),
        "reprojection_gap_px": None,
        "gravity_separation_deg": None,
    }
    if len(ippe) >= 2:
        ippe_branch["reprojection_gap_px"] = float(ippe[1]["rms_px"] - ippe[0]["rms_px"])
        ippe_branch["gravity_separation_deg"] = angular_distance_deg(ippe[0]["gravity"], ippe[1]["gravity"])
    return {
        "frame": frame.name,
        "shape": shape,
        "core_gravity": None if not core else vector_mean(core).tolist(),
        "core_solver_disagreement_deg": disagreement,
        "methods": {
            name: {
                "solution_count": len(methods[name]),
                "selected_gravity": None if selected[name] is None else selected[name]["gravity"].tolist(),
                "selected_reprojection_rms_px": None if selected[name] is None else selected[name]["rms_px"],
            }
            for name in METHOD_FLAGS
        },
        "ippe_branch": ippe_branch,
    }


def action_for(decision: str) -> str:
    return {
        "GOOD": "Hold the vehicle still; the single scan can now be taken.",
        "TOO_FRONTAL": "Move the vehicle sideways more and aim back at the fixed board.",
        "TOO_OBLIQUE": "Reduce the sideways offset slightly.",
        "BOARD_TOO_SMALL": "Move somewhat closer.",
        "BOARD_TOO_CLOSE_TO_EDGE": "Reposition the vehicle so the full board has margin.",
        "DETECTION_WEAK": "Improve board visibility and lighting, then score again.",
        "GRAVITY_SOLVER_DISAGREEMENT": "Keep the full board clear and steady, then score another still frame.",
    }[decision]


def aggregate_analysis(
    frames: list[base.DetectedFrame], K: np.ndarray, D: np.ndarray, input_count: int | None = None,
    include_lopo: bool = True,
) -> dict[str, Any]:
    rows = []
    for frame in frames:
        try:
            rows.append(frame_analysis(frame, K, D))
        except (cv2.error, ValueError, np.linalg.LinAlgError):
            continue
    total = len(frames) if input_count is None else input_count
    if not rows:
        decision = "DETECTION_WEAK"
        return {
            "decision": decision,
            "confidence": "LOW",
            "action": action_for(decision),
            "valid_frame_count": 0,
            "input_frame_count": total,
            "gravity_authority": None,
            "frames": [],
        }

    valid_fraction = len(rows) / max(1, total)
    shape_summary = {
        key: distribution([float(row["shape"][key]) for row in rows])
        for key in (
            "obliquity_proxy", "signed_obliquity_proxy", "trapezoid_asymmetry",
            "minimum_outer_edge_px", "minimum_margin_px", "board_center_x_px",
            "corner_count", "homography_rms_px",
        )
    }
    method_vectors: dict[str, np.ndarray] = {}
    for method in METHOD_FLAGS:
        vectors = [
            np.asarray(row["methods"][method]["selected_gravity"], dtype=np.float64)
            for row in rows if row["methods"][method]["selected_gravity"] is not None
        ]
        if vectors:
            method_vectors[method] = vector_mean(vectors)
    core_vectors = [method_vectors[name] for name in CORE_METHODS if name in method_vectors]
    core_disagreement = (
        max(angular_distance_deg(first, second) for index, first in enumerate(core_vectors) for second in core_vectors[index + 1 :])
        if len(core_vectors) >= 2 else float("inf")
    )
    final_up = vector_mean(core_vectors) if core_vectors else None
    frame_core_vectors = [np.asarray(row["core_gravity"], dtype=np.float64) for row in rows if row["core_gravity"] is not None]
    frame_spread = [] if final_up is None else [angular_distance_deg(vector, final_up) for vector in frame_core_vectors]

    lopo_errors: list[float] = []
    if include_lopo and final_up is not None:
        for _, model_K, model_D in base.lopo_camera_models():
            model_vectors = []
            for frame in frames:
                solved = solve_frame(frame, model_K, model_D)
                selected = [selected_solution(solved[name]) for name in CORE_METHODS]
                candidates = [item["gravity"] for item in selected if item is not None]
                if candidates:
                    model_vectors.append(vector_mean(candidates))
            if model_vectors:
                lopo_errors.append(angular_distance_deg(vector_mean(model_vectors), final_up))

    proxy = float(shape_summary["obliquity_proxy"]["median"])
    if valid_fraction < 0.6 or shape_summary["corner_count"]["median"] < MIN_CORNERS:
        decision = "DETECTION_WEAK"
    elif shape_summary["minimum_outer_edge_px"]["median"] < MIN_OUTER_EDGE_PX:
        decision = "BOARD_TOO_SMALL"
    elif shape_summary["minimum_margin_px"]["median"] < MIN_MARGIN_PX:
        decision = "BOARD_TOO_CLOSE_TO_EDGE"
    elif proxy < GOOD_MIN_OBLIQUITY_PROXY:
        decision = "TOO_FRONTAL"
    elif proxy > GOOD_MAX_OBLIQUITY_PROXY:
        decision = "TOO_OBLIQUE"
    elif core_disagreement > MAX_CORE_SOLVER_DISAGREEMENT_DEG or final_up is None:
        decision = "GRAVITY_SOLVER_DISAGREEMENT"
    else:
        decision = "GOOD"

    spread_stats = distribution(frame_spread)
    lopo_stats = distribution(lopo_errors)
    uncertainty = None
    if final_up is not None:
        uncertainty = max(
            float(spread_stats.get("p95", 0.0)),
            core_disagreement,
            float(lopo_stats.get("p95", 0.0)),
        )
    confidence_value = valid_fraction
    confidence_value *= math.exp(-max(0.0, core_disagreement - 0.1)) if math.isfinite(core_disagreement) else 0.0
    confidence_value *= min(1.0, shape_summary["corner_count"]["median"] / 12.0)
    confidence = "HIGH" if confidence_value >= 0.75 else "MEDIUM" if confidence_value >= 0.45 else "LOW"
    if decision in {"DETECTION_WEAK", "GRAVITY_SOLVER_DISAGREEMENT"}:
        confidence = "LOW"
    authority = None
    gravity_status = "PREVIEW_NOT_READY"
    if decision == "GOOD" and final_up is not None and len(rows) >= 11:
        gravity_status = "READY_FOR_CROSS_DISTANCE_VALIDATION"
        authority = {
            "u_up_camera": final_up.tolist(),
            "uncertainty_deg": uncertainty,
            "representation_policy": "VECTOR_FIRST",
            "vector_first": True,
            "matrix_first": False,
            "source_pose": "W04G",
            "gravity_status": gravity_status,
            "yaw_status": "SEPARATE_LOW_CONFIDENCE",
            "full_camera_world_rotation_authority": False,
        }
    return {
        "schema_version": "stage3a_oblique_gravity_preview.v1",
        "decision": decision,
        "confidence": confidence,
        "confidence_value_0_1": float(confidence_value),
        "action": action_for(decision),
        "valid_frame_count": len(rows),
        "input_frame_count": total,
        "shape": shape_summary,
        "solver": {
            "core_methods": list(CORE_METHODS),
            "epnp_role": "diagnostic_only",
            "aggregate_gravity_by_method": {name: value.tolist() for name, value in method_vectors.items()},
            "core_aggregate_disagreement_deg": core_disagreement,
            "frame_to_frame_gravity_spread_deg": spread_stats,
            "lopo_gravity_change_from_nominal_deg": lopo_stats,
        },
        "gravity_status": gravity_status,
        "yaw_status": "SEPARATE_LOW_CONFIDENCE",
        "gravity_authority": authority,
        "frames": rows,
        "capture_or_registration_performed": False,
    }


def print_human(result: dict[str, Any]) -> None:
    print(f"decision = {result['decision']}")
    print(f"confidence = {result['confidence']}")
    print(f"action = {result['action']}")


def reference_x_for_up(up: np.ndarray) -> np.ndarray:
    down = -normalize(up)
    reference = np.asarray([1.0, 0.0, 0.0])
    reference -= down * float(np.dot(reference, down))
    if np.linalg.norm(reference) < 1e-6:
        reference = np.asarray([0.0, 0.0, 1.0])
        reference -= down * float(np.dot(reference, down))
    return normalize(reference)


def rotation_from_gravity_and_yaw(up: np.ndarray, yaw_rad: float) -> np.ndarray:
    up = normalize(up)
    down = -up
    x_axis = Rotation.from_rotvec(up * float(yaw_rad)).apply(reference_x_for_up(up))
    x_axis = normalize(x_axis - down * float(np.dot(x_axis, down)))
    return np.column_stack((x_axis, down, normalize(np.cross(x_axis, down))))


def yaw_from_rotation_and_gravity(R_cb: np.ndarray, up: np.ndarray) -> float:
    reference = reference_x_for_up(up)
    x_axis = normalize(R_cb[:, 0] - (-normalize(up)) * float(np.dot(R_cb[:, 0], -normalize(up))))
    return math.atan2(float(np.dot(np.cross(reference, x_axis), normalize(up))), float(np.dot(reference, x_axis)))


def gravity_fixed_fit(
    object_points: np.ndarray, image_points: np.ndarray, K: np.ndarray, D: np.ndarray, u_up_camera: np.ndarray
) -> dict[str, Any]:
    """Fit yaw nuisance plus translation. No physical height truth enters this fit layer."""
    ok, rvec, tvec = cv2.solvePnP(object_points, image_points, K, D, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        raise RuntimeError("initial PnP failed")
    R_initial, _ = cv2.Rodrigues(rvec)
    x0 = np.r_[yaw_from_rotation_and_gravity(R_initial, u_up_camera), np.asarray(tvec).reshape(3)]

    def residual(parameters: np.ndarray) -> np.ndarray:
        R_cb = rotation_from_gravity_and_yaw(u_up_camera, float(parameters[0]))
        fitted_rvec, _ = cv2.Rodrigues(R_cb)
        projected, _ = cv2.projectPoints(object_points, fitted_rvec, parameters[1:4], K, D)
        return (projected.reshape(-1, 2) - image_points).reshape(-1)

    fit = least_squares(
        residual, x0, method="lm", ftol=1e-13, xtol=1e-13, gtol=1e-13, max_nfev=500
    )
    R_cb = rotation_from_gravity_and_yaw(u_up_camera, float(fit.x[0]))
    errors = residual(fit.x).reshape(-1, 2)
    return {
        "R_cb": R_cb,
        "tvec_m": fit.x[1:4],
        "yaw_nuisance_rad": float(fit.x[0]),
        "reprojection_rms_px": float(np.sqrt(np.mean(np.sum(errors**2, axis=1)))),
        "fit_parameter_count": 4,
        "fit_uses_physical_height_truth": False,
        "success": bool(fit.success),
    }


def validate_height(fit: dict[str, Any], u_up_camera: np.ndarray, camera_height_mm: float = 972.0) -> dict[str, float]:
    R_cb = fit["R_cb"]
    tvec = fit["tvec_m"]
    bottom_camera = (R_cb @ base.BOTTOM_POINTS.T).T + tvec
    top_points = np.asarray([[0.0, 0.0, 0.0], [base.BOARD_WIDTH_M, 0.0, 0.0]])
    top_camera = (R_cb @ top_points.T).T + tvec
    bottom_z = float(camera_height_mm + 1000.0 * np.mean(bottom_camera @ normalize(u_up_camera)))
    top_z = float(camera_height_mm + 1000.0 * np.mean(top_camera @ normalize(u_up_camera)))
    return {
        "reconstructed_bottom_z_mm": bottom_z,
        "bottom_signed_residual_mm": bottom_z - 912.0,
        "reconstructed_top_z_mm": top_z,
        "top_signed_residual_mm": top_z - 1037.0,
    }


def synthetic_pose(
    up: np.ndarray, internal_yaw_deg: float, distance_m: float, lateral_m: float = 0.0
) -> tuple[np.ndarray, np.ndarray]:
    R_cb = rotation_from_gravity_and_yaw(up, math.radians(internal_yaw_deg))
    center_camera = np.asarray([lateral_m, 0.0, distance_m], dtype=np.float64)
    center_camera += normalize(up) * (0.0025 - float(np.dot(normalize(up), center_camera)))
    return R_cb, center_camera - R_cb @ base.BOARD_CENTER


def synthetic_frame(
    objects: np.ndarray, up: np.ndarray, yaw_deg: float, distance_m: float, lateral_m: float,
    noise_px: float, seed: int, K_project: np.ndarray, D_project: np.ndarray,
    K_solve: np.ndarray, D_solve: np.ndarray, name: str,
) -> tuple[base.DetectedFrame, np.ndarray, np.ndarray]:
    R_cb, tvec = synthetic_pose(up, yaw_deg, distance_m, lateral_m)
    rvec, _ = cv2.Rodrigues(R_cb)
    image, _ = cv2.projectPoints(objects, rvec, tvec, K_project, D_project)
    image = image.reshape(-1, 2)
    image += np.random.default_rng(seed).normal(0.0, noise_px, image.shape)
    frame = base.DetectedFrame(name, objects, image, np.arange(len(objects), dtype=np.int32), 17)
    return frame, R_cb, tvec


def synthetic_scan_metrics(
    up: np.ndarray, yaw_deg: float, distance_m: float, noise_px: float, trial: int,
    visibility: list[np.ndarray], objects: np.ndarray, K_project: np.ndarray, D_project: np.ndarray,
    K_solve: np.ndarray, D_solve: np.ndarray,
) -> dict[str, Any]:
    frame_rows = []
    method_vectors: dict[str, list[np.ndarray]] = {name: [] for name in METHOD_FLAGS}
    method_errors: dict[str, list[float]] = {name: [] for name in METHOD_FLAGS}
    reprojection: dict[str, list[float]] = {name: [] for name in METHOD_FLAGS}
    branch_identifiable = []
    branch_selected_correct = []
    branch_gravity_separation = []
    core_frame_vectors = []
    for frame_index in range(11):
        ids = visibility[frame_index % len(visibility)]
        selected_objects = objects[ids]
        seed = 20_000_000 + int(yaw_deg * 10000) + int(distance_m * 1000) * 100 + int(noise_px * 100) * 10 + trial * 1000 + frame_index
        frame, R_true, _ = synthetic_frame(
            selected_objects, up, yaw_deg, distance_m, 0.0, noise_px, seed,
            K_project, D_project, K_solve, D_solve, f"synthetic_{frame_index}",
        )
        solved = solve_frame(frame, K_solve, D_solve)
        selected: dict[str, dict[str, Any] | None] = {name: selected_solution(solved[name]) for name in METHOD_FLAGS}
        core = []
        for method, item in selected.items():
            if item is not None:
                method_vectors[method].append(item["gravity"])
                method_errors[method].append(angular_distance_deg(item["gravity"], up))
                reprojection[method].append(item["rms_px"])
                if method in CORE_METHODS:
                    core.append(item["gravity"])
        if core:
            core_frame_vectors.append(vector_mean(core))
        ippe = solved["IPPE"]
        if len(ippe) >= 2:
            gap = ippe[1]["rms_px"] - ippe[0]["rms_px"]
            branch_identifiable.append(gap > max(0.15, 2.0 * noise_px))
            selected_rotation_error = rotation_distance_deg(ippe[0]["R_cb"], R_true)
            alternate_rotation_error = rotation_distance_deg(ippe[1]["R_cb"], R_true)
            branch_selected_correct.append(selected_rotation_error <= alternate_rotation_error)
            branch_gravity_separation.append(angular_distance_deg(ippe[0]["gravity"], ippe[1]["gravity"]))
        shape = shape_metrics(frame, K_solve, D_solve)
        frame_rows.append(shape)

    aggregate_methods = {name: vector_mean(values) for name, values in method_vectors.items() if values}
    core_aggregate = [aggregate_methods[name] for name in CORE_METHODS if name in aggregate_methods]
    consensus = vector_mean(core_aggregate)
    core_disagreement = max(
        angular_distance_deg(first, second)
        for index, first in enumerate(core_aggregate) for second in core_aggregate[index + 1 :]
    )
    all_aggregate = list(aggregate_methods.values())
    all_disagreement = max(
        angular_distance_deg(first, second)
        for index, first in enumerate(all_aggregate) for second in all_aggregate[index + 1 :]
    )
    return {
        "consensus_gravity_error_deg": angular_distance_deg(consensus, up),
        "frame_gravity_spread_p95_deg": float(np.percentile([angular_distance_deg(value, consensus) for value in core_frame_vectors], 95)),
        "core_solver_disagreement_deg": core_disagreement,
        "all_solver_disagreement_including_epnp_deg": all_disagreement,
        "method_aggregate_error_deg": {name: angular_distance_deg(value, up) for name, value in aggregate_methods.items()},
        "method_frame_error_deg": {name: distribution(values) for name, values in method_errors.items()},
        "method_reprojection_rms_px": {name: distribution(values) for name, values in reprojection.items()},
        "ippe_branch_identifiable_fraction": float(np.mean(branch_identifiable)) if branch_identifiable else 0.0,
        "ippe_lowest_rms_branch_correct_fraction": float(np.mean(branch_selected_correct)) if branch_selected_correct else 0.0,
        "ippe_branch_gravity_separation_deg": distribution(branch_gravity_separation),
        "obliquity_proxy": float(np.median([row["obliquity_proxy"] for row in frame_rows])),
        "minimum_outer_edge_px": float(np.min([row["minimum_outer_edge_px"] for row in frame_rows])),
        "minimum_margin_px": float(np.min([row["minimum_margin_px"] for row in frame_rows])),
        "board_center_x_px": float(np.median([row["board_center_x_px"] for row in frame_rows])),
    }


def _unconstrained_height_slope(frame_groups: list[list[base.DetectedFrame]], K: np.ndarray, D: np.ndarray) -> dict[str, Any]:
    solved_groups = []
    all_R_wc = []
    for frames in frame_groups:
        rows = []
        for frame in frames:
            item = selected_solution(solve_frame(frame, K, D)["ITERATIVE"])
            if item is None:
                raise RuntimeError("unconstrained PnP failed")
            rows.append(item)
            all_R_wc.append(base.R_WORLD_FROM_BOARD_FRONTAL @ item["R_cb"].T)
        solved_groups.append(rows)
    aggregate_R_wc = base.chordal_mean(all_R_wc)
    residuals = []
    for rows in solved_groups:
        values = []
        for item in rows:
            points = (item["R_cb"] @ base.BOTTOM_POINTS.T).T + item["tvec_m"]
            world_z = (aggregate_R_wc @ points.T).T[:, 2] * 1000.0 + 972.0
            values.append(float(np.mean(world_z) - 912.0))
        residuals.append(float(np.mean(values)))
    return {"bottom_residuals_mm": residuals, "slope_mm_per_m": float(np.polyfit([0.6, 0.8, 1.0], residuals, 1)[0])}


def synthetic_end_to_end(trials: int = 16) -> dict[str, Any]:
    K, D, _ = base.nominal_camera_model()
    K_stress = K.copy()
    K_stress[0, 0] *= 1.0175
    board, _ = base.board_and_detector()
    objects = np.asarray(board.getChessboardCorners(), dtype=np.float64).reshape(-1, 3)
    existing, _ = base.collect_existing()
    visibility = {pose: [frame.ids for frame in existing[pose]] for pose in base.POSE_IDS}
    up_true = normalize(np.asarray([0.006, -0.9998, 0.017]))
    trial_rows = []
    for trial in range(trials):
        gravity_vectors = []
        for frame_index in range(11):
            ids = visibility["W02"][frame_index]
            frame, _, _ = synthetic_frame(
                objects[ids], up_true, 30.0, 0.8, 0.0, 0.15, 50_000 + trial * 100 + frame_index,
                K_stress, D, K, D, f"W04G_{frame_index}",
            )
            solved = solve_frame(frame, K, D)
            candidates = [selected_solution(solved[name]) for name in CORE_METHODS]
            gravity_vectors.append(vector_mean([item["gravity"] for item in candidates if item is not None]))
        up_recovered = vector_mean(gravity_vectors)

        groups: list[list[base.DetectedFrame]] = []
        for pose_index, (pose, distance, yaw) in enumerate(zip(base.POSE_IDS, [0.6, 0.8, 1.0], [3.0, 0.6, 0.0])):
            frames = []
            for frame_index in range(11):
                ids = visibility[pose][frame_index]
                frame, _, _ = synthetic_frame(
                    objects[ids], up_true, yaw, distance, 0.0, 0.15,
                    80_000 + trial * 1000 + pose_index * 100 + frame_index,
                    K_stress, D, K, D, f"{pose}_{frame_index}",
                )
                frames.append(frame)
            groups.append(frames)
        unconstrained = _unconstrained_height_slope(groups, K, D)
        fixed_residuals = []
        fixed_top_residuals = []
        fixed_reprojection = []
        for frames in groups:
            bottom = []
            top = []
            for frame in frames:
                fit = gravity_fixed_fit(frame.object_points, frame.image_points, K, D, up_recovered)
                validation = validate_height(fit, up_recovered)
                bottom.append(validation["bottom_signed_residual_mm"])
                top.append(validation["top_signed_residual_mm"])
                fixed_reprojection.append(fit["reprojection_rms_px"])
            fixed_residuals.append(float(np.mean(bottom)))
            fixed_top_residuals.append(float(np.mean(top)))
        fixed_slope = float(np.polyfit([0.6, 0.8, 1.0], fixed_residuals, 1)[0])
        trial_rows.append(
            {
                "gravity_error_deg": angular_distance_deg(up_recovered, up_true),
                "unconstrained_slope_mm_per_m": unconstrained["slope_mm_per_m"],
                "unconstrained_bottom_residuals_mm": unconstrained["bottom_residuals_mm"],
                "gravity_fixed_slope_mm_per_m": fixed_slope,
                "gravity_fixed_bottom_residuals_mm": fixed_residuals,
                "gravity_fixed_top_residuals_mm": fixed_top_residuals,
                "gravity_fixed_reprojection_rms_px": float(np.sqrt(np.mean(np.asarray(fixed_reprojection) ** 2))),
            }
        )
    source = inspect.getsource(gravity_fixed_fit)
    return {
        "schema_version": "stage3a_gravity_fixed_end_to_end.v1",
        "failure_stress": "effective horizontal focal mapping +1.75%; used only to reproduce a 30/40/50-like near-frontal decomposition failure, not asserted as the real Stage2 error",
        "trial_count": trials,
        "gravity_error_deg": distribution([row["gravity_error_deg"] for row in trial_rows]),
        "unconstrained_height_slope_mm_per_m": distribution([abs(row["unconstrained_slope_mm_per_m"]) for row in trial_rows]),
        "gravity_fixed_height_slope_mm_per_m": distribution([abs(row["gravity_fixed_slope_mm_per_m"]) for row in trial_rows]),
        "gravity_fixed_reprojection_rms_px": distribution([row["gravity_fixed_reprojection_rms_px"] for row in trial_rows]),
        "camera_height_sensitivity": {
            "values_mm": [962, 972, 982],
            "effect": "adds the same constant to every reconstructed top/bottom height; slope unchanged",
        },
        "fit_layer": {
            "free_parameters_per_frame": ["yaw_nuisance", "translation_x", "translation_y", "translation_z"],
            "fixed": ["Stage2 K/D", "u_up_camera", "board metric geometry"],
            "uses_board_bottom_or_top_world_height": False,
            "source_contains_height_constants": any(token in source for token in ("912", "1037", "CAMERA_HEIGHT", "BOARD_BOTTOM")),
        },
        "validation_layer": {
            "uses_camera_height_mm": 972,
            "compares_bottom_mm": 912,
            "compares_top_mm": 1037,
        },
        "pass_criteria": {
            "unconstrained_p95_slope_at_least_mm_per_m": 40.0,
            "gravity_fixed_p95_slope_at_most_mm_per_m": 10.0,
        },
        "pass": (
            float(np.percentile([abs(row["unconstrained_slope_mm_per_m"]) for row in trial_rows], 95)) >= 40.0
            and float(np.percentile([abs(row["gravity_fixed_slope_mm_per_m"]) for row in trial_rows], 95)) <= 10.0
            and not any(token in source for token in ("912", "1037", "CAMERA_HEIGHT", "BOARD_BOTTOM"))
        ),
        "trials": trial_rows,
    }


def conditioning_study(trials_per_condition: int = 4) -> dict[str, Any]:
    K, D, _ = base.nominal_camera_model()
    board, _ = base.board_and_detector()
    objects = np.asarray(board.getChessboardCorners(), dtype=np.float64).reshape(-1, 3)
    existing, _ = base.collect_existing()
    visibility_by_distance = {
        0.55: [frame.ids for frame in existing["W01"]],
        0.8: [frame.ids for frame in existing["W02"]],
        1.0: [frame.ids for frame in existing["W03"]],
    }
    up_vectors = [
        normalize(np.asarray([0.006, -0.9998, 0.017])),
        normalize(np.asarray([-0.035, -0.9988, 0.034])),
        normalize(np.asarray([0.035, -0.9988, -0.034])),
    ]
    yaw_grid = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50]
    noises = [0.05, 0.15, 0.30]
    rows_by_yaw: dict[int, list[dict[str, Any]]] = {yaw: [] for yaw in yaw_grid}
    for yaw in yaw_grid:
        for distance in (0.55, 0.8, 1.0):
            for noise in noises:
                for up_index, up in enumerate(up_vectors):
                    for trial in range(trials_per_condition):
                        row = synthetic_scan_metrics(
                            up, float(yaw), distance, noise, trial + up_index * 10,
                            visibility_by_distance[distance], objects, K, D, K, D,
                        )
                        row.update({"distance_m": distance, "noise_px": noise, "gravity_case": up_index, "trial": trial})
                        rows_by_yaw[yaw].append(row)

    lopo_by_yaw: dict[int, list[float]] = {yaw: [] for yaw in yaw_grid}
    for yaw in yaw_grid:
        for distance in (0.55, 0.8, 1.0):
            up = up_vectors[0]
            for model_index, (_, model_K, model_D) in enumerate(base.lopo_camera_models()):
                row = synthetic_scan_metrics(
                    up, float(yaw), distance, 0.15, model_index,
                    visibility_by_distance[distance], objects, K, D, model_K, model_D,
                )
                lopo_by_yaw[yaw].append(row["consensus_gravity_error_deg"])

    per_yaw = {}
    accepted = []
    for yaw in yaw_grid:
        rows = rows_by_yaw[yaw]
        summary = {
            "consensus_gravity_error_deg": distribution([row["consensus_gravity_error_deg"] for row in rows]),
            "frame_gravity_spread_p95_deg": distribution([row["frame_gravity_spread_p95_deg"] for row in rows]),
            "core_solver_disagreement_deg": distribution([row["core_solver_disagreement_deg"] for row in rows]),
            "all_solver_disagreement_including_epnp_deg": distribution([row["all_solver_disagreement_including_epnp_deg"] for row in rows]),
            "ippe_branch_identifiable_fraction": float(np.mean([row["ippe_branch_identifiable_fraction"] for row in rows])),
            "ippe_lowest_rms_branch_correct_fraction": float(np.mean([row["ippe_lowest_rms_branch_correct_fraction"] for row in rows])),
            "ippe_branch_gravity_separation_deg": distribution([row["ippe_branch_gravity_separation_deg"]["median"] for row in rows]),
            "obliquity_proxy": distribution([row["obliquity_proxy"] for row in rows]),
            "minimum_outer_edge_px": distribution([row["minimum_outer_edge_px"] for row in rows]),
            "minimum_margin_px": distribution([row["minimum_margin_px"] for row in rows]),
            "lopo_consensus_gravity_error_deg": distribution(lopo_by_yaw[yaw]),
            "consensus_gravity_error_by_noise_px": {
                str(noise): distribution([
                    row["consensus_gravity_error_deg"] for row in rows if row["noise_px"] == noise
                ])
                for noise in noises
            },
            "frame_gravity_spread_by_noise_px": {
                str(noise): distribution([
                    row["frame_gravity_spread_p95_deg"] for row in rows if row["noise_px"] == noise
                ])
                for noise in noises
            },
            "method_aggregate_gravity_error_deg": {
                method: distribution([row["method_aggregate_error_deg"][method] for row in rows])
                for method in METHOD_FLAGS
            },
            "method_reprojection_rms_px": {
                method: distribution([row["method_reprojection_rms_px"][method]["median"] for row in rows])
                for method in METHOD_FLAGS
            },
            "scan_condition_count": len(rows),
        }
        passed = (
            summary["consensus_gravity_error_deg"]["p95"] <= 0.5
            and summary["frame_gravity_spread_p95_deg"]["p95"] <= 1.5
            and summary["core_solver_disagreement_deg"]["p95"] <= 0.5
            and summary["ippe_branch_identifiable_fraction"] >= 0.95
            and summary["ippe_lowest_rms_branch_correct_fraction"] >= 0.99
            and summary["minimum_outer_edge_px"]["p05"] >= MIN_OUTER_EDGE_PX
            and summary["minimum_margin_px"]["p05"] >= MIN_MARGIN_PX
            and summary["lopo_consensus_gravity_error_deg"]["p95"] <= 0.75
        )
        summary["accepted_for_good_region"] = passed
        if passed:
            accepted.append(yaw)
        per_yaw[str(yaw)] = summary

    groups = []
    current = []
    for yaw in yaw_grid:
        if yaw in accepted:
            current.append(yaw)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    widest = max(groups, key=len) if groups else []
    good_wide = len(widest) >= 5 and widest[-1] - widest[0] >= 20
    viable = bool(good_wide and per_yaw["0"]["consensus_gravity_error_deg"]["p95"] > 2.0 * max(
        per_yaw[str(yaw)]["consensus_gravity_error_deg"]["p95"] for yaw in widest
    ))
    end_to_end = synthetic_end_to_end()
    viable = bool(viable and end_to_end["pass"])
    return {
        "schema_version": "stage3a_oblique_gravity_conditioning_study.v1",
        "gravity_oblique_route_viable": viable,
        "good_region_is_wide": good_wide,
        "expected_user_difficulty": "LOW" if viable and good_wide else "HIGH",
        "w04g_recommended": viable,
        "new_capture_count_required": 1 if viable else 0,
        "user_measures_angle": False,
        "user_measures_left_right_distance": False,
        "internal_yaw_grid_deg": yaw_grid,
        "distance_m": [0.55, 0.8, 1.0],
        "corner_noise_px": noises,
        "current_visibility_patterns_used": True,
        "trials_per_condition": trials_per_condition,
        "per_internal_yaw": per_yaw,
        "accepted_internal_yaw_plateau_deg": widest,
        "image_proxy_good_region": {"minimum": GOOD_MIN_OBLIQUITY_PROXY, "maximum": GOOD_MAX_OBLIQUITY_PROXY},
        "solver_policy": {
            "authority_consensus": list(CORE_METHODS),
            "epnp": "diagnostic_only because it is materially less stable in the tested oblique region",
            "ippe_branch_selection_without_truth": "positive depth, then lowest reprojection RMS",
        },
        "gravity_yaw_invariance": gravity_yaw_invariance_numeric(),
        "gravity_fixed_fit_end_to_end": end_to_end,
        "formal_runtime_modified": False,
        "promotion": False,
        "capture_or_registration_performed": False,
    }


def collect_scan(scan: Path) -> tuple[list[base.DetectedFrame], int]:
    return base.detect_scan(scan)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only oblique gravity preview scorer")
    sub = parser.add_subparsers(dest="command", required=True)
    item = sub.add_parser("score-scan", help="score an existing scan directory read-only")
    item.add_argument("--scan", type=Path, required=True)
    item.add_argument("--json", action="store_true")
    item = sub.add_parser("score-frame", help="score one exported still image read-only")
    item.add_argument("--image", type=Path, required=True)
    item.add_argument("--json", action="store_true")
    item = sub.add_parser("score-existing", help="confirm frozen frontal scans are not W04G geometry")
    item.add_argument("--json", action="store_true")
    item.add_argument("--output", type=Path)
    item = sub.add_parser("study", help="run synthetic conditioning and gravity-fixed proof")
    item.add_argument("--trials", type=int, default=4)
    item.add_argument("--output", type=Path)
    item.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    K, D, _ = base.nominal_camera_model()
    if args.command == "score-frame":
        board, detector = base.board_and_detector()
        frame = base.detect_frame(args.image.resolve(), board, detector)
        result = aggregate_analysis([] if frame is None else [frame], K, D, 1, include_lopo=True)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print_human(result)
        return 0
    if args.command == "score-scan":
        frames, total = collect_scan(args.scan.resolve())
        result = aggregate_analysis(frames, K, D, total, include_lopo=True)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print_human(result)
        return 0
    if args.command == "score-existing":
        detected, _ = base.collect_existing()
        result = {pose: aggregate_analysis(frames, K, D, len(frames), include_lopo=False) for pose, frames in detected.items()}
        if args.output:
            base.write_json(args.output.resolve(), result)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for index, pose in enumerate(base.POSE_IDS):
                if index:
                    print()
                print_human(result[pose])
                print(f"pose = {pose}")
        return 0
    result = conditioning_study(args.trials)
    if args.output:
        base.write_json(args.output.resolve(), result)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("decision = STUDY_COMPLETE")
        print(f"route_viable = {str(result['gravity_oblique_route_viable']).lower()}")
        print(f"good_region_is_wide = {str(result['good_region_is_wide']).lower()}")
        print(f"expected_user_difficulty = {result['expected_user_difficulty']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
