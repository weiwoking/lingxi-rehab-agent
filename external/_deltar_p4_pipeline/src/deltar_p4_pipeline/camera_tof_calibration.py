from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np

from .camera_calibration import (
    board_pose_m_to_camera_plane_mm,
    detect_observation_image,
    project_board_polygon,
    solve_board_pose,
)
from .camera_calibration_contracts import (
    CameraCalibrationError,
    validate_board_manifest,
    validate_calibration_v2,
)
from .camera_tof_contracts import (
    CameraTofError,
    canonical_sha256,
    fail,
    load_strict_json,
    prepare_output_dir,
    sha256_file,
    validate_capture_manifest,
    validate_profile,
    validate_report,
    write_json,
)
from .charuco_board import charuco_board_from_manifest
from .rgb565 import read_rgb565_le
from .errors import PipelineError
from .scan_profiles import load_profile_bundle
from .scan_contracts import ScanContractError
from .scan_validator import validate_scan
from .zone_orientation import D4_STRATEGIES, canonical_values_from_raw, raw_index_for_canonical_index

TOOL_VERSION = "stage7b1-1.0"
VALID_STATUSES = (5, 9)
PROPER_D4 = {"identity", "rotate_90_cw", "rotate_180", "rotate_270_cw"}
EXPECTED_TX_MM = -20.0


@dataclass(frozen=True)
class PlaneObservation:
    pose_name: str
    split: str
    normal_camera: np.ndarray
    offset_camera_mm: float
    raw_distance_mm: np.ndarray
    raw_mad_mm: np.ndarray
    raw_sigma_mm: np.ndarray
    raw_valid: np.ndarray
    raw_rejections: tuple[tuple[str, ...], ...]
    frame_count: int
    camera_diagnostics: dict[str, Any]
    board_polygon: np.ndarray
    image_rgb: np.ndarray
    source_hashes: dict[str, str]


@dataclass(frozen=True)
class SolverResult:
    rotation: np.ndarray
    translation: np.ndarray
    residuals: np.ndarray
    converged: bool
    iterations: int
    rank: int
    condition_number: float
    robust_scale_mm: float
    cost: float
    initial_rotation_vector: np.ndarray
    initial_translation: np.ndarray


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def nominal_zone_rays(hfov_deg: float = 45.0, vfov_deg: float = 45.0) -> np.ndarray:
    """Build canonical 8x8 unit centre rays: +X right, +Y down, +Z forward."""
    if not (1.0 <= hfov_deg < 179.0 and 1.0 <= vfov_deg < 179.0):
        fail("E_CAMERA_TOF_RAY", "FoV must be in [1,179) degrees", "ray_model")
    x_limit = math.tan(math.radians(hfov_deg) / 2.0)
    y_limit = math.tan(math.radians(vfov_deg) / 2.0)
    xs = ((np.arange(8, dtype=np.float64) + 0.5) / 8.0 * 2.0 - 1.0) * x_limit
    ys = ((np.arange(8, dtype=np.float64) + 0.5) / 8.0 * 2.0 - 1.0) * y_limit
    grid_x, grid_y = np.meshgrid(xs, ys)
    rays = np.stack((grid_x, grid_y, np.ones((8, 8))), axis=-1).reshape(64, 3)
    rays /= np.linalg.norm(rays, axis=1, keepdims=True)
    return rays


def points_from_distances(rays: np.ndarray, distance_mm: np.ndarray, semantics: str) -> np.ndarray:
    rays = np.asarray(rays, dtype=np.float64)
    distance = np.asarray(distance_mm, dtype=np.float64).reshape(-1)
    if rays.shape != (len(distance), 3) or not np.isfinite(rays).all() or not np.isfinite(distance).all():
        fail("E_CAMERA_TOF_RAY", "rays/distances must be finite and shape-compatible", "distance_mm")
    if np.any(distance <= 0) or np.any(rays[:, 2] <= 0):
        fail("E_CAMERA_TOF_RAY", "distances and ray.z must be positive", "distance_mm")
    if semantics == "axial_z":
        return rays * (distance / rays[:, 2])[:, None]
    if semantics == "radial_range":
        unit = rays / np.linalg.norm(rays, axis=1, keepdims=True)
        return unit * distance[:, None]
    fail("E_CAMERA_TOF_RAY", "unsupported distance semantics", "distance_semantics", semantics)


def aggregate_raw_zones(
    tof_frames: Iterable[dict[str, Any]], *, include_status_6: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, tuple[tuple[str, ...], ...]]:
    frames = list(tof_frames)
    if not frames:
        fail("E_CAMERA_TOF_AGGREGATION", "pose has no ToF frames", "tof_frames")
    accepted = set(VALID_STATUSES) | ({6} if include_status_6 else set())
    distances: list[list[float]] = [[] for _ in range(64)]
    sigmas: list[list[float]] = [[] for _ in range(64)]
    reasons: list[set[str]] = [set() for _ in range(64)]
    for frame in frames:
        for index in range(64):
            status = frame["target_status"][index]
            count = frame["nb_target_detected"][index]
            distance = frame["distance_mm"][index]
            sigma = frame["range_sigma_mm"][index]
            if status == 255:
                reasons[index].add("status_255")
                continue
            if status == 10:
                reasons[index].add("status_10")
                continue
            if count == 0:
                reasons[index].add("target_count_0")
                continue
            if status not in accepted:
                reasons[index].add(f"status_{status}")
                continue
            if type(distance) not in {int, float} or not math.isfinite(float(distance)) or distance <= 0:
                reasons[index].add("distance_invalid")
                continue
            if type(sigma) not in {int, float} or not math.isfinite(float(sigma)) or sigma < 0:
                reasons[index].add("sigma_invalid")
                continue
            distances[index].append(float(distance))
            sigmas[index].append(float(sigma))
    median = np.full(64, np.nan, dtype=np.float64)
    mad = np.full(64, np.nan, dtype=np.float64)
    sigma_median = np.full(64, np.nan, dtype=np.float64)
    valid = np.zeros(64, dtype=bool)
    for index, values in enumerate(distances):
        if values:
            array = np.asarray(values)
            median[index] = np.median(array)
            mad[index] = np.median(np.abs(array - median[index]))
            sigma_median[index] = np.median(sigmas[index])
            valid[index] = True
        else:
            reasons[index].add("no_accepted_samples")
    return median, mad, sigma_median, valid, tuple(tuple(sorted(value)) for value in reasons)


