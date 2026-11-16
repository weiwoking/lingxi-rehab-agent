from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from deltar_p4_pipeline.camera_calibration import (
    CharucoObservation,
    board_pose_to_camera_plane,
    solve_board_pose,
)
from deltar_p4_pipeline.camera_world_calibration import (
    R_WORLD_FROM_BOARD,
    orientation_diagnostics,
    rotation_distance_deg,
    rotation_world_from_camera_from_board_pose,
)
from deltar_p4_pipeline.camera_world_contracts import (
    load_strict_json,
    sha256_file,
    write_json,
)
from deltar_p4_pipeline.charuco_board import charuco_board_from_manifest


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOARD_MANIFEST = ROOT / "board_assets" / "board_manifest.json"
DEFAULT_CAMERA_PROFILE = Path(
    r"C:\calibration\result_20260719_172703\calibration_profile.v2.json"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "artifacts" / "stage7c_precapture_probe"
)


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


def _observation(
    object_points: np.ndarray,
    rotation_camera_from_board: np.ndarray,
    translation_camera_from_board_m: np.ndarray,
    matrix: np.ndarray,
    distortion: np.ndarray,
    name: str,
) -> CharucoObservation:
    rvec, _ = cv2.Rodrigues(rotation_camera_from_board)
    projected, _ = cv2.projectPoints(
        object_points,
        rvec,
        translation_camera_from_board_m,
        matrix,
        distortion,
    )
    image_points = projected.reshape(-1, 2).astype(np.float32)
    return CharucoObservation(
        filename=name,
        sha256="synthetic_probe",
        width=640,
        height=360,
        image_points=image_points,
        object_points=object_points.astype(np.float32),
        marker_count=len(object_points),
        corner_count=len(object_points),
        coverage=0.0,
        sharpness=0.0,
        centroid=(
            float(np.mean(image_points[:, 0])),
            float(np.mean(image_points[:, 1])),
        ),
        board_angle_deg=0.0,
    )


def _rendered_corner_pixels(
    board: Any, ids_of_interest: tuple[int, ...]
) -> dict[int, list[float]]:
    rendered = board.generateImage((700, 500), marginSize=0, borderBits=1)
    if hasattr(cv2.aruco, "CharucoDetector"):
        corners, ids, _, _ = cv2.aruco.CharucoDetector(board).detectBoard(
            rendered
        )
    else:
        marker_corners, marker_ids, _ = cv2.aruco.detectMarkers(
            rendered, board.getDictionary()
        )
        _, corners, ids = cv2.aruco.interpolateCornersCharuco(
            marker_corners, marker_ids, rendered, board
        )
    if corners is None or ids is None:
        raise RuntimeError("OpenCV could not detect its generated ChArUco board")
    lookup = {
        int(identifier): point.tolist()
        for identifier, point in zip(
            np.asarray(ids).reshape(-1),
            np.asarray(corners).reshape(-1, 2),
        )
    }
    missing = [identifier for identifier in ids_of_interest if identifier not in lookup]
    if missing:
        raise RuntimeError(f"generated board did not yield required IDs: {missing}")
    return {identifier: lookup[identifier] for identifier in ids_of_interest}


def _pose_result(
    name: str,
    expected_rotation: np.ndarray,
    object_points: np.ndarray,
    matrix: np.ndarray,
    distortion: np.ndarray,
) -> dict[str, Any]:
    expected_translation = np.asarray([0.0, 0.0, 0.8], dtype=np.float64)
    solved_rotation, solved_translation, diagnostics = solve_board_pose(
        _observation(
            object_points,
            expected_rotation,
            expected_translation,
            matrix,
            distortion,
            name,
        ),
        matrix,
        distortion,
    )
    plane_normal, plane_offset_mm = board_pose_to_camera_plane(
        solved_rotation, solved_translation
    )
    rotation_world_from_camera = (
        rotation_world_from_camera_from_board_pose(solved_rotation)
    )
    human_angles = orientation_diagnostics(rotation_world_from_camera)
    return {
        "name": name,
        "expected_rotation_camera_from_board": expected_rotation.tolist(),
        "solved_rotation_camera_from_board": solved_rotation.tolist(),
        "solved_translation_camera_from_board_m": solved_translation.tolist(),
        "tvec_z_positive": bool(solved_translation[2] > 0),
        "reprojection": diagnostics,
        "camera_plane_normal_sign_normalized": plane_normal.tolist(),
        "camera_plane_offset_mm": plane_offset_mm,
        "rotation_world_from_camera": rotation_world_from_camera.tolist(),
        "orientation_diagnostics": human_angles,
        "passes_formal_mechanical_angle_gate": bool(
            abs(human_angles["roll_deg"]) <= 5.0
            and abs(human_angles["pitch_deg"]) <= 5.0
            and abs(human_angles["yaw_deg"]) <= 5.0
        ),
    }


