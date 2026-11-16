from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np

from .camera_world_contracts import fail


def _finite_points(points: Any, field: str) -> np.ndarray:
    array = np.asarray(points, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] not in {2, 3}:
        fail(
            "E_CAMERA_WORLD_OVERLAY",
            "overlay points must be Nx2 or Nx3",
            field,
        )
    if not np.isfinite(array).all():
        fail(
            "E_CAMERA_WORLD_OVERLAY",
            "overlay points contain NaN or infinity",
            field,
        )
    return array


def _project_camera_points(
    points_camera_mm: np.ndarray,
    matrix: np.ndarray,
    distortion: np.ndarray,
) -> np.ndarray:
    points = _finite_points(points_camera_mm, "points_camera_mm")
    if points.shape[1] != 3 or np.any(points[:, 2] <= 0):
        fail(
            "E_CAMERA_WORLD_OVERLAY",
            "projected Camera points must be finite Nx3 with z>0",
            "points_camera_mm",
        )
    projected, _ = cv2.projectPoints(
        points / 1000.0,
        np.zeros(3, dtype=np.float64),
        np.zeros(3, dtype=np.float64),
        np.asarray(matrix, dtype=np.float64),
        np.asarray(distortion, dtype=np.float64),
    )
    result = projected.reshape(-1, 2)
    return _finite_points(result, "projected_points")


def _draw_world_axes(
    image: np.ndarray,
    frame: Any,
    matrix: np.ndarray,
    distortion: np.ndarray,
) -> None:
    origin_camera_mm = (
        np.asarray(frame.translation_camera_from_board_m, dtype=np.float64)
        * 1000.0
    )
    rotation_camera_from_world = (
        np.asarray(frame.rotation_world_from_camera, dtype=np.float64).T
    )
    length_mm = 90.0
    world_vectors = np.eye(3, dtype=np.float64) * length_mm
    camera_points = np.vstack(
        (
            origin_camera_mm,
            origin_camera_mm
            + (rotation_camera_from_world @ world_vectors.T).T,
        )
    )
    projected = _project_camera_points(camera_points, matrix, distortion)
    origin = tuple(np.rint(projected[0]).astype(int))
    colors = ((0, 0, 255), (0, 180, 0), (255, 80, 0))
    labels = ("+Xw", "+Yw", "+Zw")
    for index, (color, label) in enumerate(zip(colors, labels), start=1):
        endpoint = tuple(np.rint(projected[index]).astype(int))
        cv2.arrowedLine(image, origin, endpoint, color, 2, cv2.LINE_AA, tipLength=0.15)
        cv2.putText(
            image,
            label,
            endpoint,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )


def _draw_overlap_grid(image: np.ndarray, overlap: np.ndarray) -> None:
    mask = np.asarray(overlap, dtype=bool).reshape(8, 8)
    cell = 8
    x0 = image.shape[1] - cell * 8 - 12
    y0 = 12
    for row in range(8):
        for col in range(8):
            left = x0 + col * cell
            top = y0 + row * cell
            color = (0, 180, 0) if mask[row, col] else (80, 80, 80)
            cv2.rectangle(
                image,
                (left, top),
                (left + cell - 1, top + cell - 1),
                color,
                -1,
            )
    cv2.putText(
        image,
        f"Stage7B overlap {int(np.count_nonzero(mask))}/64",
        (x0 - 75, y0 + cell * 8 + 16),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.38,
        (230, 230, 230),
        1,
        cv2.LINE_AA,
    )


def _draw_world_plane_normal(
    image: np.ndarray,
    frame: Any,
    normal_world: np.ndarray,
    matrix: np.ndarray,
    distortion: np.ndarray,
) -> None:
    normal = np.asarray(normal_world, dtype=np.float64).reshape(3)
    if not np.isfinite(normal).all() or np.linalg.norm(normal) <= 0:
        fail(
            "E_CAMERA_WORLD_OVERLAY",
            "ToF world plane normal must be finite and nonzero",
            "plane_normal_world",
        )
    normal /= np.linalg.norm(normal)
    origin_camera_mm = (
        np.asarray(frame.translation_camera_from_board_m, dtype=np.float64)
        * 1000.0
    )
    direction_camera = (
        np.asarray(frame.rotation_world_from_camera, dtype=np.float64).T
        @ normal
    )
    points = np.vstack(
        (origin_camera_mm, origin_camera_mm + direction_camera * 120.0)
    )
    projected = _project_camera_points(points, matrix, distortion)
    origin = tuple(np.rint(projected[0]).astype(int))
    endpoint = tuple(np.rint(projected[1]).astype(int))
    cv2.arrowedLine(
        image, origin, endpoint, (255, 255, 0), 2, cv2.LINE_AA, tipLength=0.18
    )
    cv2.putText(
        image,
        "ToF plane +normal_world",
        endpoint,
        cv2.FONT_HERSHEY_SIMPLEX,
        0.35,
        (255, 255, 0),
        1,
        cv2.LINE_AA,
    )


