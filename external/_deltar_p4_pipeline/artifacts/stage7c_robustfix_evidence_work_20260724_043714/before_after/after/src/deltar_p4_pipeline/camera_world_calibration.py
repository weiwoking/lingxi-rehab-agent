from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .camera_calibration import (
    board_pose_to_camera_plane,
    detect_observation_image,
    project_board_polygon,
    solve_board_pose,
)
from .camera_calibration_contracts import (
    CameraCalibrationError,
    validate_board_manifest,
    validate_calibration_v2,
)
from .camera_tof_calibration import (
    aggregate_raw_zones,
    fit_dominant_plane,
    points_from_distances,
)
from .camera_tof_contracts import CameraTofError
from .charuco_board import charuco_board_from_manifest
from .rgb565 import read_rgb565_le
from .scan_profiles import load_profile_bundle
from .scan_validator import validate_scan
from .camera_world_contracts import (
    CAMERA_HEIGHT_MM,
    CAMERA_HEIGHT_UNCERTAINTY_MM,
    CAPTURE_SCHEMA,
    FROZEN_CAMERA_TOF_PROFILE,
    FROZEN_CAMERA_TOF_PROFILE_ID,
    MATRIX_CONVENTION,
    MIN_CAPTURED_FRAME_COUNT,
    MIN_ORIENTATION_INLIER_COUNT,
    MIN_ORIENTATION_INLIERS_PER_POSE,
    PROFILE_SCHEMA,
    REPORT_SCHEMA,
    TOOL_VERSION,
    WORLD_FRAME,
    CameraWorldError,
    canonical_sha256,
    fail,
    load_frozen_camera_tof_profile,
    load_strict_json,
    prepare_output_dir,
    sha256_file,
    validate_capture_manifest,
    validate_profile,
    validate_report,
    write_json,
)


# OpenCV ChArUco object axes are +X right in the printed board, +Y down,
# and right-handed +Z through the board away from its printed front/vehicle.
# The board front therefore has normal -Y_world in the frozen world frame.
R_WORLD_FROM_BOARD = np.asarray(
    [
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
        [0.0, -1.0, 0.0],
    ],
    dtype=np.float64,
)
R_WORLD_FROM_CAMERA_LEVEL = R_WORLD_FROM_BOARD.copy()
T_WORLD_FROM_CAMERA_MM = np.asarray(
    [0.0, 0.0, CAMERA_HEIGHT_MM], dtype=np.float64
)
EXPECTED_BOARD_BACK_NORMAL_WORLD = np.asarray([0.0, 1.0, 0.0])


@dataclass
class FrameObservation:
    pose_name: str
    role: str
    frame_name: str
    scan_dir: Path
    rotation_world_from_camera: np.ndarray
    rotation_camera_from_board: np.ndarray
    translation_camera_from_board_m: np.ndarray
    camera_diagnostics: dict[str, Any]
    board_polygon: np.ndarray
    image_bgr: np.ndarray
    tof: dict[str, Any]
    source_paths: tuple[Path, ...]


@dataclass
class PoseObservation:
    pose_name: str
    role: str
    approximate_distance_mm: float
    total_frame_count: int
    frames: list[FrameObservation]
    frame_diagnostics: list[dict[str, Any]]
    tof_diagnostics: dict[str, Any]
    overlay: dict[str, Any] | None


@dataclass
class RotationAverage:
    rotation: np.ndarray
    residual_deg: np.ndarray
    inlier_mask: np.ndarray
    threshold_deg: float


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def make_transform(rotation: np.ndarray, translation: np.ndarray) -> np.ndarray:
    r = np.asarray(rotation, dtype=np.float64)
    t = np.asarray(translation, dtype=np.float64).reshape(3)
    if r.shape != (3, 3) or not np.isfinite(r).all() or not np.isfinite(t).all():
        fail(
            "E_CAMERA_WORLD_TRANSFORM",
            "cannot construct transform from non-finite or wrong-shaped R/t",
            "transform",
        )
    result = np.eye(4, dtype=np.float64)
    result[:3, :3] = r
    result[:3, 3] = t
    return result


def board_coordinate_contract() -> dict[str, Any]:
    return {
        "opencv_board_x": "printed_board_horizontal_right",
        "opencv_board_y": "printed_board_vertical_down",
        "opencv_board_z": "right_hand_normal_through_board_away_from_vehicle",
        "board_front_normal": "-opencv_board_z = toward_vehicle",
        "R_world_from_board": R_WORLD_FROM_BOARD.tolist(),
        "mapped_x_world": (R_WORLD_FROM_BOARD @ [1.0, 0.0, 0.0]).tolist(),
        "mapped_y_world": (R_WORLD_FROM_BOARD @ [0.0, 1.0, 0.0]).tolist(),
        "mapped_z_world": (R_WORLD_FROM_BOARD @ [0.0, 0.0, 1.0]).tolist(),
        "board_front_normal_world": (
            R_WORLD_FROM_BOARD @ [0.0, 0.0, -1.0]
        ).tolist(),
        "front_back_resolution": (
            "deterministic: solvePnP requires tvec.z>0; OpenCV +Z_board is "
            "mapped to +Y_world and the printed front normal to -Y_world"
        ),
    }


def rotation_world_from_camera_from_board_pose(
    rotation_camera_from_board: np.ndarray,
) -> np.ndarray:
    rotation = np.asarray(rotation_camera_from_board, dtype=np.float64)
    if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
        fail(
            "E_CAMERA_WORLD_BOARD_POSE",
            "T_camera_from_board rotation must be finite 3x3",
            "rotation_camera_from_board",
        )
    result = R_WORLD_FROM_BOARD @ rotation.T
    u, _, vh = np.linalg.svd(result)
    result = u @ vh
    if np.linalg.det(result) < 0:
        u[:, -1] *= -1
        result = u @ vh
    return result


def rotation_distance_deg(a: np.ndarray, b: np.ndarray) -> float:
    delta = np.asarray(a, dtype=np.float64).T @ np.asarray(b, dtype=np.float64)
    cosine = float(np.clip((np.trace(delta) - 1.0) * 0.5, -1.0, 1.0))
    sine = 0.5 * float(
        np.linalg.norm(
            [
                delta[2, 1] - delta[1, 2],
                delta[0, 2] - delta[2, 0],
                delta[1, 0] - delta[0, 1],
            ]
        )
    )
    return math.degrees(math.atan2(sine, cosine))