def run_probe(
    board_manifest_path: Path,
    camera_profile_path: Path,
) -> dict[str, Any]:
    manifest = load_strict_json(board_manifest_path)
    board = charuco_board_from_manifest(manifest)
    object_points = np.asarray(
        board.getChessboardCorners(), dtype=np.float32
    ).reshape(-1, 3)
    corners_per_row = int(manifest["squares_x"]) - 1
    id0 = 0
    right_id = 1
    next_row_id = corners_per_row
    rendered_pixels = _rendered_corner_pixels(
        board, (id0, right_id, next_row_id)
    )
    delta_x = object_points[right_id] - object_points[id0]
    delta_y = object_points[next_row_id] - object_points[id0]
    pixel_delta_x = (
        np.asarray(rendered_pixels[right_id])
        - np.asarray(rendered_pixels[id0])
    )
    pixel_delta_y = (
        np.asarray(rendered_pixels[next_row_id])
        - np.asarray(rendered_pixels[id0])
    )
    cross = np.cross(delta_x, delta_y)
    cross /= np.linalg.norm(cross)

    camera_profile = load_strict_json(camera_profile_path)
    matrix, distortion = _camera_parameters(camera_profile)
    front = _pose_result(
        "printed_front_facing_camera",
        np.eye(3, dtype=np.float64),
        object_points,
        matrix,
        distortion,
    )
    back = _pose_result(
        "obvious_back_facing_flip_about_board_x",
        np.diag([1.0, -1.0, -1.0]),
        object_points,
        matrix,
        distortion,
    )
    separation = rotation_distance_deg(
        np.asarray(front["rotation_world_from_camera"]),
        np.asarray(back["rotation_world_from_camera"]),
    )
    mapping_expected = np.asarray(
        [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]]
    )
    mapping_consistent = bool(
        np.allclose(R_WORLD_FROM_BOARD, mapping_expected)
        and delta_x[0] > 0
        and np.allclose(delta_x[1:], 0.0)
        and delta_y[1] > 0
        and np.allclose(delta_y[[0, 2]], 0.0)
        and pixel_delta_x[0] > 0
        and abs(pixel_delta_x[1]) < 1.0
        and pixel_delta_y[1] > 0
        and abs(pixel_delta_y[0]) < 1.0
        and np.allclose(cross, [0.0, 0.0, 1.0])
    )
    front_back_safe = bool(
        front["tvec_z_positive"]
        and front["passes_formal_mechanical_angle_gate"]
        and back["tvec_z_positive"]
        and not back["passes_formal_mechanical_angle_gate"]
        and separation > 179.0
    )
    return {
        "schema_version": "stage7c_board_coordinate_probe.v1",
        "created_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "opencv_version": cv2.__version__,
        "board_manifest_path": str(board_manifest_path.resolve()),
        "board_manifest_sha256": sha256_file(board_manifest_path),
        "board": {
            "squares_x": manifest["squares_x"],
            "squares_y": manifest["squares_y"],
            "square_length_mm": manifest["square_length_mm"],
            "legacy_pattern_api_available": hasattr(
                board, "getLegacyPattern"
            ),
            "legacy_pattern_enabled": (
                bool(board.getLegacyPattern())
                if hasattr(board, "getLegacyPattern")
                else None
            ),
            "chessboard_corner_count": int(len(object_points)),
            "first_object_points_m": object_points[:8].tolist(),
        },
        "direct_object_point_evidence": {
            "id0": {"id": id0, "object_point_m": object_points[id0].tolist()},
            "id0_same_row_right": {
                "id": right_id,
                "object_point_m": object_points[right_id].tolist(),
                "delta_from_id0_m": delta_x.tolist(),
            },
            "id0_next_row": {
                "id": next_row_id,
                "object_point_m": object_points[next_row_id].tolist(),
                "delta_from_id0_m": delta_y.tolist(),
            },
            "generated_print_pixels": {
                str(identifier): rendered_pixels[identifier]
                for identifier in (id0, right_id, next_row_id)
            },
            "generated_print_pixel_delta_right": pixel_delta_x.tolist(),
            "generated_print_pixel_delta_next_row": pixel_delta_y.tolist(),
            "right_hand_cross_x_y": cross.tolist(),
        },
        "physical_inference": {
            "x_board": (
                "toward printed-board right: +X object increment matches "
                "increasing x pixel in board.generateImage()"
            ),
            "y_board": (
                "toward printed-board down: +Y object increment matches "
                "increasing y pixel in board.generateImage()"
            ),
            "z_board": (
                "right-hand +X cross +Y; through the printed board away "
                "from the Camera/vehicle when the printed front faces it"
            ),
        },
        "world_mapping": {
            "R_world_from_board": R_WORLD_FROM_BOARD.tolist(),
            "x_board_to_world": (
                R_WORLD_FROM_BOARD @ [1.0, 0.0, 0.0]
            ).tolist(),
            "y_board_to_world": (
                R_WORLD_FROM_BOARD @ [0.0, 1.0, 0.0]
            ).tolist(),
            "z_board_to_world": (
                R_WORLD_FROM_BOARD @ [0.0, 0.0, 1.0]
            ).tolist(),
            "consistent_with_actual_object_points": mapping_consistent,
        },
        "front_back_probe": {
            "front": front,
            "back": back,
            "world_rotation_separation_deg": separation,
            "conclusion": (
                "tvec.z>0 alone does not encode printed front/back. The full "
                "solved rotation is preserved by the world mapping: the "
                "obvious flip differs by 180 degrees and fails the existing "
                "absolute roll/pitch/yaw <=5 degree formal gates."
            ),
            "current_formal_path_is_safe": front_back_safe,
        },
        "verdict": (
            "PASS"
            if mapping_consistent and front_back_safe
            else "FAIL"
        ),
    }


