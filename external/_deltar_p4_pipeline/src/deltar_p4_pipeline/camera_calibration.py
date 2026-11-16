from __future__ import annotations

import math
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np

from .camera_calibration_contracts import (
    CALIBRATION_V2_SCHEMA,
    INTRINSICS_REPORT_SCHEMA,
    CameraCalibrationError,
    canonical_sha256,
    fail,
    load_strict_json,
    sha256_file,
    validate_board_manifest,
    validate_calibration_v2,
    validate_orientation_manifest,
    write_json,
)
from .charuco_board import charuco_board_from_manifest


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
TOOL_VERSION = "stage7a-1.0"


@dataclass(frozen=True)
class CharucoObservation:
    filename: str
    sha256: str
    width: int
    height: int
    image_points: np.ndarray
    object_points: np.ndarray
    marker_count: int
    corner_count: int
    coverage: float
    sharpness: float
    centroid: tuple[float, float]
    board_angle_deg: float


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _detect_charuco(image: np.ndarray, board: Any) -> tuple[np.ndarray | None, np.ndarray | None, int]:
    if hasattr(cv2.aruco, "CharucoDetector"):
        detector = cv2.aruco.CharucoDetector(board)
        corners, ids, marker_corners, marker_ids = detector.detectBoard(image)
        return corners, ids, 0 if marker_ids is None else len(marker_ids)
    marker_corners, marker_ids, _ = cv2.aruco.detectMarkers(image, board.getDictionary())
    if marker_ids is None:
        return None, None, 0
    _, corners, ids = cv2.aruco.interpolateCornersCharuco(marker_corners, marker_ids, image, board)
    return corners, ids, len(marker_ids)


def detect_observation(image_path: Path, board: Any, *, min_charuco_corners: int) -> CharucoObservation:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        fail("E_CALIBRATION_IMAGE", "OpenCV cannot decode image", str(image_path))
    height, width = image.shape[:2]
    if (width, height) != (640, 360):
        fail("E_CALIBRATION_RESOLUTION", "every image must be exactly 640x360", str(image_path), {"width": width, "height": height})
    corners, ids, marker_count = _detect_charuco(image, board)
    if corners is None or ids is None or len(corners) < min_charuco_corners:
        fail("E_CALIBRATION_CORNERS", f"fewer than {min_charuco_corners} ChArUco corners", str(image_path), {
            "detected_corners": 0 if corners is None else len(corners), "detected_markers": marker_count,
        })
    points = np.asarray(corners, dtype=np.float32).reshape(-1, 2)
    indices = np.asarray(ids, dtype=np.int32).reshape(-1)
    chessboard = np.asarray(board.getChessboardCorners(), dtype=np.float32).reshape(-1, 3)
    if np.any(indices < 0) or np.any(indices >= len(chessboard)):
        fail("E_CALIBRATION_CORNERS", "detector returned an invalid ChArUco id", str(image_path))
    x0, y0 = np.min(points, axis=0)
    x1, y1 = np.max(points, axis=0)
    coverage = float(max(0.0, x1 - x0) * max(0.0, y1 - y0) / (width * height))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    rect = cv2.minAreaRect(points.astype(np.float32))
    angle = float(rect[2])
    return CharucoObservation(
        filename=image_path.name, sha256=sha256_file(image_path), width=width, height=height,
        image_points=points, object_points=chessboard[indices], marker_count=marker_count,
        corner_count=len(points), coverage=coverage, sharpness=sharpness,
        centroid=(float(np.mean(points[:, 0])), float(np.mean(points[:, 1]))), board_angle_deg=angle,
    )