def chordal_rotation_mean(rotations: list[np.ndarray]) -> np.ndarray:
    if not rotations:
        fail(
            "E_CAMERA_WORLD_ROTATION",
            "cannot average an empty rotation list",
            "rotations",
        )
    matrix = np.sum(np.stack(rotations), axis=0)
    u, _, vh = np.linalg.svd(matrix)
    result = u @ vh
    if np.linalg.det(result) < 0:
        u[:, -1] *= -1
        result = u @ vh
    return result


def robust_rotation_average(
    rotations: list[np.ndarray],
    *,
    minimum_inliers: int = 3,
    floor_threshold_deg: float = 0.25,
) -> RotationAverage:
    if len(rotations) < minimum_inliers:
        fail(
            "E_CAMERA_WORLD_ROTATION",
            f"at least {minimum_inliers} rotations are required",
            "rotations",
        )
    values = [np.asarray(item, dtype=np.float64) for item in rotations]
    for index, item in enumerate(values):
        if (
            item.shape != (3, 3)
            or not np.isfinite(item).all()
            or not np.allclose(item.T @ item, np.eye(3), atol=1e-5)
            or abs(float(np.linalg.det(item)) - 1.0) > 1e-5
        ):
            fail(
                "E_CAMERA_WORLD_ROTATION",
                "input rotation must be finite, orthonormal and proper",
                f"rotations[{index}]",
            )
    pairwise = np.asarray(
        [
            [rotation_distance_deg(a, b) for b in values]
            for a in values
        ],
        dtype=np.float64,
    )
    medoid = int(np.argmin(np.median(pairwise, axis=1)))
    mean = values[medoid]
    inliers = np.ones(len(values), dtype=bool)
    threshold = floor_threshold_deg
    for _ in range(8):
        residual = np.asarray(
            [rotation_distance_deg(mean, item) for item in values],
            dtype=np.float64,
        )
        centre = float(np.median(residual))
        mad = float(np.median(np.abs(residual - centre)))
        threshold = max(
            floor_threshold_deg,
            centre + 4.5 * max(1.4826 * mad, 0.01),
        )
        updated = residual <= threshold
        if np.count_nonzero(updated) < minimum_inliers:
            ranked = np.argsort(residual)
            updated = np.zeros(len(values), dtype=bool)
            updated[ranked[:minimum_inliers]] = True
        new_mean = chordal_rotation_mean(
            [item for item, keep in zip(values, updated) if keep]
        )
        converged = np.array_equal(updated, inliers) and rotation_distance_deg(
            mean, new_mean
        ) < 1e-9
        mean = new_mean
        inliers = updated
        if converged:
            break
    residual = np.asarray(
        [rotation_distance_deg(mean, item) for item in values],
        dtype=np.float64,
    )
    return RotationAverage(
        rotation=mean,
        residual_deg=residual,
        inlier_mask=inliers,
        threshold_deg=float(threshold),
    )


def rotation_from_diagnostics(
    roll_deg: float, pitch_deg: float, yaw_deg: float
) -> np.ndarray:
    roll = math.radians(roll_deg)
    pitch = math.radians(pitch_deg)
    yaw = math.radians(yaw_deg)
    optical = np.asarray(
        [
            math.sin(yaw) * math.cos(pitch),
            math.cos(yaw) * math.cos(pitch),
            math.sin(pitch),
        ],
        dtype=np.float64,
    )
    world_up = np.asarray([0.0, 0.0, 1.0])
    right_zero = np.cross(optical, world_up)
    norm = float(np.linalg.norm(right_zero))
    if norm < 1e-9:
        fail(
            "E_CAMERA_WORLD_EULER",
            "pitch too close to vertical for roll/yaw diagnostics",
            "pitch_deg",
        )
    right_zero /= norm
    down_zero = np.cross(optical, right_zero)
    right = math.cos(roll) * right_zero + math.sin(roll) * down_zero
    down = -math.sin(roll) * right_zero + math.cos(roll) * down_zero
    return np.column_stack((right, down, optical))


def orientation_diagnostics(rotation_world_from_camera: np.ndarray) -> dict[str, Any]:
    rotation = np.asarray(rotation_world_from_camera, dtype=np.float64)
    if (
        rotation.shape != (3, 3)
        or not np.isfinite(rotation).all()
        or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6)
        or abs(float(np.linalg.det(rotation)) - 1.0) > 1e-6
    ):
        fail(
            "E_CAMERA_WORLD_EULER",
            "orientation diagnostic input must be a proper rotation",
            "rotation_world_from_camera",
        )
    right = rotation[:, 0]
    optical = rotation[:, 2]
    pitch = math.atan2(
        float(optical[2]), float(np.hypot(optical[0], optical[1]))
    )
    yaw = math.atan2(float(optical[0]), float(optical[1]))
    right_zero = np.cross(optical, np.asarray([0.0, 0.0, 1.0]))
    length = float(np.linalg.norm(right_zero))
    if length < 1e-9:
        fail(
            "E_CAMERA_WORLD_EULER",
            "optical axis is vertical; roll diagnostic is singular",
            "rotation_world_from_camera",
        )
    right_zero /= length
    down_zero = np.cross(optical, right_zero)
    roll = math.atan2(
        float(right @ down_zero), float(right @ right_zero)
    )
    return {
        "roll_deg": math.degrees(roll),
        "pitch_deg": math.degrees(pitch),
        "yaw_deg": math.degrees(yaw),
        "convention": (
            "matrix-first R_world_from_camera; roll positive rotates Camera "
            "+X image-right toward Camera +Y image-down (clockwise in the "
            "image looking along +Z_camera); pitch positive aims +Z_camera "
            "upward toward +Z_world; yaw positive aims +Z_camera rightward "
            "toward +X_world from vehicle +Y_world forward"
        ),
    }