def _text_report(result: dict[str, Any]) -> str:
    evidence = result["direct_object_point_evidence"]
    mapping = result["world_mapping"]
    front_back = result["front_back_probe"]
    return "\n".join(
        [
            "Stage 7C pre-capture board coordinate probe",
            f"verdict: {result['verdict']}",
            f"OpenCV: {result['opencv_version']}",
            f"manifest: {result['board_manifest_path']}",
            f"manifest SHA256: {result['board_manifest_sha256']}",
            (
                "board: "
                f"{result['board']['squares_x']}x"
                f"{result['board']['squares_y']} squares, "
                f"square={result['board']['square_length_mm']} mm, "
                f"legacy_pattern={result['board']['legacy_pattern_enabled']}"
            ),
            f"first object points (m): {result['board']['first_object_points_m']}",
            f"ID0: {evidence['id0']}",
            f"same-row right: {evidence['id0_same_row_right']}",
            f"next row: {evidence['id0_next_row']}",
            f"+X_board: {result['physical_inference']['x_board']}",
            f"+Y_board: {result['physical_inference']['y_board']}",
            f"+Z_board: {result['physical_inference']['z_board']}",
            f"R_world_from_board: {mapping['R_world_from_board']}",
            (
                "mapping consistent with actual object points: "
                f"{mapping['consistent_with_actual_object_points']}"
            ),
            (
                "front/back world rotation separation: "
                f"{front_back['world_rotation_separation_deg']:.9f} deg"
            ),
            (
                "front passes mechanical gate: "
                f"{front_back['front']['passes_formal_mechanical_angle_gate']}"
            ),
            (
                "back passes mechanical gate: "
                f"{front_back['back']['passes_formal_mechanical_angle_gate']}"
            ),
            f"front/back conclusion: {front_back['conclusion']}",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read the actual OpenCV ChArUco board object and probe the frozen "
            "Stage 7C board/world coordinate contract."
        )
    )
    parser.add_argument(
        "--board-manifest", type=Path, default=DEFAULT_BOARD_MANIFEST
    )
    parser.add_argument(
        "--camera-profile", type=Path, default=DEFAULT_CAMERA_PROFILE
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR
    )
    args = parser.parse_args()
    result = run_probe(args.board_manifest, args.camera_profile)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / "board_coordinate_probe.json", result)
    (args.output_dir / "board_coordinate_probe.txt").write_text(
        _text_report(result), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["verdict"] == "PASS" else 8


if __name__ == "__main__":
    raise SystemExit(main())