def detect_observation_image(
    image: np.ndarray,
    board: Any,
    *,
    min_charuco_corners: int,
    source_name: str = "image.rgb565",
    source_sha256: str = "",
) -> CharucoObservation:
    """Detect a Stage 7A-compatible observation in an already decoded image."""
    if not isinstance(image, np.ndarray) or image.shape[:2] != (360, 640) or image.ndim != 3:
        fail("E_CALIBRATION_RESOLUTION", "decoded image must be 640x360 with three channels", source_name)
    corners, ids, marker_count = _detect_charuco(image, board)
    if corners is None or ids is None or len(corners) < min_charuco_corners:
        fail("E_CALIBRATION_CORNERS", f"fewer than {min_charuco_corners} ChArUco corners", source_name, {
            "detected_corners": 0 if corners is None else len(corners), "detected_markers": marker_count,
        })
    points = np.asarray(corners, dtype=np.float32).reshape(-1, 2)
    indices = np.asarray(ids, dtype=np.int32).reshape(-1)
    chessboard = np.asarray(board.getChessboardCorners(), dtype=np.float32).reshape(-1, 3)
    if np.any(indices < 0) or np.any(indices >= len(chessboard)):
        fail("E_CALIBRATION_CORNERS", "detector returned an invalid ChArUco id", source_name)
    x0, y0 = np.min(points, axis=0)
    x1, y1 = np.max(points, axis=0)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return CharucoObservation(
        filename=source_name, sha256=source_sha256, width=640, height=360,
        image_points=points, object_points=chessboard[indices],
        marker_count=marker_count, corner_count=len(points),
        coverage=float(max(0.0, x1 - x0) * max(0.0, y1 - y0) / (640 * 360)),
        sharpness=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        centroid=(float(np.mean(points[:, 0])), float(np.mean(points[:, 1]))),
        board_angle_deg=float(cv2.minAreaRect(points.astype(np.float32))[2]),
    )


