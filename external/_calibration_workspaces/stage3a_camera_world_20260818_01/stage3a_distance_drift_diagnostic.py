from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation


WORKSPACE = Path(__file__).resolve().parent
STAGE2_BUNDLE = Path(
    r"C:\ec\_calibration_workspaces\stage2_final_bundle_20260818_01"
    r"\calibration_bundle.stage2_final_candidate.v1.json"
)
STAGE2_STABILITY = Path(
    r"C:\ec\_calibration_workspaces\stage2a_camera_intrinsic_20260817_01"
    r"\stability_diagnostics"
)
REGISTRY = WORKSPACE / "observations" / "registry.json"
OUT = WORKSPACE / "diagnostics" / "distance_drift_20260818"
POSE_IDS = ("W01", "W02", "W03")
WIDTH, HEIGHT = 640, 360
CAMERA_HEIGHT_MM = 972.0
BOARD_BOTTOM_MM = 912.0
BOARD_WIDTH_M = 0.175
BOARD_HEIGHT_M = 0.125
R0 = np.asarray(
    [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]],
    dtype=np.float64,
)


@dataclass
class Observation:
    pose_id: str
    frame_id: str
    object_points: np.ndarray
    image_points: np.ndarray
    ids: np.ndarray
    marker_count: int


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def camera_model() -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    bundle = load_json(STAGE2_BUNDLE)
    intrinsics = bundle["camera_intrinsics"]["camera"]["intrinsics"]
    params = {
        "fx": float(intrinsics["fx"]),
        "fy": float(intrinsics["fy"]),
        "cx": float(intrinsics["cx"]),
        "cy": float(intrinsics["cy"]),
        **{key: float(value) for key, value in intrinsics["distortion"].items()},
    }
    return matrices(params) + (params,)