def write_overlays(output_dir: Path, poses: Iterable[Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for pose in poses:
        if pose.overlay is None:
            continue
        frame = pose.overlay["frame"]
        tof = pose.overlay["tof"]
        matrix = np.asarray(pose.overlay["matrix"], dtype=np.float64)
        distortion = np.asarray(
            pose.overlay["distortion"], dtype=np.float64
        )
        image = np.asarray(frame.image_bgr).copy()
        if (
            image.shape != (360, 640, 3)
            or not np.isfinite(image.astype(np.float64)).all()
        ):
            fail(
                "E_CAMERA_WORLD_OVERLAY",
                "representative image must be finite 640x360 BGR",
                pose.pose_name,
            )
        polygon = _finite_points(frame.board_polygon, "board_polygon")
        if polygon.shape != (4, 2):
            fail(
                "E_CAMERA_WORLD_OVERLAY",
                "board polygon must be 4x2",
                pose.pose_name,
            )
        cv2.polylines(
            image,
            [np.rint(polygon).astype(np.int32)],
            True,
            (255, 0, 255),
            2,
            cv2.LINE_AA,
        )
        rvec, _ = cv2.Rodrigues(
            np.asarray(frame.rotation_camera_from_board, dtype=np.float64)
        )
        cv2.drawFrameAxes(
            image,
            matrix,
            distortion,
            rvec,
            np.asarray(
                frame.translation_camera_from_board_m, dtype=np.float64
            ),
            0.08,
            2,
        )
        _draw_world_axes(image, frame, matrix, distortion)
        cv2.putText(
            image,
            f"{pose.pose_name} / {frame.frame_name}",
            (12, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            image,
            "magenta=ChArUco; RGB=board pose; Xw/Yw/Zw=world axes",
            (12, 43),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            (230, 230, 230),
            1,
            cv2.LINE_AA,
        )
        if tof is not None:
            points = _project_camera_points(
                tof["points_camera"], matrix, distortion
            )
            zones = np.asarray(tof["zones"], dtype=np.int64)
            raw_map = np.asarray(tof["raw_map"], dtype=np.int64)
            inliers = np.asarray(tof["inlier_mask"], dtype=bool)
            if len(points) != len(zones) or len(inliers) != len(zones):
                fail(
                    "E_CAMERA_WORLD_OVERLAY",
                    "ToF overlay arrays have inconsistent lengths",
                    pose.pose_name,
                )
            for point, zone, inlier in zip(points, zones, inliers):
                centre = tuple(np.rint(point).astype(int))
                color = (0, 255, 255) if inlier else (0, 0, 255)
                cv2.circle(image, centre, 4, color, -1, cv2.LINE_AA)
                cv2.putText(
                    image,
                    f"c{int(zone)}/r{int(raw_map[zone])}",
                    (centre[0] + 4, centre[1] - 3),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.27,
                    color,
                    1,
                    cv2.LINE_AA,
                )
            _draw_overlap_grid(image, tof["overlap_mask"])
            _draw_world_plane_normal(
                image,
                frame,
                tof["plane_normal_world"],
                matrix,
                distortion,
            )
            angular = pose.tof_diagnostics.get(
                "normal_angular_error_deg"
            )
            angular_text = (
                "n/a" if angular is None else f"{float(angular):.3f} deg"
            )
            cv2.putText(
                image,
                (
                    f"ToF world plane: yellow=inlier red=outlier; "
                    f"normal error={angular_text}"
                ),
                (12, image.shape[0] - 14),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.38,
                (230, 230, 230),
                1,
                cv2.LINE_AA,
            )
        output_path = output_dir / (
            f"{pose.pose_name}_camera_world_overlay.png"
        )
        if not cv2.imwrite(
            str(output_path), image, [cv2.IMWRITE_PNG_COMPRESSION, 6]
        ):
            fail(
                "E_CAMERA_WORLD_OUTPUT",
                "OpenCV failed to write overlay",
                str(output_path),
            )


def write_orientation_csv(
    path: Path, diagnostics: Iterable[dict[str, Any]]
) -> None:
    fields = [
        "pose_name", "role", "frame_name", "scan_dir", "valid",
        "detected_corners", "marker_count", "reprojection_rms_px",
        "reprojection_p95_px", "tvec_z_m", "finite_pose",
        "orientation_inlier", "orientation_residual_deg", "error",
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for item in diagnostics:
            writer.writerow(item)


def write_tof_csv(path: Path, poses: Iterable[Any]) -> None:
    fields = [
        "pose_name", "role", "canonical_zone", "raw_zone",
        "overlap", "plane_inlier", "absolute_plane_residual_mm",
        "normal_angular_error_deg", "plane_rms_mm", "plane_p95_mm",
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for pose in poses:
            tof = None if pose.overlay is None else pose.overlay["tof"]
            if tof is None:
                writer.writerow(
                    {
                        "pose_name": pose.pose_name,
                        "role": pose.role,
                        "normal_angular_error_deg": pose.tof_diagnostics.get(
                            "normal_angular_error_deg"
                        ),
                        "plane_rms_mm": pose.tof_diagnostics.get(
                            "plane_rms_mm"
                        ),
                        "plane_p95_mm": pose.tof_diagnostics.get(
                            "plane_p95_mm"
                        ),
                    }
                )
                continue
            zones = np.asarray(tof["zones"], dtype=np.int64)
            raw_map = np.asarray(tof["raw_map"], dtype=np.int64)
            inliers = np.asarray(tof["inlier_mask"], dtype=bool)
            residuals = np.asarray(
                tof["absolute_plane_residual_mm"], dtype=np.float64
            )
            overlap = np.asarray(tof["overlap_mask"], dtype=bool)
            for zone, keep, residual in zip(zones, inliers, residuals):
                writer.writerow(
                    {
                        "pose_name": pose.pose_name,
                        "role": pose.role,
                        "canonical_zone": int(zone),
                        "raw_zone": int(raw_map[zone]),
                        "overlap": bool(overlap[zone]),
                        "plane_inlier": bool(keep),
                        "absolute_plane_residual_mm": float(residual),
                        "normal_angular_error_deg": pose.tof_diagnostics[
                            "normal_angular_error_deg"
                        ],
                        "plane_rms_mm": pose.tof_diagnostics[
                            "plane_rms_mm"
                        ],
                        "plane_p95_mm": pose.tof_diagnostics[
                            "plane_p95_mm"
                        ],
                    }
                )