def solve_board_pose(
    observation: CharucoObservation, matrix: np.ndarray, distortion: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Solve ``T_camera_from_board`` and return reprojection diagnostics."""
    ok, rvec, tvec = cv2.solvePnP(
        observation.object_points.astype(np.float32), observation.image_points.astype(np.float32),
        np.asarray(matrix, dtype=np.float64), np.asarray(distortion, dtype=np.float64),
        flags=cv2.SOLVEPNP_ITERATIVE,
    )
    if not ok or not np.isfinite(rvec).all() or not np.isfinite(tvec).all():
        fail("E_CALIBRATION_PNP", "solvePnP failed or returned non-finite values", observation.filename)
    rotation, _ = cv2.Rodrigues(rvec)
    translation = np.asarray(tvec, dtype=np.float64).reshape(3)
    if translation[2] <= 0:
        fail("E_CALIBRATION_PNP", "board pose must be in front of Camera", observation.filename, {"tvec_z": float(translation[2])})
    projected, _ = cv2.projectPoints(
        observation.object_points, rvec, translation, matrix, distortion,
    )
    residual = np.linalg.norm(projected.reshape(-1, 2) - observation.image_points, axis=1)
    diagnostics = {
        "corner_count": observation.corner_count,
        "mean_px": float(np.mean(residual)),
        "rms_px": float(np.sqrt(np.mean(np.square(residual)))),
        "p95_px": float(np.percentile(residual, 95)),
        "max_px": float(np.max(residual)),
        "rvec": np.asarray(rvec).reshape(3).tolist(),
        "tvec": translation.tolist(),
    }
    return rotation, translation, diagnostics


def board_pose_m_to_camera_plane_mm(
    rotation_camera_from_board: np.ndarray,
    translation_camera_from_board_m: np.ndarray,
) -> tuple[np.ndarray, float]:
    """Convert an OpenCV board pose in metres to a Camera plane in millimetres.

    ``rotation_camera_from_board`` is unitless and
    ``translation_camera_from_board_m`` is in metres because the ChArUco
    object points passed to ``solvePnP`` are in metres.  The returned normal is
    a unit vector and the returned offset is in millimetres.
    """
    rotation = np.asarray(rotation_camera_from_board, dtype=np.float64)
    translation_m = np.asarray(
        translation_camera_from_board_m, dtype=np.float64
    ).reshape(3)
    if (
        rotation.shape != (3, 3)
        or not np.isfinite(rotation).all()
        or not np.isfinite(translation_m).all()
    ):
        fail("E_CALIBRATION_PLANE", "board pose must contain finite 3x3 R and length-3 t", "board_pose")
    normal = rotation[:, 2].copy()
    length = float(np.linalg.norm(normal))
    if length <= 0:
        fail("E_CALIBRATION_PLANE", "board normal is degenerate", "board_pose")
    normal /= length
    translation_mm = translation_m * 1000.0
    offset_camera_mm = -float(normal @ translation_mm)
    # A plane has a global sign ambiguity. Normalize it so its normal points
    # generally toward +Camera-Z, which keeps reports deterministic.
    if normal[2] < 0:
        normal = -normal
        offset_camera_mm = -offset_camera_mm
    return normal, offset_camera_mm


def board_pose_to_camera_plane(
    rotation_camera_from_board: np.ndarray,
    translation_camera_from_board_m: np.ndarray,
) -> tuple[np.ndarray, float]:
    """Compatibility wrapper for the explicit metre-to-millimetre contract."""
    return board_pose_m_to_camera_plane_mm(
        rotation_camera_from_board, translation_camera_from_board_m
    )


def project_board_polygon(
    rotation_camera_from_board: np.ndarray,
    translation_camera_from_board_m: np.ndarray,
    matrix: np.ndarray,
    distortion: np.ndarray,
    width_mm: float,
    height_mm: float,
) -> np.ndarray:
    """Project a millimetre-sized board using an OpenCV pose in metres."""
    if (
        not math.isfinite(width_mm)
        or not math.isfinite(height_mm)
        or width_mm <= 0
        or height_mm <= 0
    ):
        fail(
            "E_CALIBRATION_PROJECTION",
            "board width/height must be positive finite millimetres",
            "board_dimensions",
        )
    corners = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [width_mm / 1000.0, 0.0, 0.0],
            [width_mm / 1000.0, height_mm / 1000.0, 0.0],
            [0.0, height_mm / 1000.0, 0.0],
        ],
        dtype=np.float64,
    )
    rvec, _ = cv2.Rodrigues(np.asarray(rotation_camera_from_board, dtype=np.float64))
    projected, _ = cv2.projectPoints(
        corners, rvec, np.asarray(translation_camera_from_board_m, dtype=np.float64),
        np.asarray(matrix, dtype=np.float64), np.asarray(distortion, dtype=np.float64),
    )
    polygon = projected.reshape(4, 2)
    if not np.isfinite(polygon).all():
        fail(
            "E_CALIBRATION_PROJECTION",
            "board polygon projection returned NaN or infinity",
            "board_polygon",
        )
    return polygon


def deterministic_split(observations: Iterable[CharucoObservation], *, seed: int, heldout_ratio: float) -> tuple[list[CharucoObservation], list[CharucoObservation]]:
    values = list(observations)
    if len(values) < 2:
        fail("E_CALIBRATION_SPLIT", "at least two observations are required for a fit/held-out split", "observations")
    if not 0.05 <= heldout_ratio <= 0.40:
        fail("E_CALIBRATION_SPLIT", "heldout ratio must be in 0.05..0.40", "heldout_ratio")
    ranked = sorted(values, key=lambda item: canonical_sha256({"seed": seed, "filename": item.filename}))
    heldout_count = max(1, int(round(len(ranked) * heldout_ratio)))
    heldout = ranked[:heldout_count]
    fit = ranked[heldout_count:]
    return fit, heldout


def _camera_matrix_dict(matrix: np.ndarray, distortion: np.ndarray) -> dict[str, Any]:
    coefficients = np.asarray(distortion, dtype=np.float64).reshape(-1)
    if len(coefficients) < 5:
        fail("E_CALIBRATION_SOLVER", "OpenCV returned fewer than five Brown coefficients", "distortion")
    return {
        "matrix": np.asarray(matrix, dtype=np.float64).tolist(),
        "fx": float(matrix[0, 0]), "fy": float(matrix[1, 1]),
        "cx": float(matrix[0, 2]), "cy": float(matrix[1, 2]),
        "distortion": {
            "k1": float(coefficients[0]), "k2": float(coefficients[1]),
            "p1": float(coefficients[2]), "p2": float(coefficients[3]), "k3": float(coefficients[4]),
        },
    }


def residuals_for_observation(observation: CharucoObservation, matrix: np.ndarray, distortion: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    ok, rvec, tvec = cv2.solvePnP(
        observation.object_points.astype(np.float32), observation.image_points.astype(np.float32),
        matrix, distortion, flags=cv2.SOLVEPNP_ITERATIVE,
    )
    if not ok:
        fail("E_CALIBRATION_PNP", "solvePnP failed", observation.filename)
    projected, _ = cv2.projectPoints(observation.object_points, rvec, tvec, matrix, distortion)
    residual = np.linalg.norm(projected.reshape(-1, 2) - observation.image_points, axis=1)
    rotation, _ = cv2.Rodrigues(rvec)
    normal = rotation[:, 2]
    tilt = math.degrees(math.acos(float(np.clip(abs(normal[2]), 0.0, 1.0))))
    return residual, {
        "filename": observation.filename,
        "corners": observation.corner_count,
        "mean_px": float(np.mean(residual)),
        "rms_px": float(np.sqrt(np.mean(np.square(residual)))),
        "p95_px": float(np.percentile(residual, 95)),
        "max_px": float(np.max(residual)),
        "distance_m": float(np.linalg.norm(tvec)),
        "tilt_deg": tilt,
    }


def residual_summary(residuals: np.ndarray) -> dict[str, float]:
    if residuals.size == 0:
        fail("E_CALIBRATION_RESIDUAL", "no residuals are available", "residuals")
    return {
        "mean_px": float(np.mean(residuals)),
        "median_px": float(np.median(residuals)),
        "rms_px": float(np.sqrt(np.mean(np.square(residuals)))),
        "p90_px": float(np.percentile(residuals, 90)),
        "p95_px": float(np.percentile(residuals, 95)),
        "max_px": float(np.max(residuals)),
    }


def coverage_statistics(observations: list[CharucoObservation], grid_x: int = 4, grid_y: int = 3) -> dict[str, Any]:
    heatmap = np.zeros((grid_y, grid_x), dtype=np.int64)
    for observation in observations:
        xs = np.clip((observation.image_points[:, 0] / 640 * grid_x).astype(int), 0, grid_x - 1)
        ys = np.clip((observation.image_points[:, 1] / 360 * grid_y).astype(int), 0, grid_y - 1)
        for x, y in zip(xs, ys):
            heatmap[y, x] += 1
    return {
        "grid": [grid_x, grid_y], "corner_counts": heatmap.tolist(),
        "covered_cells": int(np.count_nonzero(heatmap)), "total_cells": int(heatmap.size),
        "outer_cells_covered": int(np.count_nonzero(np.concatenate([heatmap[0], heatmap[-1], heatmap[1:-1, 0], heatmap[1:-1, -1]]))),
    }


def calibrate_observations(
    fit: list[CharucoObservation], heldout: list[CharucoObservation], *, image_size: tuple[int, int] = (640, 360),
) -> dict[str, Any]:
    if len(fit) < 3 or not heldout:
        fail("E_CALIBRATION_SPLIT", "at least three fit images and one held-out image are required", "fit/heldout")
    object_points = [item.object_points.reshape(-1, 1, 3).astype(np.float32) for item in fit]
    image_points = [item.image_points.reshape(-1, 1, 2).astype(np.float32) for item in fit]
    flags = 0  # OpenCV Brown k1,k2,p1,p2,k3; no coefficient is fixed to zero.
    fit_rms, matrix, distortion, _, _ = cv2.calibrateCamera(object_points, image_points, image_size, None, None, flags=flags)
    if not np.isfinite(matrix).all() or not np.isfinite(distortion).all() or not math.isfinite(fit_rms):
        fail("E_CALIBRATION_NONFINITE", "solver returned NaN or infinity", "camera parameters")
    fit_residuals: list[np.ndarray] = []
    fit_per_image: list[dict[str, Any]] = []
    heldout_residuals: list[np.ndarray] = []
    heldout_per_image: list[dict[str, Any]] = []
    for item in fit:
        residual, stats = residuals_for_observation(item, matrix, distortion)
        fit_residuals.append(residual)
        fit_per_image.append(stats)
    for item in heldout:
        residual, stats = residuals_for_observation(item, matrix, distortion)
        heldout_residuals.append(residual)
        heldout_per_image.append(stats)
    all_fit = np.concatenate(fit_residuals)
    all_heldout = np.concatenate(heldout_residuals)
    return {
        "opencv_fit_rms_px": float(fit_rms),
        "fit": {**residual_summary(all_fit), "per_image": fit_per_image},
        "heldout": {**residual_summary(all_heldout), "per_image": heldout_per_image},
        "intrinsics": _camera_matrix_dict(matrix, distortion),
        "_matrix": matrix, "_distortion": distortion,
    }


def degeneracy_warnings(
    observations: list[CharucoObservation], solution: dict[str, Any], *, min_valid_images: int,
) -> list[str]:
    warnings: list[str] = []
    if len(observations) < min_valid_images:
        warnings.append("INSUFFICIENT_VALID_IMAGES")
    coverage = coverage_statistics(observations)
    if coverage["covered_cells"] < 8 or coverage["outer_cells_covered"] < 5:
        warnings.append("INSUFFICIENT_IMAGE_COVERAGE")
    centers = np.asarray([item.centroid for item in observations])
    if np.ptp(centers[:, 0]) < 0.25 * 640 or np.ptp(centers[:, 1]) < 0.20 * 360:
        warnings.append("POSE_CENTERS_TOO_SIMILAR")
    per_image = solution["fit"]["per_image"] + solution["heldout"]["per_image"]
    distances = np.asarray([item["distance_m"] for item in per_image])
    tilts = np.asarray([item["tilt_deg"] for item in per_image])
    if np.max(distances) / max(np.min(distances), 1e-9) < 1.20:
        warnings.append("BOARD_DISTANCES_TOO_SIMILAR")
    if np.max(tilts) < 8.0 or np.ptp(tilts) < 5.0:
        warnings.append("BOARD_POSES_TOO_FRONTO_PARALLEL")
    intrinsics = solution["intrinsics"]
    if not (128.0 <= intrinsics["fx"] <= 6400.0 and 72.0 <= intrinsics["fy"] <= 3600.0):
        warnings.append("FOCAL_LENGTH_IMPLAUSIBLE")
    if not (0 <= intrinsics["cx"] < 640 and 0 <= intrinsics["cy"] < 360):
        warnings.append("PRINCIPAL_POINT_OUTSIDE_IMAGE")
    if solution["heldout"]["rms_px"] > 2.0:
        warnings.append("HELDOUT_RMS_EXCEEDS_DEMO_CEILING")
    return warnings


def _failure_report(
    output_dir: Path, image_records: list[dict[str, Any]], error: CameraCalibrationError,
    *, fit_names: list[str] | None = None, heldout_names: list[str] | None = None,
) -> None:
    write_json(output_dir / "camera_intrinsics_report.json", {
        "schema_version": INTRINSICS_REPORT_SCHEMA, "status": "FAIL", "generated_at": _utc_now(),
        "error": error.as_dict(), "images": image_records, "fit_images": fit_names or [], "heldout_images": heldout_names or [],
        "measured_profile_generated": False,
    })


def calibrate_camera_charuco(
    images_dir: Path,
    board_manifest_path: Path,
    orientation_manifest_path: Path,
    output_dir: Path,
    *,
    seed: int = 7,
    heldout_ratio: float = 0.2,
    min_valid_images: int = 25,
    min_charuco_corners: int = 8,
    accept_demo_only: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    board_manifest = validate_board_manifest(load_strict_json(board_manifest_path), require_print_measurement=True)
    orientation_manifest = validate_orientation_manifest(load_strict_json(orientation_manifest_path), require_confirmed=True)
    board = charuco_board_from_manifest(board_manifest)
    paths = sorted(path for path in images_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES) if images_dir.is_dir() else []
    image_records: list[dict[str, Any]] = []
    observations: list[CharucoObservation] = []
    for path in paths:
        try:
            observation = detect_observation(path, board, min_charuco_corners=min_charuco_corners)
            observations.append(observation)
            image_records.append({
                "filename": observation.filename, "sha256": observation.sha256,
                "width": observation.width, "height": observation.height, "capture_timestamp": None,
                "source": "images_dir", "accepted": True, "rejection": None,
                "detected_markers": observation.marker_count, "detected_corners": observation.corner_count,
                "coverage": observation.coverage, "sharpness": observation.sharpness,
            })
        except CameraCalibrationError as exc:
            image_records.append({
                "filename": path.name, "sha256": sha256_file(path), "width": None, "height": None,
                "capture_timestamp": None, "source": "images_dir", "accepted": False,
                "rejection": exc.as_dict(), "detected_markers": 0, "detected_corners": 0,
                "coverage": 0.0, "sharpness": 0.0,
            })
    if len(observations) < min_valid_images:
        error = CameraCalibrationError("E_CALIBRATION_IMAGES", f"need at least {min_valid_images} valid images", "images_dir", {"valid": len(observations), "rejected": len(image_records) - len(observations)})
        _failure_report(output_dir, image_records, error)
        raise error
    fit, heldout = deterministic_split(observations, seed=seed, heldout_ratio=heldout_ratio)
    if len(heldout) < max(1, math.ceil(min_valid_images * heldout_ratio) - 1):
        error = CameraCalibrationError("E_CALIBRATION_HELDOUT", "held-out set is insufficient", "heldout", len(heldout))
        _failure_report(output_dir, image_records, error, fit_names=[x.filename for x in fit], heldout_names=[x.filename for x in heldout])
        raise error
    solution = calibrate_observations(fit, heldout)
    warnings = degeneracy_warnings(observations, solution, min_valid_images=min_valid_images)
    heldout_rms = solution["heldout"]["rms_px"]
    quality_status = "PASS" if heldout_rms <= 1.0 else "PASS_DEMO_ONLY" if heldout_rms <= 2.0 else "FAIL"
    fatal_warnings = [item for item in warnings if item != "HELDOUT_RMS_EXCEEDS_DEMO_CEILING"]
    accepted = quality_status == "PASS" and not fatal_warnings
    if quality_status == "PASS_DEMO_ONLY" and accept_demo_only and not fatal_warnings:
        accepted = True
    if fatal_warnings:
        quality_status = "FAIL"
    image_hashes = {item.filename: item.sha256 for item in observations}
    image_hash_manifest = {
        "accepted_image_sha256": image_hashes,
        "fit_images": [item.filename for item in fit],
        "heldout_images": [item.filename for item in heldout],
        "seed": seed,
    }
    write_json(output_dir / "accepted_images_manifest.json", image_hash_manifest)
    public_solution = {key: value for key, value in solution.items() if not key.startswith("_")}
    report = {
        "schema_version": INTRINSICS_REPORT_SCHEMA, "status": quality_status, "generated_at": _utc_now(),
        "model": "opencv_brown", "opencv_flags": 0,
        "images": image_records, "accepted_images": len(observations),
        "rejected_images": len(image_records) - len(observations), "fit_images": [item.filename for item in fit],
        "heldout_images": [item.filename for item in heldout], "total_corners": sum(item.corner_count for item in observations),
        "coverage": coverage_statistics(observations), "solution": public_solution,
        "degeneracy_warnings": warnings, "measured_profile_generated": accepted,
    }
    write_json(output_dir / "camera_intrinsics_report.json", report)
    if not accepted:
        fail("E_CALIBRATION_QUALITY", "calibration quality/degeneracy gate failed; measured profile was not generated", "quality", {
            "quality_status": quality_status, "warnings": warnings, "heldout_rms_px": heldout_rms,
        })
    intrinsics = solution["intrinsics"]
    cli_parameters = {
        "seed": seed, "heldout_ratio": heldout_ratio, "min_valid_images": min_valid_images,
        "min_charuco_corners": min_charuco_corners, "accept_demo_only": accept_demo_only,
    }
    provenance = {
        "board_manifest_sha256": sha256_file(board_manifest_path),
        "orientation_manifest_sha256": sha256_file(orientation_manifest_path),
        "accepted_images_manifest_sha256": sha256_file(output_dir / "accepted_images_manifest.json"),
        "accepted_image_sha256": image_hashes,
        "python_version": platform.python_version(), "opencv_version": cv2.__version__,
        "cli_parameters": cli_parameters, "seed": seed,
        "fit_images": [item.filename for item in fit], "heldout_images": [item.filename for item in heldout],
        "generated_at": _utc_now(), "tool_version": TOOL_VERSION, "source_path": str(images_dir.resolve()),
    }
    profile_seed = {"intrinsics": intrinsics, "provenance": provenance, "board_id": board_manifest["board_id"]}
    profile = {
        "schema_version": CALIBRATION_V2_SCHEMA,
        "profile_id": "sc2336_intrinsics_" + canonical_sha256(profile_seed)[:16],
        "measurement_status": "measured", "absolute_geometry_valid": False,
        "camera": {
            "resolution": {"width": 640, "height": 360},
            "orientation": {"physical": "landscape", "software_rotation_degrees": 0, "scene_upright_confirmed": True},
            "intrinsics": {
                "model": "opencv_brown", **{key: intrinsics[key] for key in ("fx", "fy", "cx", "cy")},
                "distortion": intrinsics["distortion"],
            },
        },
        "camera_tof_extrinsics": {"status": "unresolved"}, "world_pose": {"status": "unresolved"},
        "quality": {
            "fit_rms_px": solution["opencv_fit_rms_px"], "heldout_rms_px": heldout_rms,
            "heldout_p95_px": solution["heldout"]["p95_px"], "accepted_images": len(observations),
            "fit_images": len(fit), "heldout_images": len(heldout), "quality_status": quality_status,
        },
        "provenance": provenance,
    }
    validate_calibration_v2(profile)
    write_json(output_dir / "calibration_profile.v2.json", profile)
    return {"report": report, "profile": profile}