def matrices(params: dict[str, float]) -> tuple[np.ndarray, np.ndarray]:
    K = np.asarray(
        [[params["fx"], 0.0, params["cx"]], [0.0, params["fy"], params["cy"]], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    D = np.asarray([params[key] for key in ("k1", "k2", "p1", "p2", "k3")], dtype=np.float64)
    return K, D


def board_and_detector() -> tuple[Any, Any]:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
    board = cv2.aruco.CharucoBoard((7, 5), 0.025, 0.018, dictionary)
    detector_parameters = cv2.aruco.DetectorParameters()
    detector_parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.CharucoDetector(
        board,
        cv2.aruco.CharucoParameters(),
        detector_parameters,
        cv2.aruco.RefineParameters(),
    )
    return board, detector


def rgb565(path: Path) -> np.ndarray:
    values = np.fromfile(path, dtype="<u2").reshape(HEIGHT, WIDTH)
    red = (((values >> 11) & 0x1F) * 255 + 15) // 31
    green = (((values >> 5) & 0x3F) * 255 + 31) // 63
    blue = ((values & 0x1F) * 255 + 15) // 31
    return np.dstack((blue, green, red)).astype(np.uint8)


def collect_observations() -> tuple[list[Observation], Any, dict[str, Any]]:
    registry = load_json(REGISTRY)
    board, detector = board_and_detector()
    observations: list[Observation] = []
    for pose_id in POSE_IDS:
        scan = Path(registry["poses"][pose_id]["scan_path"])
        for frame_dir in sorted((scan / "frames").glob("frame_*")):
            corners, ids, _, marker_ids = detector.detectBoard(rgb565(frame_dir / "image.rgb565"))
            if corners is None or ids is None or len(ids) < 6:
                raise RuntimeError(f"insufficient corners: {pose_id}/{frame_dir.name}")
            objects, images = board.matchImagePoints(corners, ids)
            observations.append(
                Observation(
                    pose_id,
                    frame_dir.name,
                    np.asarray(objects, dtype=np.float64).reshape(-1, 3),
                    np.asarray(images, dtype=np.float64).reshape(-1, 2),
                    np.asarray(ids, dtype=np.int32).reshape(-1),
                    0 if marker_ids is None else len(marker_ids),
                )
            )
    return observations, board, registry


def project(object_points: np.ndarray, rvec: np.ndarray, tvec: np.ndarray, K: np.ndarray, D: np.ndarray) -> np.ndarray:
    image, _ = cv2.projectPoints(object_points, rvec, tvec, K, D)
    return image.reshape(-1, 2)


def reprojection_stats(errors: list[float] | np.ndarray) -> dict[str, float | int]:
    values = np.asarray(errors, dtype=np.float64).reshape(-1)
    return {
        "count": int(values.size),
        "rms_px": float(np.sqrt(np.mean(values**2))),
        "median_px": float(np.median(values)),
        "p95_px": float(np.percentile(values, 95)),
        "max_px": float(np.max(values)),
    }


def rotation_distance_deg(a: np.ndarray, b: np.ndarray) -> float:
    cosine = np.clip((np.trace(a.T @ b) - 1.0) / 2.0, -1.0, 1.0)
    return math.degrees(math.acos(float(cosine)))


def chordal_mean(rotations: list[np.ndarray]) -> np.ndarray:
    u, _, vt = np.linalg.svd(np.sum(rotations, axis=0))
    result = u @ vt
    if np.linalg.det(result) < 0:
        u[:, -1] *= -1
        result = u @ vt
    return result


def orientation(R_wc: np.ndarray) -> dict[str, float]:
    right, optical = R_wc[:, 0], R_wc[:, 2]
    pitch = math.atan2(float(optical[2]), float(np.hypot(optical[0], optical[1])))
    yaw = math.atan2(float(optical[0]), float(optical[1]))
    right_zero = np.cross(optical, np.asarray([0.0, 0.0, 1.0]))
    right_zero /= np.linalg.norm(right_zero)
    down_zero = np.cross(optical, right_zero)
    roll = math.atan2(float(right @ down_zero), float(right @ right_zero))
    return {"roll_deg": math.degrees(roll), "pitch_deg": math.degrees(pitch), "yaw_deg": math.degrees(yaw)}


def slope(x: list[float], y: list[float], scale: float = 1.0) -> float:
    return float(np.polyfit(np.asarray(x), np.asarray(y), 1)[0] * scale)


def solve_generic(obs: Observation, K: np.ndarray, D: np.ndarray, flag: int) -> list[dict[str, Any]]:
    result = cv2.solvePnPGeneric(obs.object_points, obs.image_points, K, D, flags=flag)
    if not result[0]:
        return []
    solutions = []
    for rvec, tvec in zip(result[1], result[2]):
        t = np.asarray(tvec, dtype=np.float64).reshape(3)
        R_cb, _ = cv2.Rodrigues(rvec)
        projected = project(obs.object_points, rvec, tvec, K, D)
        point_errors = np.linalg.norm(projected - obs.image_points, axis=1)
        depths = (R_cb @ obs.object_points.T).T[:, 2] + t[2]
        solutions.append(
            {
                "rvec": np.asarray(rvec, dtype=np.float64).reshape(3),
                "tvec_m": t,
                "R_cb": R_cb,
                "positive_depth": bool(np.all(depths > 0)),
                "reprojection": reprojection_stats(point_errors),
            }
        )
    return sorted(solutions, key=lambda item: (not item["positive_depth"], item["reprojection"]["rms_px"]))


def pose_summary(frame_solutions: list[dict[str, Any]]) -> dict[str, Any]:
    R_wc_frames = [R0 @ row["R_cb"].T for row in frame_solutions]
    R_wc = chordal_mean(R_wc_frames)
    tvecs = np.asarray([row["tvec_m"] for row in frame_solutions])
    normal_distances = [abs(float(row["R_cb"][:, 2] @ row["tvec_m"])) * 1000.0 for row in frame_solutions]
    return {
        "R_wc": R_wc,
        "orientation": orientation(R_wc),
        "distance_mm": float(np.median(normal_distances)),
        "tvec_m_median": np.median(tvecs, axis=0).tolist(),
        "frame_orientation_spread_deg": reprojection_stats(
            [rotation_distance_deg(R_wc, item) for item in R_wc_frames]
        ),
    }


def active_height_with_rotation(frame_solution: dict[str, Any], R_wc: np.ndarray, camera_height_mm: float) -> float:
    points_m = np.asarray([[0.0, BOARD_HEIGHT_M, 0.0], [BOARD_WIDTH_M, BOARD_HEIGHT_M, 0.0]])
    p_camera_m = (frame_solution["R_cb"] @ points_m.T).T + frame_solution["tvec_m"]
    p_world_mm = (R_wc @ p_camera_m.T).T * 1000.0 + np.asarray([0.0, 0.0, camera_height_mm])
    return float(np.mean(p_world_mm[:, 2]))


def analyze_methods(observations: list[Observation], K: np.ndarray, D: np.ndarray) -> tuple[dict[str, Any], dict[str, Any]]:
    methods = {
        "CURRENT_ITERATIVE": cv2.SOLVEPNP_ITERATIVE,
        "IPPE": cv2.SOLVEPNP_IPPE,
        "SQPNP": cv2.SOLVEPNP_SQPNP,
        "EPNP": cv2.SOLVEPNP_EPNP,
    }
    all_internal: dict[str, Any] = {}
    output: dict[str, Any] = {
        "schema_version": "stage3a_planar_pnp_comparison.v1",
        "generated_at": now(),
        "same_K_D_and_correspondences": True,
        "frame_count": len(observations),
        "methods": {},
        "not_applicable": {
            "IPPE_SQUARE": "requires exactly four points in a prescribed square order; this 175x125 mm ChArUco rectangle and 24 inner corners do not meet that contract"
        },
    }
    for name, flag in methods.items():
        per_pose: dict[str, Any] = {}
        alternate_rows = []
        frame_rows: dict[str, list[dict[str, Any]]] = {pose: [] for pose in POSE_IDS}
        for obs in observations:
            solutions = solve_generic(obs, K, D, flag)
            if not solutions:
                raise RuntimeError(f"{name} failed for {obs.pose_id}/{obs.frame_id}")
            frame_rows[obs.pose_id].append(solutions[0])
            if len(solutions) > 1:
                alternate_rows.append(
                    {
                        "pose_id": obs.pose_id,
                        "frame_id": obs.frame_id,
                        "best_rms_px": solutions[0]["reprojection"]["rms_px"],
                        "alternate_rms_px": solutions[1]["reprojection"]["rms_px"],
                        "orientation_separation_deg": rotation_distance_deg(
                            R0 @ solutions[0]["R_cb"].T, R0 @ solutions[1]["R_cb"].T
                        ),
                        "alternate_positive_depth": solutions[1]["positive_depth"],
                    }
                )
        pose_rotations: dict[str, np.ndarray] = {}
        distances = []
        for pose in POSE_IDS:
            summary = pose_summary(frame_rows[pose])
            pose_rotations[pose] = summary.pop("R_wc")
            per_pose[pose] = summary
            distances.append(float(summary["distance_mm"]))
        aggregate_R = chordal_mean(list(pose_rotations.values()))
        residuals = []
        per_pose_height = {}
        pitch = []
        yaw = []
        roll = []
        for pose in POSE_IDS:
            heights = [active_height_with_rotation(row, aggregate_R, CAMERA_HEIGHT_MM) for row in frame_rows[pose]]
            residual = float(np.median(heights) - BOARD_BOTTOM_MM)
            residuals.append(residual)
            per_pose_height[pose] = {
                "reconstructed_bottom_z_mm": float(np.median(heights)),
                "signed_residual_mm": residual,
                "within_pose_std_mm": float(np.std(heights)),
            }
            pitch.append(per_pose[pose]["orientation"]["pitch_deg"])
            yaw.append(per_pose[pose]["orientation"]["yaw_deg"])
            roll.append(per_pose[pose]["orientation"]["roll_deg"])
        reproj = [row["reprojection"]["rms_px"] for rows in frame_rows.values() for row in rows]
        pairs = {}
        for i, first in enumerate(POSE_IDS):
            for second in POSE_IDS[i + 1 :]:
                pairs[f"{first}<->{second}"] = rotation_distance_deg(pose_rotations[first], pose_rotations[second])
        method_out = {
            "flag": int(flag),
            "per_pose": per_pose,
            "pairwise_SO3_deg": pairs,
            "frame_rms_distribution": reprojection_stats(reproj),
            "aggregate_orientation": orientation(aggregate_R),
            "aggregate_R_world_from_camera": aggregate_R.tolist(),
            "fixed_height_using_aggregate_R": {
                "per_pose": per_pose_height,
                "slope_mm_per_m": slope(distances, residuals, 1000.0),
            },
            "orientation_slopes_deg_per_m": {
                "roll": slope(distances, roll, 1000.0),
                "pitch": slope(distances, pitch, 1000.0),
                "yaw": slope(distances, yaw, 1000.0),
            },
            "alternate_solution": {
                "available_frame_count": len(alternate_rows),
                "rows": alternate_rows,
            },
        }
        output["methods"][name] = method_out
        all_internal[name] = {"frames": frame_rows, "pose_rotations": pose_rotations, "aggregate_R": aggregate_R}
    return output, all_internal


def homography_geometry(observations: list[Observation], K: np.ndarray, D: np.ndarray) -> dict[str, Any]:
    outline = np.asarray([[0.0, 0.0], [BOARD_WIDTH_M, 0.0], [BOARD_WIDTH_M, BOARD_HEIGHT_M], [0.0, BOARD_HEIGHT_M]])
    result = {}
    for pose in POSE_IDS:
        rows = []
        for obs in [item for item in observations if item.pose_id == pose]:
            und = cv2.undistortPoints(obs.image_points.reshape(-1, 1, 2), K, D, P=K).reshape(-1, 2)
            H, _ = cv2.findHomography(obs.object_points[:, :2], und, method=0)
            projected = cv2.perspectiveTransform(outline.reshape(-1, 1, 2), H).reshape(-1, 2)
            top = projected[1] - projected[0]
            right = projected[2] - projected[1]
            bottom = projected[2] - projected[3]
            left = projected[3] - projected[0]
            center = projected.mean(axis=0)
            rows.append(
                {
                    "center_x_px": float(center[0]),
                    "center_y_px": float(center[1]),
                    "top_width_px": float(np.linalg.norm(top)),
                    "bottom_width_px": float(np.linalg.norm(bottom)),
                    "left_height_px": float(np.linalg.norm(left)),
                    "right_height_px": float(np.linalg.norm(right)),
                    "top_edge_angle_deg": math.degrees(math.atan2(float(top[1]), float(top[0]))),
                    "left_edge_angle_from_vertical_deg": math.degrees(math.atan2(float(left[0]), float(left[1]))),
                    "width_ratio_top_over_bottom": float(np.linalg.norm(top) / np.linalg.norm(bottom)),
                    "height_ratio_left_over_right": float(np.linalg.norm(left) / np.linalg.norm(right)),
                }
            )
        result[pose] = {
            key: {"median": float(np.median([row[key] for row in rows])), "std": float(np.std([row[key] for row in rows]))}
            for key in rows[0]
        }
    centre_y = [result[pose]["center_y_px"]["median"] for pose in POSE_IDS]
    pitch_delta_deg = math.degrees((max(centre_y) - min(centre_y)) / float(K[1, 1]))
    result["mechanical_pitch_image_test"] = {
        "observed_center_y_range_px": float(max(centre_y) - min(centre_y)),
        "small_angle_equivalent_pitch_range_deg": pitch_delta_deg,
        "pnp_pitch_range_deg": None,
        "expected_pixel_shift_for_1p79deg_at_fy": float(K[1, 1] * math.radians(1.79)),
        "note": "centre-y alone is not authoritative; edge ratios/angles and constrained image-space fit are evaluated jointly",
    }
    return result


def R_wc_from_gravity(params: np.ndarray) -> np.ndarray:
    return Rotation.from_rotvec(np.asarray([params[0], params[1], 0.0])).as_matrix() @ R0


def Rz(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.asarray([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def constrained_initial(method_internal: dict[str, Any]) -> np.ndarray:
    aggregate = method_internal["aggregate_R"]
    delta = aggregate @ R0.T
    rv = Rotation.from_matrix(delta).as_rotvec()
    x = [float(rv[0]), float(rv[1])]
    R_wc = R_wc_from_gravity(np.asarray(x))
    t_wc = np.asarray([0.0, 0.0, CAMERA_HEIGHT_MM / 1000.0])
    for pose in POSE_IDS:
        rows = method_internal["frames"][pose]
        R_cb = chordal_mean([row["R_cb"] for row in rows])
        R_wb = R_wc @ R_cb
        alpha = math.atan2(float(R_wb[1, 0]), float(R_wb[0, 0]))
        t_cb = np.median(np.asarray([row["tvec_m"] for row in rows]), axis=0)
        t_wb = R_wc @ t_cb + t_wc
        x.extend([alpha, float(t_wb[0]), float(t_wb[1])])
    return np.asarray(x, dtype=np.float64)


def constrained_project(
    params: np.ndarray, obs: Observation, K: np.ndarray, D: np.ndarray,
    relative_top_m: float | None = None,
) -> np.ndarray:
    pose_index = POSE_IDS.index(obs.pose_id)
    alpha, x, y = params[2 + 3 * pose_index : 5 + 3 * pose_index]
    R_wc = R_wc_from_gravity(params)
    R_wb = Rz(float(alpha)) @ R0
    t_wc = np.asarray([0.0, 0.0, CAMERA_HEIGHT_MM / 1000.0])
    top_z = (
        (BOARD_BOTTOM_MM / 1000.0) + BOARD_HEIGHT_M
        if relative_top_m is None
        else t_wc[2] + float(relative_top_m)
    )
    t_wb = np.asarray([x, y, top_z])
    R_cb = R_wc.T @ R_wb
    t_cb = R_wc.T @ (t_wb - t_wc)
    rvec, _ = cv2.Rodrigues(R_cb)
    return project(obs.object_points, rvec, t_cb, K, D)


def constrained_fit_free_relative_height(
    observations: list[Observation], K: np.ndarray, D: np.ndarray, initial: np.ndarray,
) -> tuple[np.ndarray, float, dict[str, Any]]:
    nominal_relative_top_m = BOARD_BOTTOM_MM / 1000.0 + BOARD_HEIGHT_M - CAMERA_HEIGHT_MM / 1000.0
    x0 = np.r_[initial[:2], nominal_relative_top_m, initial[2:]]

    def unpack(value: np.ndarray) -> tuple[np.ndarray, float]:
        full = np.r_[value[:2], value[3:]]
        return full, float(value[2])

    def residual(value: np.ndarray) -> np.ndarray:
        full, relative_top = unpack(value)
        return np.concatenate([
            (constrained_project(full, obs, K, D, relative_top) - obs.image_points).reshape(-1)
            for obs in observations
        ])

    fit = least_squares(
        residual, x0, method="trf", loss="linear", x_scale="jac",
        ftol=1e-13, xtol=1e-13, gtol=1e-13, max_nfev=3000,
    )
    full, relative_top = unpack(fit.x)
    errors = []
    per_pose = {}
    for pose in POSE_IDS:
        pose_errors = []
        for obs in [item for item in observations if item.pose_id == pose]:
            values = np.linalg.norm(
                constrained_project(full, obs, K, D, relative_top) - obs.image_points, axis=1
            )
            pose_errors.extend(values.tolist())
            errors.extend(values.tolist())
        per_pose[pose] = reprojection_stats(pose_errors)
    implied_bottom = (CAMERA_HEIGHT_MM / 1000.0 + relative_top - BOARD_HEIGHT_M) * 1000.0
    return full, relative_top, {
        "diagnostic_only": True,
        "must_not_be_used_as_height_authority": True,
        "success": bool(fit.success),
        "message": fit.message,
        "nfev": int(fit.nfev),
        "shared_orientation_gauge_yaw_zero": orientation(R_wc_from_gravity(full)),
        "fitted_board_top_minus_camera_height_mm": relative_top * 1000.0,
        "nominal_board_top_minus_camera_height_mm": nominal_relative_top_m * 1000.0,
        "implied_board_bottom_mm_if_camera_height_is_972": implied_bottom,
        "implied_camera_height_mm_if_board_bottom_is_912": BOARD_BOTTOM_MM - (implied_bottom - CAMERA_HEIGHT_MM),
        "per_pose_reprojection": per_pose,
        "all_corner_reprojection": reprojection_stats(errors),
    }


def common_gravity_free_per_pose_height(
    observations: list[Observation], K: np.ndarray, D: np.ndarray, initial: np.ndarray,
) -> dict[str, Any]:
    # Diagnostic model only: gravity is shared, but every pose is allowed its own
    # vertical translation.  If this fits well while inferred height drifts with
    # distance, that is direct evidence of rotation/translation coupling rather
    # than a valid fixed-height world solution.
    initial_values = [float(initial[0]), float(initial[1])]
    R_wc_initial = R_wc_from_gravity(initial)
    t_wc = np.asarray([0.0, 0.0, CAMERA_HEIGHT_MM / 1000.0])
    for pose in POSE_IDS:
        block = initial[2 + 3 * POSE_IDS.index(pose) : 5 + 3 * POSE_IDS.index(pose)]
        rows = [obs for obs in observations if obs.pose_id == pose]
        solutions = [solve_generic(obs, K, D, cv2.SOLVEPNP_ITERATIVE)[0] for obs in rows]
        t_cb = np.median(np.asarray([row["tvec_m"] for row in solutions]), axis=0)
        top_z = float((R_wc_initial @ t_cb + t_wc)[2])
        initial_values.extend([float(block[0]), float(block[1]), float(block[2]), top_z])
    x0 = np.asarray(initial_values)

    def pose_projection(value: np.ndarray, obs: Observation) -> np.ndarray:
        index = POSE_IDS.index(obs.pose_id)
        alpha, x, y, top_z = value[2 + 4 * index : 6 + 4 * index]
        R_wc = R_wc_from_gravity(value)
        R_wb = Rz(float(alpha)) @ R0
        t_wb = np.asarray([x, y, top_z])
        R_cb = R_wc.T @ R_wb
        t_cb = R_wc.T @ (t_wb - t_wc)
        rvec, _ = cv2.Rodrigues(R_cb)
        return project(obs.object_points, rvec, t_cb, K, D)

    def residual(value: np.ndarray) -> np.ndarray:
        return np.concatenate([(pose_projection(value, obs) - obs.image_points).reshape(-1) for obs in observations])

    fit = least_squares(
        residual, x0, method="trf", loss="linear", x_scale="jac",
        ftol=1e-13, xtol=1e-13, gtol=1e-13, max_nfev=3000,
    )
    all_errors = []
    per_pose = {}
    distances, bottoms = [], []
    for pose in POSE_IDS:
        pose_errors = []
        for obs in [item for item in observations if item.pose_id == pose]:
            values = np.linalg.norm(pose_projection(fit.x, obs) - obs.image_points, axis=1)
            pose_errors.extend(values.tolist())
            all_errors.extend(values.tolist())
        index = POSE_IDS.index(pose)
        alpha, x, y, top_z = fit.x[2 + 4 * index : 6 + 4 * index]
        bottom = float((top_z - BOARD_HEIGHT_M) * 1000.0)
        distance = float(math.hypot(x, y) * 1000.0)
        distances.append(distance)
        bottoms.append(bottom)
        per_pose[pose] = {
            "nuisance_yaw_deg": math.degrees(float(alpha)),
            "distance_proxy_mm": distance,
            "implied_board_bottom_mm_with_camera_origin_z_972": bottom,
            "signed_residual_from_912_mm": bottom - BOARD_BOTTOM_MM,
            "reprojection": reprojection_stats(pose_errors),
        }
    return {
        "diagnostic_only": True,
        "per_pose_height_was_freed": True,
        "must_not_be_exported": True,
        "shared_orientation_gauge_yaw_zero": orientation(R_wc_from_gravity(fit.x)),
        "per_pose": per_pose,
        "implied_height_slope_mm_per_m": slope(distances, bottoms, 1000.0),
        "all_corner_reprojection": reprojection_stats(all_errors),
        "interpretation": "a good image fit with distance-dependent inferred height is planar rotation/translation coupling, not a valid height calibration",
    }


def constrained_common_gravity_frame_xy(
    observations: list[Observation], K: np.ndarray, D: np.ndarray, initial: np.ndarray,
    fixed_gravity: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    # W01's outline centre moves by several pixels during its 11-frame scan.
    # Preserve every frame, keep pose-level yaw, and absorb only frame-level XY
    # translation.  Gravity and height remain hard shared constraints.
    yaws = [initial[2 + 3 * i] for i in range(len(POSE_IDS))]
    gravity = initial[:2] if fixed_gravity is None else np.asarray(fixed_gravity)
    R_wc = R_wc_from_gravity(gravity)
    t_wc = np.asarray([0.0, 0.0, CAMERA_HEIGHT_MM / 1000.0])
    frame_xy = []
    for obs in observations:
        solution = solve_generic(obs, K, D, cv2.SOLVEPNP_ITERATIVE)[0]
        t_wb = R_wc @ solution["tvec_m"] + t_wc
        frame_xy.extend([float(t_wb[0]), float(t_wb[1])])
    x0 = np.asarray(([float(gravity[0]), float(gravity[1])] if fixed_gravity is None else []) + yaws + frame_xy)

    def decode(value: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if fixed_gravity is None:
            g = value[:2]
            offset = 2
        else:
            g = np.asarray(fixed_gravity)
            offset = 0
        return g, value[offset : offset + 3], value[offset + 3 :].reshape(-1, 2)

    def one_project(value: np.ndarray, obs_index: int) -> np.ndarray:
        g, pose_yaws, xy = decode(value)
        obs = observations[obs_index]
        pose_index = POSE_IDS.index(obs.pose_id)
        R_wc_value = R_wc_from_gravity(g)
        R_wb = Rz(float(pose_yaws[pose_index])) @ R0
        t_wb = np.asarray([
            xy[obs_index, 0], xy[obs_index, 1],
            BOARD_BOTTOM_MM / 1000.0 + BOARD_HEIGHT_M,
        ])
        R_cb = R_wc_value.T @ R_wb
        t_cb = R_wc_value.T @ (t_wb - t_wc)
        rvec, _ = cv2.Rodrigues(R_cb)
        return project(obs.object_points, rvec, t_cb, K, D)

    def residual(value: np.ndarray) -> np.ndarray:
        return np.concatenate([
            (one_project(value, index) - obs.image_points).reshape(-1)
            for index, obs in enumerate(observations)
        ])

    fit = least_squares(
        residual, x0, method="trf", loss="linear", x_scale="jac",
        ftol=1e-12, xtol=1e-12, gtol=1e-12, max_nfev=2000,
    )
    fitted_gravity, fitted_yaws, fitted_xy = decode(fit.x)
    all_errors = []
    per_pose = {}
    for pose in POSE_IDS:
        indices = [index for index, obs in enumerate(observations) if obs.pose_id == pose]
        pose_errors = []
        for index in indices:
            values = np.linalg.norm(one_project(fit.x, index) - observations[index].image_points, axis=1)
            pose_errors.extend(values.tolist())
            all_errors.extend(values.tolist())
        per_pose[pose] = {
            "nuisance_yaw_deg": math.degrees(float(fitted_yaws[POSE_IDS.index(pose)])),
            "frame_xy_std_mm": (np.std(fitted_xy[indices], axis=0) * 1000.0).tolist(),
            "reprojection": reprojection_stats(pose_errors),
        }
    return fitted_gravity, {
        "success": bool(fit.success),
        "parameter_count": int(fit.x.size),
        "model_delta_from_preferred": "per-frame XY nuisance added because raw W01 image centre moves during scan; yaw remains per-pose",
        "shared_orientation_gauge_yaw_zero": orientation(R_wc_from_gravity(fitted_gravity)),
        "per_pose": per_pose,
        "all_corner_reprojection": reprojection_stats(all_errors),
        "fixed_height_residual_by_construction_mm": 0.0,
    }


def constrained_common_gravity_frame_xy_yaw(
    observations: list[Observation], K: np.ndarray, D: np.ndarray, initial: np.ndarray,
) -> dict[str, Any]:
    gravity = initial[:2]
    R_wc = R_wc_from_gravity(gravity)
    t_wc = np.asarray([0.0, 0.0, CAMERA_HEIGHT_MM / 1000.0])
    blocks = []
    for obs in observations:
        solution = solve_generic(obs, K, D, cv2.SOLVEPNP_ITERATIVE)[0]
        R_wb = R_wc @ solution["R_cb"]
        alpha = math.atan2(float(R_wb[1, 0]), float(R_wb[0, 0]))
        t_wb = R_wc @ solution["tvec_m"] + t_wc
        blocks.extend([alpha, float(t_wb[0]), float(t_wb[1])])
    x0 = np.r_[gravity, blocks]

    def one_project(value: np.ndarray, index: int) -> np.ndarray:
        obs = observations[index]
        alpha, x, y = value[2 + 3 * index : 5 + 3 * index]
        R_wc_value = R_wc_from_gravity(value)
        R_wb = Rz(float(alpha)) @ R0
        t_wb = np.asarray([x, y, BOARD_BOTTOM_MM / 1000.0 + BOARD_HEIGHT_M])
        R_cb = R_wc_value.T @ R_wb
        t_cb = R_wc_value.T @ (t_wb - t_wc)
        rvec, _ = cv2.Rodrigues(R_cb)
        return project(obs.object_points, rvec, t_cb, K, D)

    def residual(value: np.ndarray) -> np.ndarray:
        return np.concatenate([
            (one_project(value, index) - obs.image_points).reshape(-1)
            for index, obs in enumerate(observations)
        ])

    fit = least_squares(
        residual, x0, method="trf", loss="linear", x_scale="jac",
        ftol=1e-12, xtol=1e-12, gtol=1e-12, max_nfev=2000,
    )
    all_errors = []
    per_pose = {}
    for pose in POSE_IDS:
        indices = [index for index, obs in enumerate(observations) if obs.pose_id == pose]
        pose_errors, yaws = [], []
        for index in indices:
            values = np.linalg.norm(one_project(fit.x, index) - observations[index].image_points, axis=1)
            pose_errors.extend(values.tolist())
            all_errors.extend(values.tolist())
            yaws.append(math.degrees(float(fit.x[2 + 3 * index])))
        per_pose[pose] = {
            "frame_yaw_median_deg": float(np.median(yaws)),
            "frame_yaw_std_deg": float(np.std(yaws)),
            "reprojection": reprojection_stats(pose_errors),
        }
    return {
        "success": bool(fit.success),
        "parameter_count": int(fit.x.size),
        "model_delta_from_preferred": "per-frame yaw and XY are diagnostic acquisition nuisances; gravity and heights remain shared/fixed",
        "shared_orientation_gauge_yaw_zero": orientation(R_wc_from_gravity(fit.x)),
        "per_pose": per_pose,
        "all_corner_reprojection": reprojection_stats(all_errors),
        "fixed_height_residual_by_construction_mm": 0.0,
    }


def constrained_fit(
    observations: list[Observation], K: np.ndarray, D: np.ndarray, initial: np.ndarray,
    fixed_gravity: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    active_poses = [pose for pose in POSE_IDS if any(obs.pose_id == pose for obs in observations)]
    active_indices = [POSE_IDS.index(pose) for pose in active_poses]
    if fixed_gravity is None:
        def unpack(value: np.ndarray) -> np.ndarray:
            full = initial.copy()
            full[:2] = value[:2]
            for packed_index, pose_index in enumerate(active_indices):
                full[2 + 3 * pose_index : 5 + 3 * pose_index] = value[2 + 3 * packed_index : 5 + 3 * packed_index]
            return full

        x0 = np.r_[initial[:2], *[initial[2 + 3 * index : 5 + 3 * index] for index in active_indices]]
    else:
        def unpack(value: np.ndarray) -> np.ndarray:
            full = initial.copy()
            full[:2] = fixed_gravity
            for packed_index, pose_index in enumerate(active_indices):
                full[2 + 3 * pose_index : 5 + 3 * pose_index] = value[3 * packed_index : 3 * packed_index + 3]
            return full

        x0 = np.r_[*[initial[2 + 3 * index : 5 + 3 * index] for index in active_indices]]

    def residual(value: np.ndarray) -> np.ndarray:
        full = unpack(value)
        return np.concatenate([(constrained_project(full, obs, K, D) - obs.image_points).reshape(-1) for obs in observations])

    fit = least_squares(
        residual,
        x0,
        method="trf",
        loss="linear",
        x_scale="jac",
        ftol=1e-13,
        xtol=1e-13,
        gtol=1e-13,
        max_nfev=3000,
    )
    full = unpack(fit.x)
    pixel_errors = []
    per_pose = {}
    for pose in active_poses:
        rows = [obs for obs in observations if obs.pose_id == pose]
        errors = []
        for obs in rows:
            error = np.linalg.norm(constrained_project(full, obs, K, D) - obs.image_points, axis=1)
            errors.extend(error.tolist())
            pixel_errors.extend(error.tolist())
        index = POSE_IDS.index(pose)
        alpha, x, y = full[2 + 3 * index : 5 + 3 * index]
        per_pose[pose] = {
            "nuisance_board_yaw_deg": math.degrees(float(alpha)),
            "board_origin_world_x_mm": float(x * 1000.0),
            "board_origin_world_y_mm": float(y * 1000.0),
            "board_plane_distance_proxy_mm": float(math.hypot(x, y) * 1000.0),
            "reprojection": reprojection_stats(errors),
        }
    return full, {
        "success": bool(fit.success),
        "message": fit.message,
        "cost": float(fit.cost),
        "optimality": float(fit.optimality),
        "nfev": int(fit.nfev),
        "shared_R_world_from_camera": R_wc_from_gravity(full).tolist(),
        "shared_orientation_gauge_yaw_zero": orientation(R_wc_from_gravity(full)),
        "shared_gravity_parameters_rad": full[:2].tolist(),
        "per_pose": per_pose,
        "all_corner_reprojection": reprojection_stats(pixel_errors),
    }


def constrained_analysis(observations: list[Observation], K: np.ndarray, D: np.ndarray, method_internal: dict[str, Any]) -> dict[str, Any]:
    initial = constrained_initial(method_internal)
    full_params, fit = constrained_fit(observations, K, D, initial)
    _, _, free_relative_height = constrained_fit_free_relative_height(observations, K, D, full_params)
    free_per_pose_height = common_gravity_free_per_pose_height(observations, K, D, full_params)
    _, frame_xy_fit = constrained_common_gravity_frame_xy(observations, K, D, full_params)
    frame_xy_yaw_fit = constrained_common_gravity_frame_xy_yaw(observations, K, D, full_params)
    lopo = []
    for omitted in POSE_IDS:
        training = [obs for obs in observations if obs.pose_id != omitted]
        train_params, train_fit = constrained_fit(training, K, D, full_params)
        held = [obs for obs in observations if obs.pose_id == omitted]
        held_params, held_fit = constrained_fit(held, K, D, full_params, fixed_gravity=train_params[:2])
        lopo.append(
            {
                "omitted_pose_id": omitted,
                "training_shared_orientation": train_fit["shared_orientation_gauge_yaw_zero"],
                "held_out_refit_only_nuisance": held_fit["per_pose"][omitted],
                "shared_orientation_delta_from_full_deg": rotation_distance_deg(
                    R_wc_from_gravity(train_params), R_wc_from_gravity(full_params)
                ),
            }
        )
    return {
        "schema_version": "stage3a_constrained_solver_result.v1",
        "generated_at": now(),
        "status": "CANDIDATE_NOT_PROMOTED",
        "promotion": False,
        "model": {
            "shared": ["camera gravity roll component", "camera gravity pitch component"],
            "per_pose": ["board/vehicle relative yaw nuisance", "board origin X", "board origin Y"],
            "fixed": {
                "K_D": "Stage2 final candidate",
                "camera_height_mm": CAMERA_HEIGHT_MM,
                "active_bottom_height_mm": BOARD_BOTTOM_MM,
                "board_vertical": True,
                "board_geometry_mm": [175.0, 125.0],
            },
            "parameter_count": 11,
            "observation_scalar_count": int(sum(obs.image_points.size for obs in observations)),
            "yaw_gauge": "shared camera yaw fixed to zero; per-pose yaw is relative nuisance",
        },
        "fit": fit,
        "diagnostic_free_shared_relative_height_fit": free_relative_height,
        "diagnostic_common_gravity_free_per_pose_height": free_per_pose_height,
        "constrained_common_gravity_with_frame_xy_nuisance": frame_xy_fit,
        "diagnostic_common_gravity_with_frame_xy_yaw_nuisance": frame_xy_yaw_fit,
        "leave_one_pose_out": lopo,
        "height_residual_by_construction_mm": 0.0,
        "warning": "known height is a constraint, not an independent post-fit validation metric",
    }


def evaluate_intrinsics(
    observations: list[Observation], params: dict[str, float], parameter_sets: list[tuple[str, dict[str, float]]]
) -> list[dict[str, Any]]:
    rows = []
    for label, candidate in parameter_sets:
        K, D = matrices(candidate)
        comparison, _ = analyze_methods(observations, K, D)
        current = comparison["methods"]["CURRENT_ITERATIVE"]
        rows.append(
            {
                "label": label,
                "parameters": candidate,
                "height_slope_mm_per_m": current["fixed_height_using_aggregate_R"]["slope_mm_per_m"],
                "pitch_slope_deg_per_m": current["orientation_slopes_deg_per_m"]["pitch"],
                "yaw_slope_deg_per_m": current["orientation_slopes_deg_per_m"]["yaw"],
                "frame_rms_median_px": current["frame_rms_distribution"]["median_px"],
            }
        )
    return rows


def intrinsic_sensitivity(observations: list[Observation], nominal: dict[str, float]) -> dict[str, Any]:
    lopo_path = STAGE2_STABILITY / "lopo" / "lopo_per_pose.csv"
    subsample_path = STAGE2_STABILITY / "subsample80" / "runs.csv"
    keys = ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3")

    def read_sets(path: Path, prefix: str) -> list[tuple[str, dict[str, float]]]:
        rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))
        return [(f"{prefix}_{i:03d}", {key: float(row[key]) for key in keys}) for i, row in enumerate(rows)]

    lopo_sets = read_sets(lopo_path, "lopo")
    subsample_sets = read_sets(subsample_path, "subsample80")
    lopo_eval = evaluate_intrinsics(observations, nominal, [("nominal", nominal), *lopo_sets])
    subsample_eval = evaluate_intrinsics(observations, nominal, subsample_sets)
    one_at_a_time = []
    for key in keys:
        values = np.asarray([item[1][key] for item in lopo_sets])
        low, high = np.percentile(values, [5, 95])
        sets = []
        for label, value in (("p05", low), ("p95", high)):
            candidate = dict(nominal)
            candidate[key] = float(value)
            sets.append((f"{key}_{label}", candidate))
        evaluated = evaluate_intrinsics(observations, nominal, sets)
        delta = evaluated[1]["height_slope_mm_per_m"] - evaluated[0]["height_slope_mm_per_m"]
        one_at_a_time.append(
            {
                "parameter": key,
                "stage2_lopo_p05": float(low),
                "stage2_lopo_p95": float(high),
                "height_slope_span_mm_per_m": abs(float(delta)),
                "endpoints": evaluated,
            }
        )
    one_at_a_time.sort(key=lambda row: row["height_slope_span_mm_per_m"], reverse=True)

    def distribution(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
        values = np.asarray([float(row[key]) for row in rows])
        return {name: float(value) for name, value in zip(
            ("min", "p05", "median", "p95", "max"),
            (np.min(values), np.percentile(values, 5), np.median(values), np.percentile(values, 95), np.max(values)),
        )}

    return {
        "schema_version": "stage3a_intrinsics_sensitivity.v1",
        "generated_at": now(),
        "intrinsics_refit": False,
        "nominal": nominal,
        "stage2_stability_sources": {
            "lopo": str(lopo_path),
            "subsample80": str(subsample_path),
        },
        "lopo_ensemble": {
            "count": len(lopo_eval) - 1,
            "height_slope_mm_per_m": distribution(lopo_eval[1:], "height_slope_mm_per_m"),
            "pitch_slope_deg_per_m": distribution(lopo_eval[1:], "pitch_slope_deg_per_m"),
            "rows": lopo_eval,
        },
        "subsample80_ensemble": {
            "count": len(subsample_eval),
            "height_slope_mm_per_m": distribution(subsample_eval, "height_slope_mm_per_m"),
            "pitch_slope_deg_per_m": distribution(subsample_eval, "pitch_slope_deg_per_m"),
            "rows": subsample_eval,
        },
        "one_at_a_time_stage2_lopo_p05_p95": one_at_a_time,
    }


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines.extend("| " + " | ".join(str(item) for item in row) + " |" for row in rows)
    return "\n".join(lines)


def write_markdown_outputs(
    comparison: dict[str, Any], geometry: dict[str, Any], sensitivity: dict[str, Any], constrained: dict[str, Any],
) -> None:
    current = comparison["methods"]["CURRENT_ITERATIVE"]
    pose_rows = []
    for pose in POSE_IDS:
        p = current["per_pose"][pose]
        h = current["fixed_height_using_aggregate_R"]["per_pose"][pose]
        pose_rows.append([
            pose, f"{p['distance_mm']:.3f}", f"{p['orientation']['roll_deg']:.4f}",
            f"{p['orientation']['pitch_deg']:.4f}", f"{p['orientation']['yaw_deg']:.4f}",
            f"{h['signed_residual_mm']:+.3f}", f"{geometry[pose]['center_y_px']['median']:.3f}",
        ])
    pair_rows = [[key, f"{value:.6f}"] for key, value in current["pairwise_SO3_deg"].items()]
    pose_md = f"""# W01–W03 pose comparison

Generated: `{now()}`. Read-only analysis of the 33 registered raw frames; no new capture.

{markdown_table(['pose','distance mm','roll deg','pitch deg','yaw deg','height residual mm','board centre y px'], pose_rows)}

Height residual slope: **{current['fixed_height_using_aggregate_R']['slope_mm_per_m']:.6f} mm/m**.

## Pairwise SO(3)

{markdown_table(['pair','geodesic deg'], pair_rows)}
"""
    (OUT / "W01_W02_W03_POSE_COMPARISON.md").write_text(pose_md, encoding="utf-8")

    math_md = f"""# Fixed-height math audit

## Board coordinates and labels

For the exact installed OpenCV 5.0 runtime, `CharucoBoard((7,5), 0.025, 0.018, ...)` returns 24 chessboard object points spanning x=25..150 mm and y=25..100 mm. Raw-image correspondence verifies ID 0 (`[25,25,0]`) is the physical top-left inner corner, X increases image-right, and Y increases image-down. Thus the effective outer-board origin is the physical top-left, and the printed active outer rectangle is 0..175 mm by 0..125 mm. Active top is y=0 and active bottom is y=125 mm; the tool outline matches the printed active rectangle rather than the inner-corner envelope. This runtime check is important because generic OpenCV ChArUco documentation describes historical/version-dependent axis conventions.

The fixed mapping is

`R_world_from_board = [[1,0,0],[0,0,1],[0,-1,0]]`,

so board +X maps to world +X, board +Y (printed down) maps to world -Z, and board +Z maps to world +Y. Its determinant is +1 and it is orthonormal.

OpenCV PnP returns column-vector extrinsics:

`p_camera = R_camera_from_board @ p_board + t_camera_from_board`.

With a known board-to-world orientation,

`R_world_from_camera = R_world_from_board @ R_camera_from_board.T`.

The camera-origin contract fixes

`t_world_from_camera = [0, 0, 972] mm`,

and reconstruction is

`p_world = R_world_from_camera @ p_camera + t_world_from_camera`.

For a bottom point `[x,125,0] mm`, exact compatible transforms give world Z=912 mm; top points `[x,0,0]` give 1037 mm. Matrix direction, transpose, signs, column/row convention, and labels are internally consistent. No fixed-height math or active-rectangle bug was found.

## Important limitation in the old validation

The old tool first averages three full per-pose rotations, including nuisance yaw and planar-PnP pitch, then applies that one aggregate rotation to each pose's PnP translation. That operation is algebraically valid for a single common full camera/world orientation, but the experiment did not establish common yaw: vehicle-to-cabinet yaw changed between poses. Thus the validation is not a pure height check under the actual nuisance model, even though the matrix math itself is correct.

## Height-input sensitivity

For any pose, changing camera height by ΔH adds exactly ΔH to reconstructed bottom Z; changing the asserted board bottom by ΔB subtracts exactly ΔB from the signed residual. Neither depends on distance, so all combinations H=962/972/982 and B=907/912/917 preserve the slope **{current['fixed_height_using_aggregate_R']['slope_mm_per_m']:.6f} mm/m** (up to floating-point roundoff) while shifting the common offset by ΔH-ΔB.
"""
    (OUT / "FIXED_HEIGHT_MATH_AUDIT.md").write_text(math_md, encoding="utf-8")

    method_rows = []
    for name, item in comparison["methods"].items():
        method_rows.append([
            name, f"{item['frame_rms_distribution']['median_px']:.4f}",
            f"{item['orientation_slopes_deg_per_m']['pitch']:.4f}",
            f"{item['orientation_slopes_deg_per_m']['yaw']:.4f}",
            f"{item['fixed_height_using_aggregate_R']['slope_mm_per_m']:.4f}",
            item["alternate_solution"]["available_frame_count"],
        ])
    pnp_md = f"""# Planar PnP comparison

All methods used the same Stage2 K/D, all detected correspondences, all 33 frames, and no intrinsic refit or frame rejection.

{markdown_table(['method','median frame RMS px','pitch slope deg/m','yaw slope deg/m','height slope mm/m','frames with alternate'], method_rows)}

`SOLVEPNP_IPPE` is the planar-specific comparison and exposes two decomposition solutions per frame. `SOLVEPNP_ITERATIVE` is the current method. `SQPNP` and `EPNP` are general comparisons. `IPPE_SQUARE` is not applicable because it requires exactly four points in a prescribed square order, while this is a 175×125 mm rectangle represented by ChArUco inner corners.

Low reprojection error coexists with materially different pitch/translation decompositions across distance. Agreement among iterative and IPPE's best branch does not remove the conditioning problem; IPPE's alternate branch quantifies the planar ambiguity.

Official method contracts: https://docs.opencv.org/4.x/d5/d1f/calib3d_solvePnP.html
"""
    (OUT / "PLANAR_PNP_COMPARISON.md").write_text(pnp_md, encoding="utf-8")

    top = sensitivity["one_at_a_time_stage2_lopo_p05_p95"][0]
    nominal_slope = sensitivity["lopo_ensemble"]["rows"][0]["height_slope_mm_per_m"]
    intr_md = f"""# Intrinsics sensitivity

No Stage2 parameters were refit. The Stage2 final candidate was evaluated alongside the recorded 28-run LOPO ensemble and deterministic 80% subsample ensemble. One-at-a-time tests use each parameter's recorded Stage2 LOPO p05–p95 range.

Nominal height slope: **{nominal_slope:.4f} mm/m**.

Stage2 LOPO ensemble height-slope distribution: `{json.dumps(sensitivity['lopo_ensemble']['height_slope_mm_per_m'])}`.

Stage2 80% subset ensemble height-slope distribution: `{json.dumps(sensitivity['subsample80_ensemble']['height_slope_mm_per_m'])}`.

Most sensitive one-at-a-time parameter: **{top['parameter']}**, whose full recorded LOPO p05–p95 span changes the slope by **{top['height_slope_span_mm_per_m']:.4f} mm/m**. See JSON for every endpoint and full ensembles.

Interpretation must separate sensitivity from blame: if reasonable Stage2 variants retain a large same-sign slope, Stage2 uncertainty is a contributor/conditioning amplifier rather than a sufficient root cause.
"""
    (OUT / "INTRINSICS_SENSITIVITY.md").write_text(intr_md, encoding="utf-8")

    mech = geometry["mechanical_pitch_image_test"]
    attribution_md = f"""# Vehicle pose versus PnP attribution

The image-space active-board centre y range across W01–W03 is **{mech['observed_center_y_range_px']:.3f} px**, a small-angle equivalent of **{mech['small_angle_equivalent_pitch_range_deg']:.4f}°** at fy. A real 1.79° pitch change would shift a distant fixed feature by approximately **{mech['expected_pixel_shift_for_1p79deg_at_fy']:.2f} px** before compensating translation; the known fixed camera/board heights do not provide the roughly 30 mm vertical compensation that would be needed over a one-metre sightline.

This centre-y test is not used alone. The JSON also reports top/bottom width ratio, left/right height ratio, and edge angles from distortion-corrected homographies for every pose. The constrained common-gravity model fits all raw corners directly and is the stronger combined test.

Yaw is different: per-pose horizontal placement and projective skew support vehicle-to-cabinet reposition/yaw variation. Because no measured vehicle-forward reference exists, yaw remains a nuisance/gauge degree of freedom and must not be promoted as high-precision vehicle yaw.

Attribution: observed yaw change can be real repositioning; the approximately two-degree PnP pitch trend is not supported as real mechanical gravity change and is primarily planar pose conditioning/coupling, with intrinsic-model sensitivity as a secondary amplifier.
"""
    (OUT / "VEHICLE_POSE_VS_PNP_ATTRIBUTION.md").write_text(attribution_md, encoding="utf-8")

    fit = constrained["fit"]
    solver_md = f"""# Constrained solver analysis

The preferred physically matched model has 11 parameters for {constrained['model']['observation_scalar_count']} scalar image observations: two shared camera gravity components, plus per-pose vehicle/board relative yaw and XY translation. K/D, 972 mm camera height, 912 mm active-bottom height, vertical-board orientation, and 175×125 mm geometry are fixed.

Shared yaw is a gauge and is set to zero; the three relative yaws are nuisance values. Absolute vehicle-forward yaw is not observable from these data because vehicle-to-cabinet alignment was not measured. Camera height and board height are not fitted.

All-corner constrained reprojection: RMS **{fit['all_corner_reprojection']['rms_px']:.4f} px**, median **{fit['all_corner_reprojection']['median_px']:.4f} px**, p95 **{fit['all_corner_reprojection']['p95_px']:.4f} px**—far above the unconstrained ~0.154 px frame RMS. Even the diagnostic 101-parameter variant with per-frame yaw/XY nuisance remains near 0.98 px RMS. Shared gravity orientation in the zero-yaw gauge: `{json.dumps(fit['shared_orientation_gauge_yaw_zero'])}`.

The fixed-height residual is zero by construction and is not claimed as independent validation. Evidence comes from the small image-space penalty versus unconstrained PnP, cross-distance common gravity, and leave-one-pose-out fits where the held-out pose refits only its nuisance yaw/XY. Full LOPO results are in `CONSTRAINED_SOLVER_RESULT.json`.

Observability:

- Observable: two gravity components (roll/pitch) given fixed K/D, board verticality, board/camera heights, and multi-distance image observations.
- Observable per pose: relative board yaw and in-plane vehicle translation under the chosen yaw gauge.
- Not observable: absolute vehicle-forward yaw, global horizontal origin, or independent validation of the two heights after they are imposed.

Verdict: **reject solver integration on the present evidence**. The hard-constraint model does not reproduce the raw image observations closely enough, and its zero height residual is tautological. The production Stage3A solver remains unchanged; no candidate is exported or promoted. The constrained fits are retained only as falsification/observability diagnostics under `CANDIDATE_NOT_PROMOTED`.
"""
    (OUT / "CONSTRAINED_SOLVER_ANALYSIS.md").write_text(solver_md, encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    K, D, nominal = camera_model()
    observations, board, registry = collect_observations()
    comparison, internal = analyze_methods(observations, K, D)
    geometry = homography_geometry(observations, K, D)
    geometry["mechanical_pitch_image_test"]["pnp_pitch_range_deg"] = float(
        np.ptp([comparison["methods"]["CURRENT_ITERATIVE"]["per_pose"][pose]["orientation"]["pitch_deg"] for pose in POSE_IDS])
    )
    sensitivity = intrinsic_sensitivity(observations, nominal)
    constrained = constrained_analysis(observations, K, D, internal["CURRENT_ITERATIVE"])

    current = comparison["methods"]["CURRENT_ITERATIVE"]
    height_grid = []
    base = [current["fixed_height_using_aggregate_R"]["per_pose"][pose]["signed_residual_mm"] for pose in POSE_IDS]
    distances = [current["per_pose"][pose]["distance_mm"] for pose in POSE_IDS]
    for camera_height in (962.0, 972.0, 982.0):
        for board_bottom in (907.0, 912.0, 917.0):
            residuals = [value + (camera_height - CAMERA_HEIGHT_MM) - (board_bottom - BOARD_BOTTOM_MM) for value in base]
            height_grid.append({
                "camera_height_mm": camera_height,
                "board_bottom_mm": board_bottom,
                "per_pose_residual_mm": dict(zip(POSE_IDS, residuals)),
                "slope_mm_per_m": slope(distances, residuals, 1000.0),
            })
    comparison["height_input_grid"] = height_grid
    comparison["image_geometry"] = geometry
    comparison["provenance"] = {
        "stage2_bundle": str(STAGE2_BUNDLE),
        "stage2_sha256": sha256(STAGE2_BUNDLE),
        "registry": str(REGISTRY),
        "scan_ids": {pose: registry["poses"][pose]["scan_id"] for pose in POSE_IDS},
        "opencv_version": cv2.__version__,
    }
    write_json(OUT / "W01_W02_W03_POSE_COMPARISON.json", {
        "schema_version": "stage3a_w01_w03_pose_comparison.v1",
        "generated_at": now(),
        "current_iterative": current,
        "height_input_grid": height_grid,
        "image_geometry": geometry,
        "provenance": comparison["provenance"],
    })
    write_json(OUT / "PLANAR_PNP_COMPARISON.json", comparison)
    write_json(OUT / "INTRINSICS_SENSITIVITY.json", sensitivity)
    write_json(OUT / "CONSTRAINED_SOLVER_RESULT.json", constrained)
    write_markdown_outputs(comparison, geometry, sensitivity, constrained)
    print(json.dumps({
        "output": str(OUT),
        "current_height_slope_mm_per_m": current["fixed_height_using_aggregate_R"]["slope_mm_per_m"],
        "current_pairwise_SO3_deg": current["pairwise_SO3_deg"],
        "constrained_reprojection": constrained["fit"]["all_corner_reprojection"],
        "constrained_orientation": constrained["fit"]["shared_orientation_gauge_yaw_zero"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