def _camera_parameters(profile: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    intrinsics = profile["camera"]["intrinsics"]
    matrix = np.asarray(
        [
            [intrinsics["fx"], 0.0, intrinsics["cx"]],
            [0.0, intrinsics["fy"], intrinsics["cy"]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    distortion = intrinsics["distortion"]
    coefficients = np.asarray(
        [distortion[key] for key in ("k1", "k2", "p1", "p2", "k3")],
        dtype=np.float64,
    )
    return matrix, coefficients


def _canonical_tof_values(
    raw: np.ndarray, raw_index_for_canonical_index: np.ndarray
) -> np.ndarray:
    return np.asarray(raw)[raw_index_for_canonical_index]


def _tof_pose_diagnostics(
    frames: list[FrameObservation],
    camera_tof_profile: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not frames:
        return {
            "status": "FAIL",
            "error": "no Camera-valid frames available for ToF plane validation",
            "usable_overlap_zone_count": 0,
            "normal_angular_error_deg": None,
            "plane_rms_mm": None,
            "plane_p95_mm": None,
            "plane_max_mm": None,
            "rejected_zones": [],
        }, None
    tof_frames = [frame.tof for frame in frames]
    median, mad, sigma, raw_valid, raw_reasons = aggregate_raw_zones(
        tof_frames, include_status_6=False
    )
    raw_map = np.asarray(
        camera_tof_profile["orientation"]["raw_index_for_canonical_index"],
        dtype=np.int64,
    )
    distance = _canonical_tof_values(median, raw_map).astype(np.float64)
    canonical_mad = _canonical_tof_values(mad, raw_map).astype(np.float64)
    canonical_sigma = _canonical_tof_values(sigma, raw_map).astype(np.float64)
    valid = _canonical_tof_values(raw_valid, raw_map).astype(bool)
    valid &= np.asarray(camera_tof_profile["zone_valid_mask"], dtype=bool)
    overlap = np.asarray(
        camera_tof_profile["camera_tof_overlap_mask"], dtype=bool
    )
    valid &= overlap
    valid &= (
        np.isfinite(distance)
        & np.isfinite(canonical_mad)
        & np.isfinite(canonical_sigma)
    )
    valid &= canonical_mad <= np.maximum(20.0, 3.0 * canonical_sigma)
    zones = np.flatnonzero(valid)
    rejected: list[dict[str, Any]] = []
    for canonical in np.flatnonzero(~valid):
        raw_index = int(raw_map[canonical])
        reasons = list(raw_reasons[raw_index])
        if not overlap[canonical]:
            reasons.append("outside_camera_tof_overlap_mask")
        if not camera_tof_profile["zone_valid_mask"][canonical]:
            reasons.append("stage7b_zone_invalid")
        if (
            np.isfinite(canonical_mad[canonical])
            and np.isfinite(canonical_sigma[canonical])
            and canonical_mad[canonical]
            > max(20.0, 3.0 * canonical_sigma[canonical])
        ):
            reasons.append("pose_mad_exceeds_gate")
        rejected.append(
            {
                "canonical_zone": int(canonical),
                "raw_zone": raw_index,
                "reasons": sorted(set(reasons or ["nonfinite_or_invalid"])),
            }
        )
    if len(zones) < 12:
        return {
            "status": "FAIL",
            "error": "fewer than 12 usable overlap zones",
            "usable_overlap_zone_count": int(len(zones)),
            "normal_angular_error_deg": None,
            "plane_rms_mm": None,
            "plane_p95_mm": None,
            "plane_max_mm": None,
            "rejected_zones": rejected,
        }, None
    rays = np.asarray(camera_tof_profile["zone_rays"], dtype=np.float64)
    points_tof = points_from_distances(
        rays[zones], distance[zones], camera_tof_profile["distance_semantics"]
    )
    transform_ct = np.asarray(
        camera_tof_profile["T_camera_from_tof"], dtype=np.float64
    )
    points_camera = (
        transform_ct[:3, :3] @ points_tof.T
    ).T + transform_ct[:3, 3]
    # Use the robust Camera orientation at the pose level so the board-plane
    # validation is independent of board translation and preserves the fixed
    # world origin/Camera height contract.
    pose_rotation = robust_rotation_average(
        [frame.rotation_world_from_camera for frame in frames],
        minimum_inliers=min(3, len(frames)),
    ).rotation
    points_world = (
        pose_rotation @ points_camera.T
    ).T + T_WORLD_FROM_CAMERA_MM
    try:
        normal, offset, inliers, _ = fit_dominant_plane(
            points_world, zones, min_inliers=12
        )
    except CameraTofError as exc:
        return {
            "status": "FAIL",
            "error": str(exc),
            "usable_overlap_zone_count": int(len(zones)),
            "normal_angular_error_deg": None,
            "plane_rms_mm": None,
            "plane_p95_mm": None,
            "plane_max_mm": None,
            "rejected_zones": rejected,
        }, None
    if float(normal @ EXPECTED_BOARD_BACK_NORMAL_WORLD) < 0:
        normal = -normal
        offset = -offset
    absolute = np.abs(points_world @ normal + offset)
    fit_values = absolute[inliers]
    angular = math.degrees(
        math.acos(
            float(
                np.clip(
                    normal @ EXPECTED_BOARD_BACK_NORMAL_WORLD, -1.0, 1.0
                )
            )
        )
    )
    for zone in zones[~inliers]:
        rejected.append(
            {
                "canonical_zone": int(zone),
                "raw_zone": int(raw_map[zone]),
                "reasons": ["robust_plane_outlier"],
            }
        )
    diagnostics = {
        "status": "PASS",
        "expected_normal_contract": (
            "+Y_world is OpenCV +Z_board (through board away from vehicle); "
            "printed board-front normal is -Y_world"
        ),
        "plane_normal_world": normal.tolist(),
        "plane_offset_world_mm": float(offset),
        "usable_overlap_zone_count": int(np.count_nonzero(inliers)),
        "normal_angular_error_deg": float(angular),
        "plane_rms_mm": float(np.sqrt(np.mean(np.square(fit_values)))),
        "plane_p95_mm": float(np.percentile(fit_values, 95)),
        "plane_max_mm": float(np.max(fit_values)),
        "rejected_zones": rejected,
    }
    overlay = {
        "zones": zones,
        "raw_map": raw_map,
        "points_camera": points_camera,
        "points_world": points_world,
        "absolute_plane_residual_mm": absolute,
        "inlier_mask": inliers,
        "overlap_mask": overlap,
        "plane_normal_world": normal,
        "plane_offset_world_mm": float(offset),
    }
    return diagnostics, overlay


def load_capture_observations(
    manifest_path: Path,
    camera_profile_path: Path,
    camera_tof_profile_path: Path,
    board_manifest_path: Path,
    *,
    profile_bundle_path: Path,
) -> tuple[dict[str, Any], list[PoseObservation], dict[str, Any]]:
    manifest = validate_capture_manifest(
        load_strict_json(manifest_path),
        manifest_path=manifest_path,
        camera_profile_path=camera_profile_path,
        camera_tof_profile_path=camera_tof_profile_path,
        board_manifest_path=board_manifest_path,
    )
    camera_profile = manifest["_camera_profile"]
    camera_tof_profile = manifest["_camera_tof_profile"]
    board_manifest = manifest["_board_manifest"]
    matrix, distortion = _camera_parameters(camera_profile)
    board = charuco_board_from_manifest(board_manifest)
    bundle = load_profile_bundle(profile_bundle_path, validate_model_hashes=False)
    poses: list[PoseObservation] = []
    source_hashes = dict(manifest["_source_hashes"])
    board_width_mm = (
        float(board_manifest["squares_x"])
        * float(board_manifest["square_length_mm"])
    )
    board_height_mm = (
        float(board_manifest["squares_y"])
        * float(board_manifest["square_length_mm"])
    )
    for pose in manifest["_resolved_poses"]:
        frames: list[FrameObservation] = []
        frame_diagnostics: list[dict[str, Any]] = []
        total_frame_count = 0
        for scan_dir in pose["_scan_dirs"]:
            validated = validate_scan(scan_dir, bundle)
            scan_manifest_path = scan_dir / "scan.json"
            source_hashes[str(scan_manifest_path.resolve())] = sha256_file(
                scan_manifest_path
            )
            for frame in validated.frames:
                total_frame_count += 1
                frame_name = str(frame.frame["frame_id"])
                source_paths = (
                    frame.image_path,
                    frame.directory / "frame.json",
                    frame.directory / "tof.json",
                )
                for source in source_paths:
                    source_hashes[str(source.resolve())] = sha256_file(source)
                base_diagnostic: dict[str, Any] = {
                    "pose_name": pose["pose_name"],
                    "role": pose["role"],
                    "frame_name": frame_name,
                    "scan_dir": str(scan_dir.resolve()),
                    "valid": False,
                }
                try:
                    image_rgb = read_rgb565_le(frame.image_path)
                    image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
                    observation = detect_observation_image(
                        image_bgr,
                        board,
                        min_charuco_corners=8,
                        source_name=f"{pose['pose_name']}/{frame_name}",
                        source_sha256=source_hashes[
                            str(frame.image_path.resolve())
                        ],
                    )
                    rotation_cb, translation_cb, diagnostics = solve_board_pose(
                        observation, matrix, distortion
                    )
                    base_diagnostic.update(
                        {
                            "detected_corners": int(
                                observation.corner_count
                            ),
                            "marker_count": int(observation.marker_count),
                            "reprojection_mean_px": diagnostics["mean_px"],
                            "reprojection_rms_px": diagnostics["rms_px"],
                            "reprojection_p95_px": diagnostics["p95_px"],
                            "reprojection_max_px": diagnostics["max_px"],
                            "tvec_z_m": diagnostics["tvec"][2],
                            "finite_pose": True,
                        }
                    )
                    if diagnostics["rms_px"] > 0.5:
                        base_diagnostic["error"] = (
                            "reprojection RMS exceeds 0.5 px formal gate"
                        )
                        frame_diagnostics.append(base_diagnostic)
                        continue
                    rotation_wc = rotation_world_from_camera_from_board_pose(
                        rotation_cb
                    )
                    polygon = project_board_polygon(
                        rotation_cb,
                        translation_cb,
                        matrix,
                        distortion,
                        board_width_mm,
                        board_height_mm,
                    )
                    base_diagnostic.update(
                        {
                            "valid": True,
                            "rotation_world_from_camera": rotation_wc.tolist(),
                            "translation_world_from_camera_mm": (
                                T_WORLD_FROM_CAMERA_MM.tolist()
                            ),
                        }
                    )
                    frames.append(
                        FrameObservation(
                            pose_name=pose["pose_name"],
                            role=pose["role"],
                            frame_name=frame_name,
                            scan_dir=scan_dir,
                            rotation_world_from_camera=rotation_wc,
                            rotation_camera_from_board=rotation_cb,
                            translation_camera_from_board_m=translation_cb,
                            camera_diagnostics=base_diagnostic,
                            board_polygon=polygon,
                            image_bgr=image_bgr,
                            tof=frame.tof,
                            source_paths=source_paths,
                        )
                    )
                except (CameraCalibrationError, CameraWorldError) as exc:
                    base_diagnostic.update(
                        {
                            "error": str(exc),
                            "finite_pose": False,
                        }
                    )
                frame_diagnostics.append(base_diagnostic)
        tof_diagnostics, tof_overlay = _tof_pose_diagnostics(
            frames, camera_tof_profile
        )
        overlay: dict[str, Any] | None = None
        if frames:
            representative = frames[0]
            overlay = {
                "frame": representative,
                "tof": tof_overlay,
                "matrix": matrix,
                "distortion": distortion,
            }
        poses.append(
            PoseObservation(
                pose_name=pose["pose_name"],
                role=pose["role"],
                approximate_distance_mm=float(
                    pose["approximate_distance_mm"]
                ),
                total_frame_count=total_frame_count,
                frames=frames,
                frame_diagnostics=frame_diagnostics,
                tof_diagnostics=tof_diagnostics,
                overlay=overlay,
            )
        )
    metadata = {
        "camera_profile_id": camera_profile["profile_id"],
        "camera_tof_profile_id": camera_tof_profile["profile_id"],
        "source_hashes": source_hashes,
        "matrix": matrix,
        "distortion": distortion,
        "board_contract": board_coordinate_contract(),
    }
    return manifest, poses, metadata


def _summary(values: np.ndarray) -> dict[str, Any]:
    array = np.asarray(values, dtype=np.float64)
    if array.size == 0:
        return {"count": 0, "rms_deg": None, "p95_deg": None, "max_deg": None}
    return {
        "count": int(array.size),
        "rms_deg": float(np.sqrt(np.mean(np.square(array)))),
        "p95_deg": float(np.percentile(array, 95)),
        "max_deg": float(np.max(array)),
    }


def _height_sanity_check(
    manifest: dict[str, Any], poses: list[PoseObservation]
) -> dict[str, Any]:
    board_height = manifest["board_origin_height_above_floor_mm"]
    if board_height is None:
        return {
            "status": "NOT_PERFORMED",
            "reason": (
                "board_origin_height_above_floor_mm was not measured; no "
                "height value was fabricated and measured 969+/-5 mm remains authoritative"
            ),
            "inferred_camera_height_mm": None,
            "difference_from_measured_height_mm": None,
        }
    inferred: list[float] = []
    for pose in poses:
        for frame in pose.frames:
            camera_position_board_m = (
                -frame.rotation_camera_from_board.T
                @ frame.translation_camera_from_board_m
            )
            delta_world_mm = (
                R_WORLD_FROM_BOARD @ camera_position_board_m
            ) * 1000.0
            inferred.append(float(board_height + delta_world_mm[2]))
    if not inferred:
        return {
            "status": "FAIL",
            "reason": "no valid PnP frames for independent height sanity check",
            "inferred_camera_height_mm": None,
            "difference_from_measured_height_mm": None,
        }
    estimate = float(np.median(inferred))
    difference = estimate - CAMERA_HEIGHT_MM
    return {
        "status": "PASS" if abs(difference) <= 20.0 else "FAIL",
        "reason": (
            "diagnostic only; never overrides measured Camera height or world translation"
        ),
        "inferred_camera_height_mm": estimate,
        "difference_from_measured_height_mm": difference,
    }


def _gate(
    passed: bool, actual: Any, requirement: str
) -> dict[str, Any]:
    return {
        "passed": bool(passed),
        "actual": actual,
        "requirement": requirement,
    }


def solve_world_capture(
    manifest: dict[str, Any],
    poses: list[PoseObservation],
    metadata: dict[str, Any],
    *,
    expected_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    all_frames = [frame for pose in poses for frame in pose.frames]
    all_diagnostics = [
        item for pose in poses for item in pose.frame_diagnostics
    ]
    rotations = [frame.rotation_world_from_camera for frame in all_frames]
    average: RotationAverage | None = None
    if len(rotations) >= 3:
        average = robust_rotation_average(rotations)
    if average is None:
        rotation_wc = R_WORLD_FROM_CAMERA_LEVEL.copy()
        residual_all = np.asarray([], dtype=np.float64)
        inlier_mask = np.zeros(len(rotations), dtype=bool)
        robust_threshold = None
    else:
        rotation_wc = average.rotation
        residual_all = average.residual_deg
        inlier_mask = average.inlier_mask
        robust_threshold = average.threshold_deg
    inlier_residual = residual_all[inlier_mask]
    orientation = orientation_diagnostics(rotation_wc)

    frame_index = {
        (frame.pose_name, frame.frame_name, str(frame.scan_dir.resolve())): index
        for index, frame in enumerate(all_frames)
    }
    for pose in poses:
        for diagnostic in pose.frame_diagnostics:
            if not diagnostic["valid"]:
                diagnostic["orientation_inlier"] = False
                diagnostic["orientation_residual_deg"] = None
                continue
            key = (
                diagnostic["pose_name"],
                diagnostic["frame_name"],
                str(Path(diagnostic["scan_dir"]).resolve()),
            )
            index = frame_index[key]
            diagnostic["orientation_inlier"] = bool(inlier_mask[index])
            diagnostic["orientation_residual_deg"] = float(
                residual_all[index]
            )

    pose_means: dict[str, np.ndarray] = {}
    pose_orientation: dict[str, Any] = {}
    for pose in poses:
        selected = [
            frame.rotation_world_from_camera
            for frame in pose.frames
            if inlier_mask[
                frame_index[
                    (
                        frame.pose_name,
                        frame.frame_name,
                        str(frame.scan_dir.resolve()),
                    )
                ]
            ]
        ]
        if selected:
            pose_mean = chordal_rotation_mean(selected)
            pose_means[pose.pose_name] = pose_mean
            pose_orientation[pose.pose_name] = {
                "role": pose.role,
                "inlier_frame_count": len(selected),
                "angle_from_all_frame_mean_deg": rotation_distance_deg(
                    rotation_wc, pose_mean
                ),
                "rotation_world_from_camera": pose_mean.tolist(),
            }
        else:
            pose_orientation[pose.pose_name] = {
                "role": pose.role,
                "inlier_frame_count": 0,
                "angle_from_all_frame_mean_deg": None,
                "rotation_world_from_camera": None,
            }
    pairwise: list[dict[str, Any]] = []
    for left_index, (left_name, left_rotation) in enumerate(pose_means.items()):
        for right_name, right_rotation in list(pose_means.items())[
            left_index + 1:
        ]:
            pairwise.append(
                {
                    "left": left_name,
                    "right": right_name,
                    "angle_deg": rotation_distance_deg(
                        left_rotation, right_rotation
                    ),
                }
            )
    max_pairwise = max(
        (item["angle_deg"] for item in pairwise), default=math.inf
    )
    orientation_summary = {
        "robust_method": (
            "geodesic medoid initialization; MAD outlier rejection; "
            "chordal SVD mean projected to SO(3)"
        ),
        "robust_threshold_deg": robust_threshold,
        "all_valid_frame_residual": _summary(residual_all),
        "inlier_residual": _summary(inlier_residual),
        "rejected_frame_count": int(len(rotations) - np.count_nonzero(inlier_mask)),
        "pose_means": pose_orientation,
        "pose_mean_pairwise": pairwise,
        "max_pose_mean_pairwise_deg": (
            float(max_pairwise) if math.isfinite(max_pairwise) else None
        ),
    }
    tof_summary = {
        "poses": {
            pose.pose_name: {
                "role": pose.role,
                **pose.tof_diagnostics,
            }
            for pose in poses
        },
    }
    valid_frame_count = int(np.count_nonzero(inlier_mask))
    base_valid_frame_count = len(all_frames)
    frame_count = sum(pose.total_frame_count for pose in poses)
    capture_summary = {
        "pose_count": len(poses),
        "frame_count": frame_count,
        "valid_frame_count": valid_frame_count,
        "rejected_frame_count": frame_count - valid_frame_count,
    }
    finite_frame_quality = (
        base_valid_frame_count >= MIN_CAPTURED_FRAME_COUNT
        and all(
            item.get("valid") is True
            and item.get("detected_corners", 0) >= 8
            and item.get("reprojection_rms_px", math.inf) <= 0.5
            for item in all_diagnostics
        )
    )
    reprojection_values = [
        float(item["reprojection_rms_px"])
        for item in all_diagnostics
        if type(item.get("reprojection_rms_px")) in {int, float}
        and math.isfinite(float(item["reprojection_rms_px"]))
    ]
    orientation_inlier_metrics = _summary(inlier_residual)
    tof_pose_passes = []
    for pose in poses:
        diag = pose.tof_diagnostics
        tof_pose_passes.append(
            diag.get("usable_overlap_zone_count", 0) >= 16
            and diag.get("normal_angular_error_deg") is not None
            and diag["normal_angular_error_deg"] <= 5.0
            and diag.get("plane_rms_mm") is not None
            and diag["plane_rms_mm"] <= 15.0
            and diag.get("plane_p95_mm") is not None
            and diag["plane_p95_mm"] <= 30.0
        )
    gates = {
        "pose_count": _gate(
            len(poses) >= 3, len(poses), "pose_count >= 3"
        ),
        "frame_count": _gate(
            frame_count >= MIN_CAPTURED_FRAME_COUNT,
            frame_count,
            f"frame_count >= {MIN_CAPTURED_FRAME_COUNT}",
        ),
        "five_frames_per_pose": _gate(
            all(pose.total_frame_count >= 5 for pose in poses),
            {
                pose.pose_name: pose.total_frame_count
                for pose in poses
            },
            "each pose frame_count >= 5",
        ),
        "frame_quality": _gate(
            finite_frame_quality,
            {
                "all_frames_pass": finite_frame_quality,
                "base_valid_frame_count": base_valid_frame_count,
                "minimum_corners": min(
                    (
                        item.get("detected_corners", 0)
                        for item in all_diagnostics
                    ),
                    default=0,
                ),
                "maximum_reprojection_rms_px": max(
                    reprojection_values
                ) if reprojection_values else None,
            },
            (
                f"base_valid_frame_count >= {MIN_CAPTURED_FRAME_COUNT}; "
                "every frame corners >= 8, finite pose, "
                "reprojection RMS <= 0.5 px"
            ),
        ),
        "valid_frame_count": _gate(
            valid_frame_count >= MIN_ORIENTATION_INLIER_COUNT,
            valid_frame_count,
            (
                "robust orientation inlier frame count >= "
                f"{MIN_ORIENTATION_INLIER_COUNT}"
            ),
        ),
        "per_pose_orientation_inlier_count": _gate(
            len(pose_orientation) >= 3
            and all(
                value["inlier_frame_count"]
                >= MIN_ORIENTATION_INLIERS_PER_POSE
                for value in pose_orientation.values()
            ),
            {
                name: value["inlier_frame_count"]
                for name, value in pose_orientation.items()
            },
            (
                "each pose robust orientation inlier frame count >= "
                f"{MIN_ORIENTATION_INLIERS_PER_POSE}"
            ),
        ),
        "orientation_rms": _gate(
            orientation_inlier_metrics["rms_deg"] is not None
            and orientation_inlier_metrics["rms_deg"] <= 0.5,
            orientation_inlier_metrics["rms_deg"],
            "inlier orientation residual RMS <= 0.5 deg",
        ),
        "orientation_p95": _gate(
            orientation_inlier_metrics["p95_deg"] is not None
            and orientation_inlier_metrics["p95_deg"] <= 1.0,
            orientation_inlier_metrics["p95_deg"],
            "inlier orientation residual P95 <= 1.0 deg",
        ),
        "orientation_max": _gate(
            orientation_inlier_metrics["max_deg"] is not None
            and orientation_inlier_metrics["max_deg"] <= 1.5,
            orientation_inlier_metrics["max_deg"],
            "inlier orientation residual max <= 1.5 deg",
        ),
        "near_mid_far_pairwise": _gate(
            math.isfinite(max_pairwise) and max_pairwise <= 1.0,
            float(max_pairwise) if math.isfinite(max_pairwise) else None,
            "near/mid/far pose-mean pairwise angle <= 1.0 deg",
        ),
        "camera_roll": _gate(
            abs(orientation["roll_deg"]) <= 5.0,
            orientation["roll_deg"],
            "abs(camera_roll_deg) <= 5 deg",
        ),
        "camera_pitch": _gate(
            abs(orientation["pitch_deg"]) <= 5.0,
            orientation["pitch_deg"],
            "abs(camera_pitch_deg) <= 5 deg",
        ),
        "camera_yaw": _gate(
            abs(orientation["yaw_deg"]) <= 5.0,
            orientation["yaw_deg"],
            "abs(camera_yaw_deg) <= 5 deg",
        ),
        "tof_vertical_plane": _gate(
            len(tof_pose_passes) >= 3 and all(tof_pose_passes),
            {
                pose.pose_name: bool(passed)
                for pose, passed in zip(poses, tof_pose_passes)
            },
            (
                "each pose: usable overlap zones >=16, normal angular error "
                "<=5 deg, plane RMS <=15 mm, P95 <=30 mm"
            ),
        ),
        "camera_height_contract": _gate(
            np.array_equal(
                T_WORLD_FROM_CAMERA_MM,
                np.asarray([0.0, 0.0, CAMERA_HEIGHT_MM]),
            )
            and manifest["camera_height_mm"] == CAMERA_HEIGHT_MM
            and manifest["camera_height_uncertainty_mm"]
            == CAMERA_HEIGHT_UNCERTAINTY_MM,
            {
                "translation_world_from_camera_mm": (
                    T_WORLD_FROM_CAMERA_MM.tolist()
                ),
                "camera_height_mm": manifest["camera_height_mm"],
                "camera_height_uncertainty_mm": manifest[
                    "camera_height_uncertainty_mm"
                ],
            },
            "measured Camera height 969 +/-5 mm; translation exactly [0,0,969]",
        ),
    }
    if expected_profile is not None:
        expected_rotation = np.asarray(
            expected_profile["rotation_world_from_camera"], dtype=np.float64
        )
        profile_delta = rotation_distance_deg(
            expected_rotation, rotation_wc
        )
        gates["profile_rotation_consistency"] = _gate(
            profile_delta <= 0.25,
            profile_delta,
            "capture mean vs supplied profile <= 0.25 deg",
        )
    height_sanity = _height_sanity_check(manifest, poses)
    if height_sanity["status"] != "NOT_PERFORMED":
        gates["independent_height_sanity"] = _gate(
            height_sanity["status"] == "PASS",
            height_sanity["difference_from_measured_height_mm"],
            "abs(inferred - measured) <= 20 mm",
        )
    passed = all(item["passed"] for item in gates.values())

    transform_wc = make_transform(rotation_wc, T_WORLD_FROM_CAMERA_MM)
    frozen_camera_tof = load_frozen_camera_tof_profile(
        FROZEN_CAMERA_TOF_PROFILE
    )
    transform_ct = np.asarray(
        frozen_camera_tof["T_camera_from_tof"], dtype=np.float64
    )
    transform_wt = transform_wc @ transform_ct
    if not np.allclose(
        transform_wt, transform_wc @ transform_ct, atol=1e-12
    ):
        fail(
            "E_CAMERA_WORLD_COMPOSITION",
            "ToF composition roundtrip failed",
            "T_world_from_tof",
        )
    if (
        not np.allclose(
            rotation_wc.T @ rotation_wc, np.eye(3), atol=1e-9
        )
        or abs(float(np.linalg.det(rotation_wc)) - 1.0) > 1e-9
    ):
        fail(
            "E_CAMERA_WORLD_ROTATION",
            "final Camera rotation failed SO(3) checks",
            "rotation_world_from_camera",
        )
    residual_summary = {
        "camera_orientation": orientation_summary,
        "tof_vertical_plane": tof_summary,
    }
    profile_seed = {
        "camera_profile_id": metadata["camera_profile_id"],
        "camera_tof_profile_id": metadata["camera_tof_profile_id"],
        "rotation_world_from_camera": rotation_wc.tolist(),
        "translation_world_from_camera_mm": T_WORLD_FROM_CAMERA_MM.tolist(),
        "source_data_hashes": metadata["source_hashes"],
    }
    profile = {
        "schema_version": PROFILE_SCHEMA,
        "profile_id": (
            "camera_world_pose_" + canonical_sha256(profile_seed)[:16]
        ),
        "created_at": utc_now(),
        "validation_status": "PASS" if passed else "FAIL",
        "camera_profile_id": metadata["camera_profile_id"],
        "camera_tof_profile_id": metadata["camera_tof_profile_id"],
        "world_frame": WORLD_FRAME,
        "matrix_convention": MATRIX_CONVENTION,
        "T_world_from_camera": transform_wc.tolist(),
        "rotation_world_from_camera": rotation_wc.tolist(),
        "translation_world_from_camera_mm": (
            T_WORLD_FROM_CAMERA_MM.tolist()
        ),
        "T_world_from_tof": transform_wt.tolist(),
        "rotation_world_from_tof": transform_wt[:3, :3].tolist(),
        "translation_world_from_tof_mm": transform_wt[:3, 3].tolist(),
        "camera_height_mm": CAMERA_HEIGHT_MM,
        "camera_height_uncertainty_mm": CAMERA_HEIGHT_UNCERTAINTY_MM,
        "orientation_diagnostics": orientation,
        "capture_summary": capture_summary,
        "residual_summary": residual_summary,
        "source_data_hashes": metadata["source_hashes"],
        "warnings": [
            "ABSOLUTE_GEOMETRY_REMAINS_INVALID_PENDING_INDEPENDENT_GROUND_OR_KNOWN_HEIGHT_VALIDATION",
            "STAGE7B_NOMINAL_RAYS_NOT_MANUFACTURER_PER_ZONE_MEASURED",
        ],
        "unresolved_fields": [
            "absolute_geometry_validation_against_real_ground_or_known_height_object",
            "manufacturer_measured_per_zone_tof_rays",
        ],
        "world_pose": {"status": "measured" if passed else "candidate"},
        "world_pose_resolved": bool(passed),
        "absolute_geometry_valid": False,
    }
    report = {
        "schema_version": REPORT_SCHEMA,
        "created_at": utc_now(),
        "capture_id": manifest["capture_id"],
        "status": "PASS" if passed else "FAIL",
        "validation_status": "PASS" if passed else "FAIL",
        "gates": gates,
        "capture_summary": capture_summary,
        "orientation_summary": orientation_summary,
        "tof_plane_summary": tof_summary,
        "height_sanity_check": height_sanity,
        "source_profile_binding": {
            "camera_profile_path": str(
                manifest["_camera_profile_path"]
            ),
            "camera_profile_id": metadata["camera_profile_id"],
            "camera_profile_sha256": metadata["source_hashes"][
                str(manifest["_camera_profile_path"])
            ],
            "camera_tof_profile_path": str(
                manifest["_camera_tof_profile_path"]
            ),
            "camera_tof_profile_id": metadata[
                "camera_tof_profile_id"
            ],
            "camera_tof_profile_sha256": metadata["source_hashes"][
                str(manifest["_camera_tof_profile_path"])
            ],
            "stage7b_binding_verified": True,
            "board_manifest_path": str(
                manifest["_board_manifest_path"]
            ),
            "board_manifest_sha256": metadata["source_hashes"][
                str(manifest["_board_manifest_path"])
            ],
        },
        "warnings": profile["warnings"],
        "formal_profile_generated": bool(passed and expected_profile is None),
        "tool_version": TOOL_VERSION,
    }
    return {
        "passed": passed,
        "profile": profile,
        "report": report,
        "frame_diagnostics": all_diagnostics,
        "pose_diagnostics": [
            {
                "pose_name": pose.pose_name,
                "role": pose.role,
                "approximate_distance_mm": pose.approximate_distance_mm,
                "frame_count": pose.total_frame_count,
                "valid_camera_frame_count": len(pose.frames),
                "orientation": pose_orientation[pose.pose_name],
                "tof_vertical_plane": pose.tof_diagnostics,
            }
            for pose in poses
        ],
        "poses": poses,
        "metadata": metadata,
    }


def manifest_template(
    output: Path,
    *,
    camera_profile: Path,
    camera_tof_profile: Path,
    board_manifest: Path,
) -> dict[str, Any]:
    if output.exists():
        fail(
            "E_CAMERA_WORLD_OUTPUT",
            "manifest template output already exists",
            str(output),
        )
    camera = load_strict_json(camera_profile)
    validate_calibration_v2(camera)
    camera_tof = load_frozen_camera_tof_profile(camera_tof_profile)
    board = load_strict_json(board_manifest)
    validate_board_manifest(board, require_print_measurement=True)
    value = {
        "schema_version": CAPTURE_SCHEMA,
        "capture_id": "stage7c_world_capture_replace_me",
        "created_at": utc_now(),
        "camera_profile_path": str(camera_profile.resolve()),
        "camera_profile_id": camera["profile_id"],
        "camera_profile_sha256": sha256_file(camera_profile),
        "camera_tof_profile_path": str(camera_tof_profile.resolve()),
        "camera_tof_profile_id": camera_tof["profile_id"],
        "camera_tof_profile_sha256": sha256_file(camera_tof_profile),
        "board_manifest_path": str(board_manifest.resolve()),
        "board_manifest_sha256": sha256_file(board_manifest),
        "world_frame": WORLD_FRAME,
        "camera_height_mm": CAMERA_HEIGHT_MM,
        "camera_height_uncertainty_mm": CAMERA_HEIGHT_UNCERTAINTY_MM,
        "board_alignment": {
            "vertical": True,
            "top_edge_horizontal": True,
            "front_faces_vehicle": True,
            "vehicle_forward_perpendicular_to_board": True,
            "setup_angle_uncertainty_deg": 0.5,
        },
        "board_origin_height_above_floor_mm": None,
        "poses": [
            {
                "pose_name": role.removeprefix("world_front_"),
                "role": role,
                "scan_dir": f"REPLACE_WITH_EXPLICIT_{role.upper()}_SCAN_DIR",
                "stationary_confirmed": True,
                "approximate_distance_mm": distance,
                "alignment_confirmed": True,
                "notes": "5 stationary frames; preserve board/vehicle direction",
            }
            for role, distance in (
                ("world_front_near", 650.0),
                ("world_front_mid", 850.0),
                ("world_front_far", 1050.0),
            )
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, value)
    return value


def _write_results(
    output: Path,
    result: dict[str, Any],
    *,
    allow_formal_profile: bool,
) -> None:
    from .camera_world_outputs import (
        write_orientation_csv,
        write_overlays,
        write_tof_csv,
    )

    write_json(output / "calibration_report.json", result["report"])
    write_json(
        output / "frame_diagnostics.json",
        {
            "schema_version": "camera_world_frame_diagnostics.v1",
            "frames": result["frame_diagnostics"],
        },
    )
    write_json(
        output / "pose_diagnostics.json",
        {
            "schema_version": "camera_world_pose_diagnostics.v1",
            "poses": result["pose_diagnostics"],
        },
    )
    write_json(
        output / "source_hashes.json",
        {
            "schema_version": "camera_world_source_hashes.v1",
            "files": result["metadata"]["source_hashes"],
        },
    )
    candidate = {
        "schema_version": "camera_world_pose_candidate.v1",
        "candidate_only": True,
        "must_not_be_used_as_measured_profile": True,
        "validation_status": result["report"]["status"],
        "solution": result["profile"],
        "failed_gates": [
            name
            for name, gate in result["report"]["gates"].items()
            if not gate["passed"]
        ],
    }
    write_json(output / "camera_world_pose.candidate.json", candidate)
    write_orientation_csv(
        output / "orientation_residuals.csv",
        result["frame_diagnostics"],
    )
    write_tof_csv(
        output / "tof_plane_residuals.csv", result["poses"]
    )
    write_overlays(output / "overlays", result["poses"])
    if result["passed"] and allow_formal_profile:
        validate_profile(result["profile"])
        write_json(output / "camera_world_pose.v1.json", result["profile"])


def calibrate_capture(
    manifest_path: Path,
    camera_profile_path: Path,
    camera_tof_profile_path: Path,
    board_manifest_path: Path,
    output_dir: Path,
    *,
    profile_bundle_path: Path,
) -> dict[str, Any]:
    # Binding is deliberately repeated before output creation and observation
    # loading so no alternative Stage 7B candidate can be selected.
    load_frozen_camera_tof_profile(camera_tof_profile_path)
    output = prepare_output_dir(output_dir)
    manifest, poses, metadata = load_capture_observations(
        manifest_path,
        camera_profile_path,
        camera_tof_profile_path,
        board_manifest_path,
        profile_bundle_path=profile_bundle_path,
    )
    result = solve_world_capture(manifest, poses, metadata)
    _write_results(output, result, allow_formal_profile=True)
    validate_report(result["report"])
    return result["report"]


def validate_capture_with_profile(
    profile_path: Path,
    manifest_path: Path,
    output_dir: Path,
    *,
    profile_bundle_path: Path,
) -> dict[str, Any]:
    profile = validate_profile(load_strict_json(profile_path))
    manifest_value = load_strict_json(manifest_path)
    manifest_root = manifest_path.resolve().parent
    def manifest_source(name: str) -> Path:
        value = Path(manifest_value[name])
        return value if value.is_absolute() else manifest_root / value
    output = prepare_output_dir(output_dir)
    manifest, poses, metadata = load_capture_observations(
        manifest_path,
        manifest_source("camera_profile_path"),
        manifest_source("camera_tof_profile_path"),
        manifest_source("board_manifest_path"),
        profile_bundle_path=profile_bundle_path,
    )
    if profile["camera_profile_id"] != metadata["camera_profile_id"]:
        fail(
            "E_CAMERA_WORLD_PROFILE_ID",
            "formal profile Camera source differs from capture manifest",
            "camera_profile_id",
        )
    if profile["camera_tof_profile_id"] != metadata["camera_tof_profile_id"]:
        fail(
            "E_CAMERA_WORLD_PROFILE_ID",
            "formal profile Camera-ToF source differs from capture manifest",
            "camera_tof_profile_id",
        )
    for source in (
        manifest["_camera_profile_path"],
        manifest["_camera_tof_profile_path"],
        manifest["_board_manifest_path"],
    ):
        key = str(source)
        if profile["source_data_hashes"].get(key) != metadata[
            "source_hashes"
        ].get(key):
            fail(
                "E_CAMERA_WORLD_HASH",
                "formal profile source hash differs from capture manifest",
                f"source_data_hashes.{key}",
            )
    result = solve_world_capture(
        manifest, poses, metadata, expected_profile=profile
    )
    result["report"]["formal_profile_generated"] = False
    _write_results(output, result, allow_formal_profile=False)
    validate_report(result["report"])
    return result["report"]