def fit_dominant_plane(
    points: np.ndarray, zone_indices: np.ndarray, *, min_inliers: int = 12, max_iterations: int = 6,
) -> tuple[np.ndarray, float, np.ndarray, dict[str, Any]]:
    points = np.asarray(points, dtype=np.float64)
    zones = np.asarray(zone_indices, dtype=np.int64)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) != len(zones):
        fail("E_CAMERA_TOF_PLANE", "points and zone indices have incompatible shapes", "points")
    if len(points) < min_inliers:
        fail("E_CAMERA_TOF_PLANE", "too few valid zones for dominant plane", "points", {"count": len(points), "minimum": min_inliers})
    mask = np.ones(len(points), dtype=bool)
    residual = np.zeros(len(points), dtype=np.float64)
    for _ in range(max_iterations):
        selected = points[mask]
        centroid = np.mean(selected, axis=0)
        _, singular, vh = np.linalg.svd(selected - centroid, full_matrices=False)
        normal = vh[-1]
        normal /= np.linalg.norm(normal)
        if normal[2] < 0:
            normal = -normal
        offset = -float(normal @ centroid)
        residual = np.abs(points @ normal + offset)
        centre = float(np.median(residual[mask]))
        mad = float(np.median(np.abs(residual[mask] - centre)))
        threshold = max(5.0, centre + 4.5 * max(1.4826 * mad, 0.5))
        updated = residual <= threshold
        if np.array_equal(updated, mask):
            break
        if np.count_nonzero(updated) < min_inliers:
            break
        mask = updated
    if np.count_nonzero(mask) < min_inliers:
        fail("E_CAMERA_TOF_PLANE", "dominant plane has too few inliers", "points")
    coords = np.column_stack((zones[mask] % 8, zones[mask] // 8)).astype(np.float64)
    if np.linalg.matrix_rank(coords - np.mean(coords, axis=0), tol=1e-6) < 2:
        fail("E_CAMERA_TOF_PLANE", "dominant-plane zones are spatially degenerate", "zone_indices")
    selected = points[mask]
    centroid = np.mean(selected, axis=0)
    _, singular, vh = np.linalg.svd(selected - centroid, full_matrices=False)
    normal = vh[-1] / np.linalg.norm(vh[-1])
    if normal[2] < 0:
        normal = -normal
    offset = -float(normal @ centroid)
    residual = np.abs(points @ normal + offset)
    return normal, offset, mask, {
        "valid_zone_count": int(len(points)),
        "plane_inlier_count": int(np.count_nonzero(mask)),
        "median_abs_plane_residual_mm": float(np.median(residual[mask])),
        "mad_abs_plane_residual_mm": float(np.median(np.abs(residual[mask] - np.median(residual[mask])))),
        "plane_normal_tof": normal.tolist(),
        "plane_offset_tof_mm": offset,
        "singular_values": singular.tolist(),
        "rejected_canonical_zones": zones[~mask].tolist(),
    }


def transform_points(rotation: np.ndarray, translation: np.ndarray, points: np.ndarray) -> np.ndarray:
    return (np.asarray(rotation) @ np.asarray(points).T).T + np.asarray(translation).reshape(1, 3)


def inverse_transform(rotation: np.ndarray, translation: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    r_inv = np.asarray(rotation, dtype=np.float64).T
    return r_inv, -r_inv @ np.asarray(translation, dtype=np.float64).reshape(3)


def transform_plane_camera_to_tof(
    normal_camera: np.ndarray, offset_camera: float, rotation_camera_from_tof: np.ndarray,
    translation_camera_from_tof: np.ndarray,
) -> tuple[np.ndarray, float]:
    normal = np.asarray(rotation_camera_from_tof, dtype=np.float64).T @ np.asarray(normal_camera, dtype=np.float64)
    offset = float(np.asarray(normal_camera) @ np.asarray(translation_camera_from_tof) + offset_camera)
    length = np.linalg.norm(normal)
    return normal / length, offset / length


def residual_and_jacobian(
    rotation: np.ndarray, translation: np.ndarray, points: np.ndarray,
    normals: np.ndarray, offsets: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Left rotation perturbation and additive Camera-frame translation.

    ``R_new = Rodrigues(delta_r) @ R`` and ``t_new = t + delta_t``.
    Thus ``J_rotation = (R p x n)^T`` and ``J_translation = n^T``.
    """
    q = (np.asarray(rotation, dtype=np.float64) @ np.asarray(points, dtype=np.float64).T).T
    normals = np.asarray(normals, dtype=np.float64)
    residual = np.sum(normals * (q + np.asarray(translation).reshape(1, 3)), axis=1) + np.asarray(offsets)
    jacobian = np.column_stack((np.cross(q, normals), normals))
    return residual, jacobian


def residual_summary(residuals: np.ndarray) -> dict[str, float]:
    values = np.abs(np.asarray(residuals, dtype=np.float64))
    if values.size == 0:
        fail("E_CAMERA_TOF_RESIDUAL", "cannot summarize an empty residual array", "residuals")
    return {
        "count": int(values.size), "mean_mm": float(np.mean(values)),
        "median_mm": float(np.median(values)),
        "rms_mm": float(np.sqrt(np.mean(np.square(values)))),
        "p95_mm": float(np.percentile(values, 95)), "max_mm": float(np.max(values)),
    }


def solve_point_to_plane(
    points: np.ndarray, normals: np.ndarray, offsets: np.ndarray, *,
    initial_rotation_vector: np.ndarray | None = None,
    initial_translation: np.ndarray | None = None,
    max_iterations: int = 80, damping: float = 1e-3, tolerance: float = 1e-9,
    max_rotation_deg: float = 180.0,
) -> SolverResult:
    points = np.asarray(points, dtype=np.float64)
    normals = np.asarray(normals, dtype=np.float64)
    offsets = np.asarray(offsets, dtype=np.float64)
    if points.shape != normals.shape or points.ndim != 2 or points.shape[1] != 3 or offsets.shape != (len(points),):
        fail("E_CAMERA_TOF_SOLVER", "point/plane arrays have incompatible shapes", "solver_inputs")
    if len(points) < 12 or not np.isfinite(points).all() or not np.isfinite(normals).all() or not np.isfinite(offsets).all():
        fail("E_CAMERA_TOF_SOLVER", "solver inputs are insufficient or non-finite", "solver_inputs")
    rvec0 = np.zeros(3) if initial_rotation_vector is None else np.asarray(initial_rotation_vector, dtype=np.float64).reshape(3)
    t0 = np.asarray(
        [EXPECTED_TX_MM, 3.5, 6.5]
        if initial_translation is None
        else initial_translation,
        dtype=np.float64,
    ).reshape(3)
    rotation, _ = cv2.Rodrigues(rvec0)
    translation = t0.copy()
    lm = float(damping)
    converged = False
    cost = math.inf
    scale = 1.0
    iteration = -1
    for iteration in range(max_iterations):
        residual, jacobian = residual_and_jacobian(rotation, translation, points, normals, offsets)
        if not np.isfinite(residual).all() or not np.isfinite(jacobian).all():
            fail("E_CAMERA_TOF_SOLVER", "solver produced NaN/Inf", "iteration", iteration)
        median = float(np.median(residual))
        scale = max(1.4826 * float(np.median(np.abs(residual - median))), 0.5)
        abs_residual = np.abs(residual)
        huber = 1.345 * scale
        weights = np.ones_like(abs_residual)
        large = abs_residual > huber
        weights[large] = huber / abs_residual[large]
        sqrt_w = np.sqrt(weights)
        weighted_j = jacobian * sqrt_w[:, None]
        weighted_r = residual * sqrt_w
        current_cost = float(np.sum(np.where(abs_residual <= huber, 0.5 * residual ** 2, huber * (abs_residual - 0.5 * huber))))
        accepted = False
        for _ in range(12):
            hessian = weighted_j.T @ weighted_j + lm * np.diag(np.maximum(np.diag(weighted_j.T @ weighted_j), 1.0))
            gradient = weighted_j.T @ weighted_r
            try:
                delta = -np.linalg.solve(hessian, gradient)
            except np.linalg.LinAlgError as exc:
                raise CameraTofError(
                    "E_CAMERA_TOF_UNOBSERVABLE", "normal equations are singular", "solver",
                ) from exc
            if not np.isfinite(delta).all():
                fail("E_CAMERA_TOF_SOLVER", "non-finite LM step", "solver")
            delta_rotation, _ = cv2.Rodrigues(delta[:3])
            trial_rotation = delta_rotation @ rotation
            trial_translation = translation + delta[3:]
            if np.any(np.abs(trial_translation) > 150.0):
                lm *= 10.0
                continue
            trial_rvec, _ = cv2.Rodrigues(trial_rotation)
            if np.linalg.norm(trial_rvec.reshape(3)) > math.radians(max_rotation_deg):
                lm *= 10.0
                continue
            trial_residual, _ = residual_and_jacobian(trial_rotation, trial_translation, points, normals, offsets)
            trial_abs = np.abs(trial_residual)
            trial_cost = float(np.sum(np.where(trial_abs <= huber, 0.5 * trial_residual ** 2, huber * (trial_abs - 0.5 * huber))))
            if trial_cost <= current_cost:
                rotation, translation, cost = trial_rotation, trial_translation, trial_cost
                lm = max(lm / 3.0, 1e-12)
                accepted = True
                break
            lm *= 10.0
        if not accepted:
            break
        if np.linalg.norm(delta) < tolerance or abs(current_cost - cost) < tolerance * max(1.0, current_cost):
            converged = True
            break
    residual, jacobian = residual_and_jacobian(rotation, translation, points, normals, offsets)
    singular = np.linalg.svd(jacobian, compute_uv=False)
    threshold = max(jacobian.shape) * np.finfo(float).eps * singular[0]
    rank = int(np.count_nonzero(singular > threshold))
    condition = float(singular[0] / singular[-1]) if singular[-1] > threshold else math.inf
    if rank < 6 or not math.isfinite(condition) or condition > 1e9:
        fail("E_CAMERA_TOF_UNOBSERVABLE", "point-to-plane system is rank-deficient or ill-conditioned", "solver", {
            "rank": rank, "condition_number": condition if math.isfinite(condition) else "infinite",
        })
    if not converged:
        fail("E_CAMERA_TOF_SOLVER", "LM failed its convergence/cost-decrease gate", "solver", {"iterations": iteration + 1})
    return SolverResult(
        rotation=rotation, translation=translation, residuals=residual,
        converged=True, iterations=iteration + 1, rank=rank,
        condition_number=condition, robust_scale_mm=scale, cost=cost,
        initial_rotation_vector=rvec0, initial_translation=t0,
    )


def _camera_parameters(profile: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, str]:
    validate_calibration_v2(profile)
    if profile.get("camera_tof_extrinsics") != {"status": "unresolved"} or profile.get("absolute_geometry_valid") is not False:
        fail("E_CAMERA_TOF_CAMERA_PROFILE", "Stage 7A source profile must remain unresolved for extrinsics", "camera_tof_extrinsics")
    intrinsics = profile["camera"]["intrinsics"]
    matrix = np.asarray([[intrinsics["fx"], 0.0, intrinsics["cx"]], [0.0, intrinsics["fy"], intrinsics["cy"]], [0.0, 0.0, 1.0]])
    distortion = intrinsics["distortion"]
    coefficients = np.asarray([distortion[key] for key in ("k1", "k2", "p1", "p2", "k3")], dtype=np.float64)
    return matrix, coefficients, profile["profile_id"]


def _mean_camera_plane(planes: list[tuple[np.ndarray, float]]) -> tuple[np.ndarray, float]:
    reference = planes[0][0]
    normals = np.stack([(-n if n @ reference < 0 else n) for n, _ in planes])
    offsets = np.asarray([(-d if n @ reference < 0 else d) for n, d in planes])
    normal = np.mean(normals, axis=0)
    normal /= np.linalg.norm(normal)
    return normal, float(np.mean(offsets))


def load_capture_observations(
    manifest_path: Path, camera_profile_path: Path, board_manifest_path: Path, *,
    profile_bundle_path: Path | None = None, include_status_6: bool = False,
) -> tuple[dict[str, Any], list[PlaneObservation], dict[str, Any]]:
    manifest = validate_capture_manifest(
        load_strict_json(manifest_path), manifest_path=manifest_path,
        camera_profile_path=camera_profile_path, board_manifest_path=board_manifest_path,
    )
    camera_profile = load_strict_json(camera_profile_path)
    matrix, distortion, camera_profile_id = _camera_parameters(camera_profile)
    board_manifest = validate_board_manifest(load_strict_json(board_manifest_path), require_print_measurement=True)
    board = charuco_board_from_manifest(board_manifest)
    bundle = load_profile_bundle(profile_bundle_path or Path(__file__).resolve().parents[2] / "profiles", validate_model_hashes=False)
    observations: list[PlaneObservation] = []
    all_hashes = {
        str(manifest_path.resolve()): sha256_file(manifest_path),
        str(camera_profile_path.resolve()): sha256_file(camera_profile_path),
        str(board_manifest_path.resolve()): sha256_file(board_manifest_path),
    }
    for pose in manifest["_resolved_poses"]:
        tof_frames: list[dict[str, Any]] = []
        camera_planes: list[tuple[np.ndarray, float]] = []
        camera_stats: list[dict[str, Any]] = []
        representative_image: np.ndarray | None = None
        polygon: np.ndarray | None = None
        pose_hashes: dict[str, str] = {}
        for scan_dir in pose["_scan_dirs"]:
            validated = validate_scan(scan_dir, bundle)
            scan_manifest_path = scan_dir / "scan.json"
            pose_hashes[str(scan_manifest_path.resolve())] = sha256_file(scan_manifest_path)
            for frame in validated.frames:
                image = read_rgb565_le(frame.image_path)
                # Stage 7A OpenCV paths use BGR; RGB565 decoder returns RGB.
                bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
                image_hash = sha256_file(frame.image_path)
                observation = detect_observation_image(
                    bgr, board, min_charuco_corners=8,
                    source_name=f"{pose['pose_name']}/{frame.frame['frame_id']}", source_sha256=image_hash,
                )
                rotation, translation, diagnostics = solve_board_pose(observation, matrix, distortion)
                if diagnostics["rms_px"] > 2.0:
                    fail("E_CAMERA_TOF_PNP", "PnP reprojection RMS exceeds 2 px", pose["pose_name"], diagnostics)
                camera_planes.append(
                    board_pose_m_to_camera_plane_mm(rotation, translation)
                )
                camera_stats.append(diagnostics)
                tof_frames.append(frame.tof)
                polygon = project_board_polygon(
                    rotation, translation, matrix, distortion,
                    float(board_manifest["squares_x"]) * float(board_manifest["square_length_mm"]),
                    float(board_manifest["squares_y"]) * float(board_manifest["square_length_mm"]),
                )
                representative_image = bgr
                for source in (frame.image_path, frame.directory / "frame.json", frame.directory / "tof.json"):
                    pose_hashes[str(source.resolve())] = sha256_file(source)
        if not tof_frames or representative_image is None or polygon is None:
            fail("E_CAMERA_TOF_CAPTURE", "pose contains no validated frames", pose["pose_name"])
        median, mad, sigma, valid, reasons = aggregate_raw_zones(tof_frames, include_status_6=include_status_6)
        normal, offset = _mean_camera_plane(camera_planes)
        observations.append(PlaneObservation(
            pose_name=pose["pose_name"], split=pose["split"], normal_camera=normal,
            offset_camera_mm=offset, raw_distance_mm=median, raw_mad_mm=mad,
            raw_sigma_mm=sigma, raw_valid=valid, raw_rejections=reasons,
            frame_count=len(tof_frames),
            camera_diagnostics={
                "frame_count": len(camera_stats),
                "rms_px_median": float(np.median([item["rms_px"] for item in camera_stats])),
                "rms_px_max": float(np.max([item["rms_px"] for item in camera_stats])),
                "normal_camera": normal.tolist(), "offset_camera_mm": offset,
            },
            board_polygon=polygon, image_rgb=representative_image,
            source_hashes=pose_hashes,
        ))
        all_hashes.update(pose_hashes)
    return manifest, observations, {"camera_profile_id": camera_profile_id, "matrix": matrix, "distortion": distortion, "source_hashes": all_hashes}


def _candidate_dataset(
    observations: list[PlaneObservation], orientation: str, semantics: str,
) -> tuple[dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]], dict[str, dict[str, Any]]]:
    rays = nominal_zone_rays()
    datasets: dict[str, list[np.ndarray]] = {
        "train_points": [], "train_normals": [], "train_offsets": [], "train_zones": [],
        "heldout_points": [], "heldout_normals": [], "heldout_offsets": [], "heldout_zones": [],
    }
    diagnostics: dict[str, dict[str, Any]] = {}
    for pose in observations:
        raw_map = raw_index_for_canonical_index(orientation)
        distance = canonical_values_from_raw(pose.raw_distance_mm, orientation)
        aggregate_valid = canonical_values_from_raw(pose.raw_valid, orientation).astype(bool)
        valid = aggregate_valid.copy()
        mad = canonical_values_from_raw(pose.raw_mad_mm, orientation)
        sigma = canonical_values_from_raw(pose.raw_sigma_mm, orientation)
        valid &= np.isfinite(distance) & np.isfinite(mad) & np.isfinite(sigma)
        valid &= mad <= np.maximum(20.0, 3.0 * sigma)
        rejected_zones = []
        for canonical in np.flatnonzero(~valid):
            raw = int(raw_map[canonical])
            reasons = list(pose.raw_rejections[raw])
            if aggregate_valid[canonical] and np.isfinite(mad[canonical]) and np.isfinite(sigma[canonical]) and mad[canonical] > max(20.0, 3.0 * sigma[canonical]):
                reasons.append("pose_mad_exceeds_gate")
            rejected_zones.append({
                "canonical_zone": int(canonical), "raw_zone": raw,
                "reasons": sorted(set(reasons or ["nonfinite_aggregate"])),
            })
        zones = np.flatnonzero(valid)
        points = points_from_distances(rays[zones], distance[zones], semantics)
        _, _, inliers, plane_diag = fit_dominant_plane(points, zones)
        points = points[inliers]
        zones = zones[inliers]
        prefix = pose.split
        datasets[f"{prefix}_points"].append(points)
        datasets[f"{prefix}_normals"].append(np.repeat(pose.normal_camera[None, :], len(points), axis=0))
        datasets[f"{prefix}_offsets"].append(np.repeat(pose.offset_camera_mm, len(points)))
        datasets[f"{prefix}_zones"].append(zones)
        diagnostics[pose.pose_name] = {
            **plane_diag,
            "split": pose.split,
            "frame_count": pose.frame_count,
            "distance_median_mm": float(np.median(distance[valid])),
            "distance_mad_mm": float(np.median(mad[valid])),
            "camera_plane": pose.camera_diagnostics,
            "inlier_canonical_zones": zones.tolist(),
            "aggregation_rejected_zones": rejected_zones,
        }
    result: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
    for split in ("train", "heldout"):
        if not datasets[f"{split}_points"]:
            fail("E_CAMERA_TOF_SPLIT", f"{split} has no points", split)
        result[split] = (
            np.concatenate(datasets[f"{split}_points"]),
            np.concatenate(datasets[f"{split}_normals"]),
            np.concatenate(datasets[f"{split}_offsets"]),
            np.concatenate(datasets[f"{split}_zones"]),
        )
    return result, diagnostics


def _initializations() -> list[tuple[str, np.ndarray, np.ndarray]]:
    values: list[tuple[str, np.ndarray, np.ndarray]] = []
    angle_vectors = (
        np.zeros(3),
        np.radians([2.0, -2.0, 1.0]),
        np.radians([-2.0, 2.0, -1.0]),
    )
    for y_sign in (-1, 1):
        for z_sign in (-1, 1):
            for index, angles in enumerate(angle_vectors):
                values.append(
                    (
                        f"y{y_sign:+d}_z{z_sign:+d}_a{index}",
                        angles,
                        np.asarray(
                            [EXPECTED_TX_MM, y_sign * 3.5, z_sign * 6.5]
                        ),
                    )
                )
    return values


def select_train_solution(
    solutions: list[tuple[str, SolverResult]],
) -> tuple[str, SolverResult]:
    """Freeze one local solution using train-only diagnostics.

    Held-out data is deliberately absent from this interface.  Mechanical
    plausibility is only a final deterministic tie-break after train residual,
    conditioning, and multi-start cluster stability.
    """
    if not solutions:
        fail("E_CAMERA_TOF_SOLVER", "cannot select from an empty solution set", "solutions")
    rotation_vectors = np.stack([
        cv2.Rodrigues(solution.rotation)[0].reshape(3)
        for _, solution in solutions
    ])
    translations = np.stack([solution.translation for _, solution in solutions])
    median_rotation = np.median(rotation_vectors, axis=0)
    median_translation = np.median(translations, axis=0)

    def train_key(item: tuple[str, SolverResult]) -> tuple[Any, ...]:
        label, solution = item
        summary = residual_summary(solution.residuals)
        rotation_vector = cv2.Rodrigues(solution.rotation)[0].reshape(3)
        stability_distance = (
            float(np.linalg.norm(rotation_vector - median_rotation)),
            float(np.linalg.norm(solution.translation - median_translation)),
        )
        mechanical_tie_break = (
            not tx_sign_matches_expected(solution.translation[0]),
            float(np.linalg.norm(rotation_vector)),
            abs(float(solution.translation[0]) - EXPECTED_TX_MM),
            float(np.linalg.norm(solution.translation)),
        )
        return (
            summary["rms_mm"],
            summary["p95_mm"],
            summary["max_mm"],
            float(solution.condition_number),
            *stability_distance,
            *mechanical_tie_break,
            label,
        )

    return min(solutions, key=train_key)


def tx_sign_matches_expected(
    tx_mm: float, expected_tx_mm: float = EXPECTED_TX_MM
) -> bool:
    """Return whether a Camera-X translation has the expected nonzero sign."""
    return bool(
        math.isfinite(float(tx_mm))
        and math.isfinite(float(expected_tx_mm))
        and float(tx_mm) * float(expected_tx_mm) > 0.0
    )


def mechanical_diagnostics(
    rotation_camera_from_tof: np.ndarray,
    translation_camera_from_tof_mm: np.ndarray,
) -> dict[str, Any]:
    """Describe the frozen Camera-coordinate mechanical plausibility contract."""
    rotation = np.asarray(rotation_camera_from_tof, dtype=np.float64)
    translation = np.asarray(
        translation_camera_from_tof_mm, dtype=np.float64
    ).reshape(3)
    tx_mm = float(translation[0])
    return {
        "expected_tx_mm": EXPECTED_TX_MM,
        "tx_sign_matches_expected": tx_sign_matches_expected(tx_mm),
        "tx_error_from_expected_mm": abs(tx_mm - EXPECTED_TX_MM),
        "translation_norm_mm": float(np.linalg.norm(translation)),
        "rotation_angle_deg": float(
            np.degrees(np.linalg.norm(cv2.Rodrigues(rotation)[0]))
        ),
    }


def enumerate_candidates(observations: list[PlaneObservation]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ranking: list[dict[str, Any]] = []
    per_mapping_diagnostics: dict[str, Any] = {}
    for orientation in D4_STRATEGIES:
        for semantics in ("axial_z", "radial_range"):
            candidate_key = f"{orientation}/{semantics}"
            try:
                datasets, pose_diagnostics = _candidate_dataset(observations, orientation, semantics)
                train_points, train_normals, train_offsets, _ = datasets["train"]
                held_points, held_normals, held_offsets, _ = datasets["heldout"]
                starts: list[dict[str, Any]] = []
                solutions: list[tuple[str, SolverResult]] = []
                for label, rvec, translation in _initializations():
                    try:
                        solved = solve_point_to_plane(
                            train_points, train_normals, train_offsets,
                            initial_rotation_vector=rvec, initial_translation=translation,
                        )
                        solutions.append((label, solved))
                        starts.append({
                            "label": label, "status": "success",
                            "train": residual_summary(solved.residuals),
                            "rotation_matrix": solved.rotation.tolist(),
                            "translation_mm": solved.translation.tolist(),
                            "rank": solved.rank, "condition_number": solved.condition_number,
                            "iterations": solved.iterations,
                        })
                    except CameraTofError as exc:
                        starts.append({"label": label, "status": "failed", "error": exc.as_dict()})
                if not solutions:
                    raise CameraTofError("E_CAMERA_TOF_SOLVER", "all initializations failed", candidate_key)
                selected_start, best = select_train_solution(solutions)
                for start in starts:
                    if start["status"] == "success":
                        start["selected"] = start["label"] == selected_start
                held_residual, _ = residual_and_jacobian(best.rotation, best.translation, held_points, held_normals, held_offsets)
                rotations = np.stack([cv2.Rodrigues(item.rotation)[0].reshape(3) for _, item in solutions])
                translations = np.stack([item.translation for _, item in solutions])
                stability = {
                    "successful_starts": len(solutions),
                    "total_starts": len(_initializations()),
                    "rotation_spread_deg": float(np.degrees(np.max(np.linalg.norm(rotations - np.median(rotations, axis=0), axis=1)))),
                    "translation_spread_mm": float(np.max(np.linalg.norm(translations - np.median(translations, axis=0), axis=1))),
                }
                ranking.append({
                    "candidate_id": candidate_key, "status": "success",
                    "orientation": orientation, "distance_semantics": semantics,
                    "orientation_class": "proper_rotation_gauge" if orientation in PROPER_D4 else "reflection",
                    "train": residual_summary(best.residuals),
                    "heldout": residual_summary(held_residual),
                    "rotation_matrix": best.rotation.tolist(),
                    "translation_mm": best.translation.tolist(),
                    "rank": best.rank, "condition_number": best.condition_number,
                    "robust_scale_mm": best.robust_scale_mm, "stability": stability,
                    "selected_start": selected_start,
                    "internal_solution_selection": "train_only",
                    "mechanical": mechanical_diagnostics(
                        best.rotation, best.translation
                    ),
                    "starts": starts,
                })
                per_mapping_diagnostics[candidate_key] = pose_diagnostics
            except CameraTofError as exc:
                ranking.append({
                    "candidate_id": candidate_key, "status": "failed",
                    "orientation": orientation, "distance_semantics": semantics,
                    "orientation_class": "proper_rotation_gauge" if orientation in PROPER_D4 else "reflection",
                    "error": exc.as_dict(),
                })
    ranking.sort(key=lambda item: (
        item["status"] != "success",
        item.get("heldout", {}).get("rms_mm", math.inf),
        item.get("train", {}).get("rms_mm", math.inf),
        not item.get("mechanical", {}).get("tx_sign_matches_expected", False),
        item.get("mechanical", {}).get("rotation_angle_deg", math.inf),
        item.get("mechanical", {}).get("tx_error_from_expected_mm", math.inf),
    ))
    return ranking, per_mapping_diagnostics


def manifest_template(
    output_path: Path, *, camera_profile: Path, board_manifest: Path,
) -> dict[str, Any]:
    if output_path.exists():
        fail("E_CAMERA_TOF_OUTPUT", "manifest output already exists", str(output_path))
    if not camera_profile.is_file() or not board_manifest.is_file():
        fail("E_CAMERA_TOF_PATH", "camera profile and board manifest must exist", "template_sources")
    poses = []
    names = (
        "front_near", "front_mid", "front_far", "tilt_left", "tilt_right",
        "tilt_up", "tilt_down", "yaw_pitch", "heldout_left",
        "heldout_right", "heldout_up", "heldout_down",
    )
    for index, name in enumerate(names):
        poses.append({
            "pose_name": name, "split": "train" if index < 8 else "heldout",
            "scan_dir": f"scans/{name}", "stationary_confirmed": True,
            "estimated_distance_mm": 350 + (index % 4) * 100,
            "tilt_hint": name, "image_region_hint": "centre",
            "notes": "Replace scan_dir and hints after collecting exactly 5 stationary frames.",
        })
    value = {
        "schema_version": "camera_tof_capture.v1",
        "capture_id": "stage7b_capture_REPLACE_TIMESTAMP",
        "created_at": utc_now(),
        "camera_profile": str(camera_profile.resolve()),
        "camera_profile_sha256": sha256_file(camera_profile),
        "board_manifest": str(board_manifest.resolve()),
        "board_manifest_sha256": sha256_file(board_manifest),
        "backer": {
            "width_mm": 900.0, "height_mm": 600.0,
            "material": "rigid matte opaque board", "matte": True, "opaque": True,
        },
        "poses": poses,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, value)
    return value


def calibrate_capture(
    capture_manifest_path: Path, camera_profile_path: Path, board_manifest_path: Path,
    output_dir: Path, *, profile_bundle_path: Path | None = None,
    include_status_6: bool = False,
) -> dict[str, Any]:
    output = prepare_output_dir(output_dir)
    created_at = utc_now()
    try:
        manifest, observations, metadata = load_capture_observations(
            capture_manifest_path, camera_profile_path, board_manifest_path,
            profile_bundle_path=profile_bundle_path, include_status_6=include_status_6,
        )
        ranking, all_diagnostics = enumerate_candidates(observations)
        from .camera_tof_outputs import (
            build_profile, select_candidate, write_overlays, write_residual_csv,
        )
        best, margin, gates, warnings = select_candidate(ranking, observations)
        passed = best is not None and all(item["status"] == "PASS" for item in gates)
        chosen_diagnostics = {} if best is None else all_diagnostics[best["candidate_id"]]
        write_json(output / "candidate_ranking.json", {
            "schema_version": "camera_tof_candidate_ranking.v1",
            "created_at": created_at, "candidates": ranking,
            "selection": None if best is None else best["candidate_id"],
            "candidate_margin": margin,
        })
        write_json(output / "source_hashes.json", {
            "schema_version": "camera_tof_source_hashes.v1",
            "files": metadata["source_hashes"],
        })
        candidate = {
            "schema_version": "camera_tof_extrinsics_candidate.v1",
            "created_at": created_at, "validation_status": "PASS" if passed else "FAIL",
            "best_candidate": best, "candidate_margin": margin,
            "gates": gates, "warnings": warnings,
            "formal_profile_generated": bool(passed),
            "absolute_geometry_valid": False, "world_pose_resolved": False,
        }
        write_json(output / "camera_tof_extrinsics.candidate.json", candidate)
        overlay_mask = [False] * 64
        if best is not None:
            overlay_mask, _ = write_overlays(
                output, observations, best, chosen_diagnostics,
                metadata["matrix"], metadata["distortion"],
            )
            write_residual_csv(output / "residuals.csv", observations, best, chosen_diagnostics)
        else:
            (output / "residuals.csv").write_text(
                "pose_name,split,canonical_zone,raw_zone,distance_mm,residual_mm,dominant_plane_inlier\n",
                encoding="utf-8",
            )
            (output / "overlays").mkdir()
        # Overlay generation may add a persisted diagnostic when a non-finite
        # board polygon is safely skipped, so write pose diagnostics afterward.
        write_json(output / "pose_diagnostics.json", {
            "schema_version": "camera_tof_pose_diagnostics.v1",
            "selected_candidate": None if best is None else best["candidate_id"],
            "poses": chosen_diagnostics,
        })
        report = {
            "schema_version": "camera_tof_calibration_report.v1",
            "created_at": created_at,
            "capture_id": manifest["capture_id"],
            "status": "PASS" if passed else "FAIL",
            "validation_status": "PASS" if passed else "FAIL",
            "selected_candidate": None if best is None else best["candidate_id"],
            "training_pose_count": sum(item.split == "train" for item in observations),
            "heldout_pose_count": sum(item.split == "heldout" for item in observations),
            "gates": gates, "warnings": warnings,
            "formal_profile_generated": bool(passed),
            "camera_tof_extrinsics_status": "measured" if passed else "unresolved",
            "absolute_geometry_valid": False, "world_pose_resolved": False,
            "tool_version": TOOL_VERSION,
        }
        validate_report(report)
        write_json(output / "calibration_report.json", report)
        if passed and best is not None:
            public_metadata = {
                **metadata, "created_at": created_at,
            }
            profile = build_profile(
                best, margin, observations, chosen_diagnostics,
                public_metadata, overlay_mask, warnings,
            )
            validate_profile(profile, require_pass=True)
            write_json(output / "camera_tof_extrinsics.v1.json", profile)
        return report
    except (CameraTofError, CameraCalibrationError, ScanContractError, PipelineError) as exc:
        normalized = exc if isinstance(exc, CameraTofError) else CameraTofError(
            "E_CAMERA_TOF_CAPTURE", str(getattr(exc, "message", exc)),
            getattr(exc, "field", None), getattr(exc, "details", None),
        )
        report = {
            "schema_version": "camera_tof_calibration_report.v1",
            "created_at": created_at, "capture_id": None,
            "status": "FAIL", "validation_status": "FAIL",
            "selected_candidate": None, "training_pose_count": 0,
            "heldout_pose_count": 0,
            "gates": [{"name": "input_or_processing", "status": "FAIL", "details": normalized.as_dict()}],
            "warnings": [], "formal_profile_generated": False,
            "camera_tof_extrinsics_status": "unresolved",
            "absolute_geometry_valid": False, "world_pose_resolved": False,
            "tool_version": TOOL_VERSION,
        }
        validate_report(report)
        write_json(output / "calibration_report.json", report)
        if normalized is exc:
            raise
        raise normalized from exc


def validate_capture_with_profile(
    profile_path: Path, capture_manifest_path: Path, output_dir: Path, *,
    profile_bundle_path: Path | None = None,
) -> dict[str, Any]:
    """Score a frozen PASS profile without optimizing on capture data."""
    profile = validate_profile(load_strict_json(profile_path), require_pass=True)
    manifest_raw = load_strict_json(capture_manifest_path)
    manifest_root = capture_manifest_path.resolve().parent
    camera_profile = Path(manifest_raw["camera_profile"])
    board_manifest = Path(manifest_raw["board_manifest"])
    if not camera_profile.is_absolute():
        camera_profile = manifest_root / camera_profile
    if not board_manifest.is_absolute():
        board_manifest = manifest_root / board_manifest
    output = prepare_output_dir(output_dir)
    manifest, observations, metadata = load_capture_observations(
        capture_manifest_path, camera_profile, board_manifest,
        profile_bundle_path=profile_bundle_path,
    )
    orientation = profile["orientation"]["name"]
    semantics = profile["distance_semantics"]
    datasets, diagnostics = _candidate_dataset(observations, orientation, semantics)
    rotation = np.asarray(profile["rotation_matrix"])
    translation = np.asarray(profile["translation"])
    split_results: dict[str, Any] = {}
    for split, (points, normals, offsets, _) in datasets.items():
        residual, _ = residual_and_jacobian(rotation, translation, points, normals, offsets)
        split_results[split] = residual_summary(residual)
    status = "PASS" if split_results["heldout"]["rms_mm"] <= max(15.0, 3.0 * split_results["train"]["rms_mm"]) else "FAIL"
    report = {
        "schema_version": "camera_tof_calibration_report.v1",
        "created_at": utc_now(), "capture_id": manifest["capture_id"],
        "status": status, "validation_status": status,
        "selected_candidate": f"{orientation}/{semantics}",
        "training_pose_count": sum(item.split == "train" for item in observations),
        "heldout_pose_count": sum(item.split == "heldout" for item in observations),
        "gates": [{"name": "frozen_profile_residual", "status": status, "details": split_results}],
        "warnings": profile["warnings"], "formal_profile_generated": False,
        "camera_tof_extrinsics_status": "measured",
        "absolute_geometry_valid": False, "world_pose_resolved": False,
        "tool_version": TOOL_VERSION,
    }
    from .camera_tof_outputs import write_overlays, write_residual_csv
    best = {
        "candidate_id": f"{orientation}/{semantics}",
        "status": "success", "orientation": orientation,
        "distance_semantics": semantics,
        "rotation_matrix": rotation.tolist(), "translation_mm": translation.tolist(),
        "train": split_results["train"], "heldout": split_results["heldout"],
    }
    write_json(output / "candidate_ranking.json", {
        "schema_version": "camera_tof_candidate_ranking.v1",
        "created_at": report["created_at"], "candidates": [best],
        "selection": best["candidate_id"], "candidate_margin": profile["candidate_margin"],
    })
    write_json(output / "camera_tof_extrinsics.candidate.json", {
        "schema_version": "camera_tof_extrinsics_candidate.v1",
        "created_at": report["created_at"], "validation_status": status,
        "best_candidate": best, "candidate_margin": profile["candidate_margin"],
        "gates": report["gates"], "warnings": report["warnings"],
        "formal_profile_generated": False,
        "absolute_geometry_valid": False, "world_pose_resolved": False,
    })
    write_overlays(
        output, observations, best, diagnostics,
        metadata["matrix"], metadata["distortion"],
    )
    write_residual_csv(output / "residuals.csv", observations, best, diagnostics)
    validate_report(report)
    write_json(output / "calibration_report.json", report)
    write_json(output / "pose_diagnostics.json", {"schema_version": "camera_tof_pose_diagnostics.v1", "poses": diagnostics})
    write_json(output / "source_hashes.json", {"schema_version": "camera_tof_source_hashes.v1", "files": metadata["source_hashes"]})
    return report
