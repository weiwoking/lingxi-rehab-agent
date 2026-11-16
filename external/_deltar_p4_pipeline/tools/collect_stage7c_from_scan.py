#!/usr/bin/env python3
"""Collect Stage 7C observations from the frozen 11-direction scan chain.

This is deliberately a PC-only adapter.  It never edits a source scan and it
does not implement a second calibration solver.  It decodes the authoritative
RGB565 payload, uses the existing Stage 7A/7C ChArUco and PnP functions for
quality checks, and writes one-frame compatibility scans that the original
``camera-world-calibrate`` implementation can consume.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from datetime import datetime, timezone
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import cv2
import numpy as np

from deltar_p4_pipeline.camera_calibration import (
    detect_observation_image,
    solve_board_pose,
)
from deltar_p4_pipeline.camera_calibration_contracts import (
    CameraCalibrationError,
    load_strict_json as load_camera_json,
    validate_board_manifest,
    validate_calibration_v2,
)
from deltar_p4_pipeline.camera_world_calibration import (
    _camera_parameters,
    calibrate_capture,
    manifest_template,
    robust_rotation_average,
    rotation_distance_deg,
    rotation_world_from_camera_from_board_pose,
)
from deltar_p4_pipeline.camera_world_contracts import (
    CameraWorldError,
    FROZEN_CAMERA_TOF_PROFILE,
    canonical_sha256,
    load_frozen_camera_tof_profile,
    sha256_file,
    write_json,
)
from deltar_p4_pipeline.charuco_board import charuco_board_from_manifest
from deltar_p4_pipeline.rgb565 import read_rgb565_le
from deltar_p4_pipeline.scan_profiles import load_profile_bundle
from deltar_p4_pipeline.scan_validator import validate_scan


TOOL_VERSION = "stage7c-scan-collector-1.4"
DEFAULT_DATA_ROOT = Path(r"C:\ec\_deltar_scan_service_data")
DEFAULT_SESSION_ROOT = Path(
    r"C:\Users\Weiwo\Downloads\stage7c_recalibration_20260813"
)
DEFAULT_CAMERA_PROFILE = Path(
    r"C:\calibration\result_20260719_172703\calibration_profile.v2.json"
)
DEFAULT_STAGE7C_PROFILE = Path(
    r"C:\calibration\stage7c_world_result_robustfix_20260724_044035"
    r"\camera_world_pose.v1.json"
)
DEFAULT_BOARD_MANIFEST = ROOT / "board_assets" / "board_manifest.json"
DEFAULT_PROFILE_BUNDLE = ROOT / "profiles"

EXPECTED_BOARD_MANIFEST_SHA256 = (
    "db5dafdf1c1dd5208f883850919d0182efaf9a20f9b06d6a6debf39735cbd37a"
)
EXPECTED_BOARD_IMAGE_SHA256 = (
    "6db3ba6f7b3ee5b77648f074db42e8b7c259910ec8b5a4656e0617348276961b"
)
EXPECTED_BOARD_DEFINITION_SHA256 = (
    "7cdbc4afea4409a17489747ce30c20ca686b7ce5742026d8b1201c0a46d2244c"
)
EXPECTED_BOARD_FIELDS = {
    "family": "opencv_charuco",
    "dictionary": "DICT_5X5_100",
    "squares_x": 7,
    "squares_y": 5,
    "square_length_mm": 25.0,
    "marker_length_mm": 18.0,
    "page_size": "A4",
    "print_scale": 1.0,
    "render_dpi": 300,
}
ROLE_TO_FORMAL = {
    "near": "world_front_near",
    "mid": "world_front_mid",
    "far": "world_front_far",
}
ROLE_DISTANCE_MM = {"near": 650.0, "mid": 850.0, "far": 1050.0}
STATIONARY_MIN_VALID_FRAMES = 8
STATIONARY_P95_MAX_DEG = 0.5
STATIONARY_ABSOLUTE_MAX_DEG = 1.0
STRICT_PAIR_DELTA_MAX_US = 10_000
SELECTION_DIRECTION0 = "direction0_only"
SELECTION_STATIONARY_BEST = "stationary_best_of_11"
SELECTION_BEST_OF_11 = "best_of_11_independent_frame_quality"
CROSS_FRAME_POSE_GATE_DISABLED = "disabled_by_collection_contract"
VEHICLE_MOTION_ASSUMPTION = "physically_unpowered_by_user"
MID_REFERENCE_SCHEMA_VERSION = "stage7c_mid_pose_reference.v1"
MID_REFERENCE_REQUIRED_SAMPLES = 5
POSE_DIAGNOSTIC_GOOD_MAX_DEG = 0.8
POSE_DIAGNOSTIC_WITHIN_ONE_DEG_MAX_DEG = 1.0


class CollectorError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CollectorError(f"cannot read JSON {path}: {exc}") from exc
    if type(value) is not dict:
        raise CollectorError(f"JSON root must be an object: {path}")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pose_diagnostic_status(error_deg: float) -> str:
    if error_deg <= POSE_DIAGNOSTIC_GOOD_MAX_DEG:
        return "GOOD"
    if error_deg <= POSE_DIAGNOSTIC_WITHIN_ONE_DEG_MAX_DEG:
        return "WITHIN_1_DEG"
    return "ADJUST"


def _mid_reference_source_state(
    session_root: Path,
    contracts: dict[str, Any],
) -> dict[str, Any]:
    session_path = session_root / "session.json"
    if not session_path.is_file():
        return {
            "availability": "UNAVAILABLE",
            "reason": "requires_mid_5_of_5",
            "mid_sample_count": 0,
        }
    session = read_json(session_path)
    candidates: list[tuple[dict[str, Any], str]] = []
    for collection in session.get("collections", []):
        if collection.get("role") != "mid":
            continue
        for compatibility_path in collection.get("compatibility_scan_dirs", []):
            candidates.append((collection, str(compatibility_path)))
    if len(candidates) < MID_REFERENCE_REQUIRED_SAMPLES:
        return {
            "availability": "UNAVAILABLE",
            "reason": "requires_mid_5_of_5",
            "mid_sample_count": len(candidates),
        }

    # Match formal Stage7C session calibration: a later accepted retry supersedes
    # an earlier one, so the five most recently accepted Mid samples are authority.
    selected = candidates[-MID_REFERENCE_REQUIRED_SAMPLES:]
    source_samples: list[dict[str, Any]] = []
    for collection, compatibility_path_text in selected:
        compatibility_path = Path(compatibility_path_text).resolve(strict=True)
        image_path = compatibility_path / "frames" / "frame_000000" / "image.rgb565"
        if not image_path.is_file():
            raise CollectorError(
                f"Mid compatibility sample has no authoritative image.rgb565: {image_path}"
            )
        source_samples.append(
            {
                "scan_id": str(collection["scan_id"]),
                "selected_frame_id": str(collection.get("selected_frame_id")),
                "compatibility_scan_path": str(compatibility_path),
                "image_rgb565_sha256": file_sha256(image_path),
            }
        )
    fingerprint_payload = {
        "schema_version": MID_REFERENCE_SCHEMA_VERSION,
        "reference_role": "mid",
        "source_samples": source_samples,
        "board_manifest_sha256": contracts["board_manifest_sha256"],
        "stage7a_profile_sha256": contracts["camera_sha256"],
        "stage7b_profile_sha256": contracts["stage7b_sha256"],
        "formal_stage7c_sha256": contracts["stage7c_sha256"],
    }
    return {
        "availability": "AVAILABLE",
        "source_samples": source_samples,
        "source_session_fingerprint": canonical_sha256(fingerprint_payload),
    }


def _build_mid_pose_reference(
    session_root: Path,
    contracts: dict[str, Any],
    source_state: dict[str, Any],
) -> dict[str, Any]:
    matrix, distortion = _camera_parameters(contracts["camera"])
    rotations: list[np.ndarray] = []
    for sample in source_state["source_samples"]:
        compatibility_path = Path(sample["compatibility_scan_path"])
        image_path = compatibility_path / "frames" / "frame_000000" / "image.rgb565"
        rgb = read_rgb565_le(image_path)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        observation = detect_observation_image(
            bgr,
            contracts["board"],
            min_charuco_corners=8,
            source_name=(
                f"{sample['scan_id']}/{sample['selected_frame_id']} Mid reference"
            ),
            source_sha256=sample["image_rgb565_sha256"],
        )
        rotation_camera_from_board, _, _ = solve_board_pose(
            observation, matrix, distortion
        )
        rotations.append(
            rotation_world_from_camera_from_board_pose(rotation_camera_from_board)
        )
    aggregate = robust_rotation_average(rotations)
    residuals = np.asarray(aggregate.residual_deg, dtype=np.float64)
    reference = {
        "schema_version": MID_REFERENCE_SCHEMA_VERSION,
        "created_at": utc_now(),
        "reference_role": "mid",
        "sample_count": len(rotations),
        "source_samples": source_state["source_samples"],
        "board_manifest_sha256": contracts["board_manifest_sha256"],
        "stage7a_profile_sha256": contracts["camera_sha256"],
        "stage7b_profile_sha256": contracts["stage7b_sha256"],
        "formal_stage7c_sha256": contracts["stage7c_sha256"],
        "rotation_world_from_camera_reference": aggregate.rotation.tolist(),
        "aggregation": {
            "method": (
                "formal_stage7c.robust_rotation_average: geodesic medoid "
                "initialization; MAD outlier rejection; chordal SVD mean "
                "projected to SO(3); geodesic angular residual"
            ),
            "inlier_count": int(np.count_nonzero(aggregate.inlier_mask)),
            "rejected_count": int(len(rotations) - np.count_nonzero(aggregate.inlier_mask)),
            "residual_median_deg": float(np.median(residuals)),
            "residual_p95_deg": float(np.percentile(residuals, 95)),
            "residual_max_deg": float(np.max(residuals)),
        },
        "source_session_fingerprint": source_state["source_session_fingerprint"],
    }
    analysis_root = session_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    write_json(analysis_root / "mid_pose_reference.json", reference)
    return reference


def load_or_refresh_mid_pose_reference(
    session_root: Path,
    contracts: dict[str, Any],
) -> dict[str, Any]:
    """Return a diagnostic reference; never let reference failure block collection."""
    try:
        source_state = _mid_reference_source_state(session_root, contracts)
        if source_state["availability"] != "AVAILABLE":
            return source_state
        path = session_root / "analysis" / "mid_pose_reference.json"
        if path.is_file():
            cached = read_json(path)
            if (
                cached.get("schema_version") == MID_REFERENCE_SCHEMA_VERSION
                and cached.get("source_session_fingerprint")
                == source_state["source_session_fingerprint"]
                and cached.get("sample_count") == MID_REFERENCE_REQUIRED_SAMPLES
            ):
                return {**cached, "availability": "AVAILABLE", "cache_status": "REUSED"}
        rebuilt = _build_mid_pose_reference(session_root, contracts, source_state)
        return {**rebuilt, "availability": "AVAILABLE", "cache_status": "REBUILT"}
    except Exception as exc:
        return {
            "availability": "UNAVAILABLE",
            "reason": "mid_reference_build_failed",
            "detail": str(exc),
        }


def compute_pose_reference_diagnostic(
    inspection: dict[str, Any],
    reference: dict[str, Any],
) -> dict[str, Any]:
    if reference.get("availability") != "AVAILABLE":
        return dict(reference)
    base = {
        "availability": "AVAILABLE",
        "reference_role": "mid",
        "reference_sample_count": int(reference["sample_count"]),
        "reference_path": reference.get("reference_path"),
        "reference_cache_status": reference.get("cache_status"),
        "source_session_fingerprint": reference["source_session_fingerprint"],
    }
    selected_frame_id = inspection.get("selected_frame_id")
    selected_row = next(
        (
            row
            for row in inspection.get("frames", [])
            if row.get("frame_id") == selected_frame_id
            and row.get("rotation_world_from_camera") is not None
        ),
        None,
    )
    passing_rows = [
        row
        for row in inspection.get("frames", [])
        if row.get("detector_pass")
        and row.get("rotation_world_from_camera") is not None
    ]
    if selected_row is None or not passing_rows:
        return {
            **base,
            "selected_frame_error_vs_mid_deg": None,
            "scan_robust_pose_error_vs_mid_deg": None,
            "diagnostic_status": "UNAVAILABLE",
            "reason": "no_selected_detector_pass_pose",
            "relative_orientation": None,
        }

    reference_rotation = np.asarray(
        reference["rotation_world_from_camera_reference"], dtype=np.float64
    )
    selected_rotation = np.asarray(
        selected_row["rotation_world_from_camera"], dtype=np.float64
    )
    passing_rotations = [
        np.asarray(row["rotation_world_from_camera"], dtype=np.float64)
        for row in passing_rows
    ]
    scan_aggregate = robust_rotation_average(
        passing_rotations,
        minimum_inliers=min(3, len(passing_rotations)),
    )
    selected_error = rotation_distance_deg(reference_rotation, selected_rotation)
    scan_error = rotation_distance_deg(reference_rotation, scan_aggregate.rotation)
    relative_rotation = reference_rotation.T @ scan_aggregate.rotation
    relative_rotvec_rad = cv2.Rodrigues(relative_rotation)[0].reshape(3)
    return {
        **base,
        "selected_frame_error_vs_mid_deg": float(selected_error),
        "scan_robust_pose_error_vs_mid_deg": float(scan_error),
        "diagnostic_status": pose_diagnostic_status(float(scan_error)),
        "relative_orientation": {
            "meaning": "Far scan robust pose relative to Mid reference",
            "convention": "rotvec(R_mid_reference.T @ R_far_scan_robust)",
            "coordinate_basis": "Mid-reference camera orientation basis",
            "relative_rotvec_deg": [
                float(value) for value in np.rad2deg(relative_rotvec_rad)
            ],
        },
        "scan_aggregation": {
            "method": "formal_stage7c.robust_rotation_average",
            "detector_pass_pose_count": len(passing_rotations),
            "inlier_count": int(np.count_nonzero(scan_aggregate.inlier_mask)),
            "rejected_count": int(
                len(passing_rotations) - np.count_nonzero(scan_aggregate.inlier_mask)
            ),
        },
        "diagnostic_only": True,
        "affects_ranking": False,
        "affects_acceptance": False,
        "affects_materialization": False,
    }


def safe_pose_reference_diagnostic(
    session_root: Path,
    contracts: dict[str, Any],
    inspection: dict[str, Any],
) -> dict[str, Any]:
    """Isolate all diagnostic failures from collection business behavior."""
    try:
        reference = load_or_refresh_mid_pose_reference(session_root, contracts)
        reference_path = session_root / "analysis" / "mid_pose_reference.json"
        if reference.get("availability") == "AVAILABLE":
            reference["reference_path"] = str(reference_path)
        return compute_pose_reference_diagnostic(inspection, reference)
    except Exception as exc:
        return {
            "availability": "UNAVAILABLE",
            "reason": "pose_reference_diagnostic_failed",
            "detail": str(exc),
            "diagnostic_only": True,
            "affects_ranking": False,
            "affects_acceptance": False,
            "affects_materialization": False,
        }


def board_definition_hash(manifest: dict[str, Any]) -> str:
    fields = {key: manifest.get(key) for key in EXPECTED_BOARD_FIELDS}
    return canonical_sha256(fields)


def load_contracts(args: argparse.Namespace) -> dict[str, Any]:
    board_path = args.board_manifest.resolve(strict=True)
    board_manifest = load_camera_json(board_path)
    validate_board_manifest(board_manifest, require_print_measurement=True)
    actual_fields = {key: board_manifest.get(key) for key in EXPECTED_BOARD_FIELDS}
    checks = {
        "board_fields": actual_fields == EXPECTED_BOARD_FIELDS,
        "board_manifest_sha256": file_sha256(board_path)
        == EXPECTED_BOARD_MANIFEST_SHA256,
        "board_definition_sha256": board_definition_hash(board_manifest)
        == EXPECTED_BOARD_DEFINITION_SHA256,
    }
    board_image = board_path.parent / str(board_manifest["board_image"])
    checks["board_image_sha256"] = (
        board_image.is_file()
        and file_sha256(board_image) == EXPECTED_BOARD_IMAGE_SHA256
        and board_manifest["board_sha256"] == EXPECTED_BOARD_IMAGE_SHA256
    )
    if not all(checks.values()):
        raise CollectorError(
            "board contract mismatch; refusing collection: "
            + json.dumps(checks, sort_keys=True)
        )

    camera_path = args.camera_profile.resolve(strict=True)
    camera = load_camera_json(camera_path)
    validate_calibration_v2(camera)
    stage7b_path = args.camera_tof_profile.resolve(strict=True)
    stage7b = load_frozen_camera_tof_profile(stage7b_path)
    stage7c_path = args.current_stage7c_profile.resolve(strict=True)
    stage7c = read_json(stage7c_path)
    return {
        "board_path": board_path,
        "board_manifest": board_manifest,
        "board_manifest_sha256": file_sha256(board_path),
        "board_image": board_image,
        "board": charuco_board_from_manifest(board_manifest),
        "camera_path": camera_path,
        "camera": camera,
        "camera_sha256": file_sha256(camera_path),
        "stage7b_path": stage7b_path,
        "stage7b": stage7b,
        "stage7b_sha256": file_sha256(stage7b_path),
        "stage7c_path": stage7c_path,
        "stage7c": stage7c,
        "stage7c_sha256": file_sha256(stage7c_path),
    }


def resolve_scan(args: argparse.Namespace) -> Path:
    validated = args.data_root.resolve() / "validated"
    if args.scan_id:
        candidate = validated / args.scan_id
        if not candidate.is_dir():
            raise CollectorError(f"scan_id is not present under validated: {args.scan_id}")
        return candidate
    candidates: list[tuple[str, Path]] = []
    if validated.is_dir():
        for path in validated.iterdir():
            if not path.is_dir() or not path.name.startswith("scan_"):
                continue
            manifest_path = path / "scan.json"
            try:
                manifest = read_json(manifest_path)
            except CollectorError:
                continue
            if manifest.get("frame_count") == 11:
                candidates.append((str(manifest.get("created_at", path.name)), path))
    if not candidates:
        raise CollectorError(f"no completed 11-frame scan found under {validated}")
    return max(candidates, key=lambda item: (item[0], item[1].name))[1]


def detect_raw(
    image_bgr: np.ndarray,
    board: Any,
) -> tuple[Any, Any, Any, Any]:
    if hasattr(cv2.aruco, "CharucoDetector"):
        return cv2.aruco.CharucoDetector(board).detectBoard(image_bgr)
    marker_corners, marker_ids, _ = cv2.aruco.detectMarkers(
        image_bgr, board.getDictionary()
    )
    if marker_ids is None:
        return None, None, marker_corners, marker_ids
    _, corners, ids = cv2.aruco.interpolateCornersCharuco(
        marker_corners, marker_ids, image_bgr, board
    )
    return corners, ids, marker_corners, marker_ids


def draw_preview(
    image_bgr: np.ndarray,
    *,
    marker_corners: Any,
    marker_ids: Any,
    charuco_corners: Any,
    charuco_ids: Any,
    matrix: np.ndarray,
    distortion: np.ndarray,
    pose_diagnostics: dict[str, Any] | None,
    role: str,
    direction_index: int,
    status: str,
    reason: str,
    output: Path,
) -> None:
    preview = image_bgr.copy()
    if marker_ids is not None and len(marker_ids):
        cv2.aruco.drawDetectedMarkers(preview, marker_corners, marker_ids)
    if charuco_ids is not None and charuco_corners is not None and len(charuco_ids):
        cv2.aruco.drawDetectedCornersCharuco(
            preview, charuco_corners, charuco_ids, (255, 0, 255)
        )
    if pose_diagnostics is not None:
        cv2.drawFrameAxes(
            preview,
            matrix,
            distortion,
            np.asarray(pose_diagnostics["rvec"], dtype=np.float64),
            np.asarray(pose_diagnostics["tvec"], dtype=np.float64),
            0.05,
            2,
        )
    colour = (0, 200, 0) if status == "PASS" else (0, 0, 255)
    corner_count = 0 if charuco_ids is None else len(charuco_ids)
    lines = [
        f"Stage7C {role} direction={direction_index}",
        f"corners={corner_count} {status}",
        reason[:100],
    ]
    for index, line in enumerate(lines):
        y = 24 + index * 24
        cv2.putText(
            preview, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.58,
            colour if index == 1 else (255, 255, 255), 2, cv2.LINE_AA,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), preview, [cv2.IMWRITE_PNG_COMPRESSION, 6]):
        raise CollectorError(f"OpenCV could not write preview: {output}")


def draw_best_preview(
    source_preview: Path,
    output: Path,
    *,
    role: str,
    row: dict[str, Any],
    selection_mode: str,
    detector_pass_frame_count: int,
    stationary: dict[str, Any] | None,
    review_status: str,
) -> None:
    preview = cv2.imread(str(source_preview), cv2.IMREAD_COLOR)
    if preview is None:
        raise CollectorError(f"OpenCV could not read preview: {source_preview}")
    best_of_11 = selection_mode == SELECTION_BEST_OF_11
    panel_height = 196 if best_of_11 else 142
    cv2.rectangle(preview, (0, 0), (preview.shape[1], panel_height), (0, 0, 0), -1)
    if best_of_11:
        lines = [
            f"Stage7C role={role} | best-of-11",
            f"chosen frame={row['frame_id']}",
            f"detector pass={detector_pass_frame_count}/11",
            f"corners={row['charuco_corner_count']}/24 markers={row['detected_marker_count']}/17",
            f"RMS={row['reprojection_rms_px']:.6f} px",
            f"pair delta={row['pair_delta_us']} us",
            review_status,
        ]
    else:
        assert stationary is not None
        frame_label = (
            "chosen frame" if stationary["status"] == "PASS" else "best candidate"
        )
        lines = [
            f"Stage7C {role} | stationary-best",
            f"{frame_label}={row['frame_id']}",
            (
                f"corners={row['charuco_corner_count']} "
                f"markers={row['detected_marker_count']} "
                f"RMS={row['reprojection_rms_px']:.6f}px"
            ),
            (
                f"pose p95={stationary['p95_angular_deviation_deg']:.6f}deg "
                f"max={stationary['max_angular_deviation_deg']:.6f}deg"
            ),
            review_status,
        ]
    for index, line in enumerate(lines):
        cv2.putText(
            preview,
            line,
            (10, 24 + index * 27),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (0, 210, 0) if index == len(lines) - 1 and review_status == "PASS" else (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), preview, [cv2.IMWRITE_PNG_COMPRESSION, 6]):
        raise CollectorError(f"OpenCV could not write BEST preview: {output}")


def stationary_ranking_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -int(row["charuco_corner_count"]),
        -int(row["detected_marker_count"]),
        float(row["reprojection_rms_px"]),
        abs(int(row["pair_delta_us"])),
        int(row["sequence"]),
    )


def frame_is_usable(
    *,
    detector_pass: bool,
    is_frozen_11: bool,
    direction_index: int,
    stationary_best: bool,
    best_of_11: bool = False,
) -> bool:
    return (
        detector_pass
        and is_frozen_11
        and (stationary_best or best_of_11 or direction_index == 0)
    )


def rank_stationary_frames(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        (row for row in rows if row.get("detector_pass")),
        key=stationary_ranking_key,
    )
    for rank, row in enumerate(ranked, start=1):
        row["stationary_best_rank"] = rank
    return ranked


def select_best_of_11(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Select exactly one independently passing frame without a cross-frame gate."""
    if len(rows) != 11:
        raise CollectorError(
            f"BEST_OF_11_FRAME_COUNT_MISMATCH: expected 11, got {len(rows)}"
        )
    ranking = rank_stationary_frames(rows)
    chosen = ranking[0] if ranking else None
    for row in rows:
        row["usable"] = row is chosen
        if row.get("detector_pass"):
            row["rejection_reason"] = (
                None if row is chosen else "not_selected_by_best_of_11_ranking"
            )
    return ranking, chosen


def select_stationary_best(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    if len(rows) != 11:
        raise CollectorError(
            f"STATIONARY_SCAN_FRAME_COUNT_MISMATCH: expected 11, got {len(rows)}"
        )
    stationary = evaluate_stationary_consistency(rows)
    ranking = rank_stationary_frames(rows)
    best_candidate = ranking[0] if ranking else None
    for row in rows:
        row["usable"] = False
        if row.get("detector_pass"):
            row["rejection_reason"] = stationary["failure_code"]
    chosen: dict[str, Any] | None = None
    if stationary["status"] == "PASS":
        chosen = best_candidate
        assert chosen is not None
        chosen["usable"] = True
        chosen["rejection_reason"] = None
        for row in ranking[1:]:
            row["rejection_reason"] = "not_selected_by_stationary_best_ranking"
    return stationary, ranking, chosen


def ranking_summary(
    row: dict[str, Any], *, include_stationary_diagnostic: bool = True
) -> dict[str, Any]:
    summary = {
        "rank": row["stationary_best_rank"],
        "frame_id": row["frame_id"],
        "sequence": row["sequence"],
        "direction_index": row["direction_index"],
        "charuco_corner_count": row["charuco_corner_count"],
        "detected_marker_count": row["detected_marker_count"],
        "reprojection_rms_px": row["reprojection_rms_px"],
        "pair_delta_us": row["pair_delta_us"],
    }
    if include_stationary_diagnostic:
        summary["stationary_angular_deviation_deg"] = row.get(
            "stationary_angular_deviation_deg"
        )
    return summary


def evaluate_stationary_consistency(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    passing = [row for row in rows if row.get("detector_pass")]
    base = {
        "minimum_detector_pass_frame_count": STATIONARY_MIN_VALID_FRAMES,
        "detector_pass_frame_count": len(passing),
        "p95_limit_deg": STATIONARY_P95_MAX_DEG,
        "max_limit_deg": STATIONARY_ABSOLUTE_MAX_DEG,
        "central_rotation_method": (
            "formal_stage7c.robust_rotation_average over "
            "R_world_from_camera_from_board_pose"
        ),
    }
    if len(passing) < STATIONARY_MIN_VALID_FRAMES:
        return {
            **base,
            "status": "FAIL",
            "failure_code": "STATIONARY_SCAN_INSUFFICIENT_VALID_FRAMES",
            "central_rotation_world_from_camera": None,
            "median_angular_deviation_deg": None,
            "p95_angular_deviation_deg": None,
            "max_angular_deviation_deg": None,
        }

    rotations = [
        np.asarray(row["rotation_world_from_camera"], dtype=np.float64)
        for row in passing
    ]
    central = robust_rotation_average(rotations)
    deviations = np.asarray(
        [rotation_distance_deg(central.rotation, rotation) for rotation in rotations],
        dtype=np.float64,
    )
    for row, deviation in zip(passing, deviations):
        row["stationary_angular_deviation_deg"] = float(deviation)
    median = float(np.median(deviations))
    p95 = float(np.percentile(deviations, 95))
    maximum = float(np.max(deviations))
    passed = (
        p95 <= STATIONARY_P95_MAX_DEG
        and maximum <= STATIONARY_ABSOLUTE_MAX_DEG
    )
    return {
        **base,
        "status": "PASS" if passed else "FAIL",
        "failure_code": None if passed else "STATIONARY_POSE_CONSISTENCY_FAIL",
        "central_rotation_world_from_camera": central.rotation.tolist(),
        "robust_inlier_count": int(np.count_nonzero(central.inlier_mask)),
        "robust_threshold_deg": float(central.threshold_deg),
        "median_angular_deviation_deg": median,
        "p95_angular_deviation_deg": p95,
        "max_angular_deviation_deg": maximum,
    }


def strict_review_for_row(row: dict[str, Any]) -> dict[str, bool]:
    return {
        "full_24_corners": int(row["charuco_corner_count"]) == 24,
        "all_17_markers": int(row["detected_marker_count"]) == 17,
        "rms_le_0_2_px": float(row["reprojection_rms_px"]) <= 0.2,
        "pair_delta_reasonable": (
            abs(int(row["pair_delta_us"])) <= STRICT_PAIR_DELTA_MAX_US
        ),
    }


def ranking_reason(row: dict[str, Any]) -> str:
    return (
        "lexicographic(-charuco_corner_count,-detected_marker_count,"
        "reprojection_rms_px,abs(pair_delta_us),sequence)="
        f"{stationary_ranking_key(row)}"
    )


def write_ranking_csv(
    path: Path,
    ranking: list[dict[str, Any]],
    *,
    include_stationary_diagnostic: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "rank", "frame_id", "sequence", "direction_index",
        "charuco_corner_count", "detected_marker_count",
        "reprojection_rms_px", "pair_delta_us", "source_rgb565_sha256",
    ]
    if include_stationary_diagnostic:
        fields.insert(-1, "stationary_angular_deviation_deg")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in ranking:
            writer.writerow(
                {
                    "rank": row["stationary_best_rank"],
                    **{field: row.get(field) for field in fields if field != "rank"},
                }
            )


def source_png_evidence(
    args: argparse.Namespace,
    scan_id: str,
    frame_id: str,
    decoded_rgb: np.ndarray,
) -> dict[str, Any]:
    path = args.data_root / "results" / scan_id / "logs" / frame_id / "source_rgb.png"
    if not path.is_file():
        return {"path": None, "sha256": None, "matches_raw_rgb565_decode": None}
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    matches = (
        bgr is not None
        and bgr.shape == (360, 640, 3)
        and np.array_equal(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), decoded_rgb)
    )
    return {
        "path": str(path.resolve()),
        "sha256": file_sha256(path),
        "matches_raw_rgb565_decode": bool(matches),
    }


def inspect_scan(
    args: argparse.Namespace,
    contracts: dict[str, Any],
    scan_dir: Path,
    output_root: Path,
) -> tuple[dict[str, Any], Any]:
    best_of_11 = bool(args.best_of_11)
    best_frame_mode = bool(args.stationary_best or best_of_11)
    bundle = load_profile_bundle(args.profile_bundle, validate_model_hashes=False)
    validated = validate_scan(scan_dir, bundle)
    matrix, distortion = _camera_parameters(contracts["camera"])
    rows: list[dict[str, Any]] = []
    for frame in validated.frames:
        frame_id = str(frame.frame["frame_id"])
        direction = int(frame.frame["sequence"])
        raw_sha = file_sha256(frame.image_path)
        rgb = read_rgb565_le(frame.image_path)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        corners, charuco_ids, marker_corners, marker_ids = detect_raw(
            bgr, contracts["board"]
        )
        detected_ids = (
            [] if marker_ids is None
            else [int(value) for value in np.asarray(marker_ids).reshape(-1)]
        )
        charuco_id_values = (
            [] if charuco_ids is None
            else [int(value) for value in np.asarray(charuco_ids).reshape(-1)]
        )
        detector_pass = False
        pose_diagnostics: dict[str, Any] | None = None
        rotation_camera_from_board: np.ndarray | None = None
        rotation_world_from_camera: np.ndarray | None = None
        rejection = "no_board_or_fewer_than_8_charuco_corners"
        try:
            observation = detect_observation_image(
                bgr,
                contracts["board"],
                min_charuco_corners=8,
                source_name=f"{validated.scan['scan_id']}/{frame_id}",
                source_sha256=raw_sha,
            )
            rotation_camera_from_board, _, pose_diagnostics = solve_board_pose(
                observation, matrix, distortion
            )
            rotation_world_from_camera = rotation_world_from_camera_from_board_pose(
                rotation_camera_from_board
            )
            if pose_diagnostics["rms_px"] <= 0.5:
                detector_pass = True
                rejection = "pass"
            else:
                rejection = "reprojection_rms_exceeds_0.5_px"
        except (CameraCalibrationError, CameraWorldError) as exc:
            rejection = str(exc)

        is_frozen_11 = int(validated.scan["frame_count"]) == 11
        usable = frame_is_usable(
            detector_pass=detector_pass,
            is_frozen_11=is_frozen_11,
            direction_index=direction,
            stationary_best=bool(args.stationary_best),
            best_of_11=best_of_11,
        )
        if detector_pass and not is_frozen_11:
            rejection = "detector_pass_but_not_an_11_direction_scan"
        elif detector_pass and direction != 0 and not best_frame_mode:
            rejection = "direction_not_zero_after_vehicle_rotation"
        elif detector_pass and args.stationary_best:
            rejection = "stationary_candidate_pending_scan_gate"
        elif detector_pass and best_of_11:
            rejection = "best_of_11_candidate_pending_individual_quality_ranking"
        status = "PASS" if usable else "FAIL"
        preview = (
            output_root / "detection_preview"
            / f"{validated.scan['scan_id']}_{frame_id}.png"
        )
        draw_preview(
            bgr,
            marker_corners=marker_corners,
            marker_ids=marker_ids,
            charuco_corners=corners,
            charuco_ids=charuco_ids,
            matrix=matrix,
            distortion=distortion,
            pose_diagnostics=pose_diagnostics,
            role=args.role,
            direction_index=direction,
            status=status,
            reason=rejection,
            output=preview,
        )
        png_evidence = source_png_evidence(
            args, validated.scan["scan_id"], frame_id, rgb
        )
        rows.append(
            {
                "scan_id": validated.scan["scan_id"],
                "frame_id": frame_id,
                "sequence": direction,
                "direction_index": direction,
                "direction_source": (
                    "frame.sequence under frozen rehab_environment_scan "
                    "11-direction contract"
                ),
                "camera_timestamp_us": frame.frame["camera_timestamp_us"],
                "tof_timestamp_us": frame.frame["tof_timestamp_us"],
                "pair_delta_us": frame.frame["pair_delta_us"],
                "image_path": str(frame.image_path.resolve()),
                "source_rgb565_sha256": raw_sha,
                "decoded_rgb888_sha256": hashlib.sha256(rgb.tobytes()).hexdigest(),
                "pc_source_rgb_png": png_evidence,
                "image_dimensions": [640, 360],
                "pixel_format": "RGB565_LE",
                "detected_marker_ids": detected_ids,
                "detected_marker_count": len(detected_ids),
                "charuco_corner_ids": charuco_id_values,
                "charuco_corner_count": len(charuco_id_values),
                "reprojection_rms_px": (
                    None if pose_diagnostics is None
                    else pose_diagnostics["rms_px"]
                ),
                "pnp_success": pose_diagnostics is not None,
                "rvec": (
                    None if pose_diagnostics is None else pose_diagnostics["rvec"]
                ),
                "rotation_camera_from_board": (
                    None
                    if rotation_camera_from_board is None
                    else rotation_camera_from_board.tolist()
                ),
                "rotation_world_from_camera": (
                    None
                    if rotation_world_from_camera is None
                    else rotation_world_from_camera.tolist()
                ),
                "tvec_diagnostic_only": (
                    None if pose_diagnostics is None else pose_diagnostics["tvec"]
                ),
                "detector_pass": detector_pass,
                "usable": usable,
                "rejection_reason": None if usable else rejection,
                "preview_path": str(preview.resolve()),
            }
        )

    stationary: dict[str, Any] | None = None
    ranking: list[dict[str, Any]] = []
    chosen: dict[str, Any] | None = None
    strict_review: dict[str, bool] | None = None
    best_preview: Path | None = None
    if args.stationary_best:
        stationary, ranking, chosen = select_stationary_best(rows)
    elif best_of_11:
        ranking, chosen = select_best_of_11(rows)
    if best_frame_mode:
        best_candidate = ranking[0] if ranking else None
        if best_candidate is not None:
            strict_review = strict_review_for_row(best_candidate)
            review_status = (
                "STATIONARY_FAIL"
                if stationary is not None and stationary["status"] != "PASS"
                else "PASS"
                if all(strict_review.values())
                else "GPT_REVIEW_REQUIRED"
            )
            best_preview = (
                output_root / "detection_preview"
                / f"{validated.scan['scan_id']}_BEST_{best_candidate['frame_id']}.png"
            )
            draw_best_preview(
                Path(best_candidate["preview_path"]),
                best_preview,
                role=args.role,
                row=best_candidate,
                selection_mode=(
                    SELECTION_BEST_OF_11 if best_of_11 else SELECTION_STATIONARY_BEST
                ),
                detector_pass_frame_count=sum(
                    bool(row["detector_pass"]) for row in rows
                ),
                stationary=stationary,
                review_status=review_status,
            )
    report = {
        "schema_version": "stage7c_scan_collection_inspection.v1",
        "tool_version": TOOL_VERSION,
        "created_at": utc_now(),
        "dry_run": bool(args.dry_run),
        "role": args.role,
        "selection_mode": (
            SELECTION_BEST_OF_11
            if best_of_11
            else SELECTION_STATIONARY_BEST
            if args.stationary_best
            else SELECTION_DIRECTION0
        ),
        "cross_frame_pose_gate": (
            CROSS_FRAME_POSE_GATE_DISABLED if best_of_11 else None
        ),
        "vehicle_motion_assumption": (
            VEHICLE_MOTION_ASSUMPTION if best_of_11 else None
        ),
        "scan_id": validated.scan["scan_id"],
        "scan_dir": str(scan_dir.resolve()),
        "scan_manifest_sha256": file_sha256(scan_dir / "scan.json"),
        "frame_count": validated.scan["frame_count"],
        "camera_resolution": [640, 360],
        "authoritative_detection_source": "validated image.rgb565 decoded as RGB565_LE",
        "detector_pass_frame_count": sum(
            bool(row["detector_pass"]) for row in rows
        ),
        "failed_frame_count": sum(
            not bool(row["detector_pass"]) for row in rows
        ),
        "usable_frame_count": sum(bool(row["usable"]) for row in rows),
        "stationary_consistency": stationary,
        "ranking": ranking,
        "top_3_ranking": [
            ranking_summary(
                row, include_stationary_diagnostic=not best_of_11
            )
            for row in ranking[:3]
        ],
        "selected_frame_id": None if chosen is None else chosen["frame_id"],
        "selected_sequence": None if chosen is None else chosen["sequence"],
        "ranking_reason": None if chosen is None else ranking_reason(chosen),
        "best_candidate_frame_id": (
            None if not ranking else ranking[0]["frame_id"]
        ),
        "best_candidate_sequence": None if not ranking else ranking[0]["sequence"],
        "best_candidate_ranking_reason": (
            None if not ranking else ranking_reason(ranking[0])
        ),
        "strict_review": strict_review,
        "review_status": (
            None
            if strict_review is None
            else "STATIONARY_FAIL"
            if stationary is not None and stationary["status"] != "PASS"
            else "PASS"
            if all(strict_review.values())
            else "GPT_REVIEW_REQUIRED"
        ),
        "hard_gate_status": (
            "PASS"
            if chosen is not None or (
                not best_frame_mode and any(bool(row["usable"]) for row in rows)
            )
            else "NO_VALID_CHARUCO_FRAME"
            if best_of_11
            else stationary["failure_code"]
            if stationary is not None
            else "NO_USABLE_FRAME"
        ),
        "best_preview_path": (
            None if best_preview is None else str(best_preview.resolve())
        ),
        "frames": rows,
    }
    if args.role == "far":
        report["pose_reference"] = safe_pose_reference_diagnostic(
            args.session_root.resolve(), contracts, report
        )
    return report, validated


def contract_record(contracts: dict[str, Any]) -> dict[str, Any]:
    return {
        "board": {
            "board_id": contracts["board_manifest"]["board_id"],
            "definition_sha256": EXPECTED_BOARD_DEFINITION_SHA256,
            "manifest_path": str(contracts["board_path"]),
            "manifest_sha256": EXPECTED_BOARD_MANIFEST_SHA256,
            "image_path": str(contracts["board_image"]),
            "image_sha256": EXPECTED_BOARD_IMAGE_SHA256,
            **EXPECTED_BOARD_FIELDS,
            "overall_active_width_mm": 175.0,
            "overall_active_height_mm": 125.0,
            "white_border_required": False,
            "printed_on_a4_with_white_page_margin": True,
        },
        "stage7a": {
            "path": str(contracts["camera_path"]),
            "profile_id": contracts["camera"]["profile_id"],
            "sha256": contracts["camera_sha256"],
        },
        "stage7b": {
            "path": str(contracts["stage7b_path"]),
            "profile_id": contracts["stage7b"]["profile_id"],
            "sha256": contracts["stage7b_sha256"],
        },
        "current_stage7c_record_only": {
            "path": str(contracts["stage7c_path"]),
            "profile_id": contracts["stage7c"].get("profile_id"),
            "sha256": contracts["stage7c_sha256"],
        },
        "camera_resolution": [640, 360],
        "pixel_format": "RGB565_LE",
    }


def copy_or_link(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise CollectorError(f"destination already exists: {destination}")
    try:
        os.link(source, destination)
        return "hardlink"
    except OSError:
        shutil.copy2(source, destination)
        return "copy"


def backup_existing_direction0_session(
    session_root: Path,
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    root = session_root.resolve()
    if not root.exists():
        return {"status": "NO_EXISTING_SESSION_TO_BACKUP", "source": str(root)}
    session_path = root / "session.json"
    if not session_path.is_file():
        raise CollectorError(
            f"existing session root has no session.json; refusing automatic move: {root}"
        )
    session = read_json(session_path)
    mode = session.get("collection_policy", {}).get("selection_mode")
    if mode in {SELECTION_STATIONARY_BEST, SELECTION_BEST_OF_11}:
        return {
            "status": "ALREADY_BEST_FRAME_SESSION",
            "selection_mode": mode,
            "source": str(root),
        }
    accepted = session.get("collection_policy", {}).get("accepted_direction_index")
    if accepted != 0:
        raise CollectorError(
            "existing session is neither stationary-best nor the legacy direction-0 session"
        )

    stamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    target = root.with_name(f"{root.name}_direction0_backup_{stamp}")
    if target.exists():
        raise CollectorError(f"backup target already exists: {target}")
    files = sorted(path for path in root.rglob("*") if path.is_file())
    directories = sorted(path for path in root.rglob("*") if path.is_dir())
    key_files = [
        path
        for path in files
        if path.name == "session.json"
        or path.name == "scan.json"
        or "collector" in path.name
        or (path.parent.name == "manifest" and "scan" in path.name)
    ]
    before_hashes = {
        str(path.relative_to(root)): file_sha256(path) for path in key_files
    }
    root.rename(target)
    if root.exists() or not target.is_dir():
        raise CollectorError("session backup rename verification failed")
    after_files = sorted(path for path in target.rglob("*") if path.is_file())
    after_directories = sorted(path for path in target.rglob("*") if path.is_dir())
    after_hashes = {
        str(path.relative_to(target)): file_sha256(path)
        for path in after_files
        if path.name == "session.json"
        or path.name == "scan.json"
        or "collector" in path.name
        or (path.parent.name == "manifest" and "scan" in path.name)
    }
    if (
        len(files) != len(after_files)
        or len(directories) != len(after_directories)
        or before_hashes != after_hashes
    ):
        raise CollectorError("session backup inventory/hash verification failed")
    return {
        "status": "BACKUP_COMPLETE",
        "source": str(root),
        "target": str(target),
        "source_exists_after": False,
        "before": {
            "directory_count": len(directories),
            "file_count": len(files),
            "key_sha256": before_hashes,
        },
        "after": {
            "directory_count": len(after_directories),
            "file_count": len(after_files),
            "key_sha256": after_hashes,
        },
    }


def load_or_create_session(
    session_root: Path,
    contracts: dict[str, Any],
    *,
    selection_mode: str = SELECTION_DIRECTION0,
) -> dict[str, Any]:
    session_root.mkdir(parents=True, exist_ok=True)
    for name in (
        "board", "near", "mid", "far", "detection_preview", "manifest",
        "candidate", "analysis", "logs",
    ):
        (session_root / name).mkdir(exist_ok=True)
    path = session_root / "session.json"
    current_contract = contract_record(contracts)
    if path.is_file():
        value = read_json(path)
        if value.get("contract") != current_contract:
            raise CollectorError(
                "session calibration/board contract changed; start a new session"
            )
        existing_mode = value.get("collection_policy", {}).get(
            "selection_mode", SELECTION_DIRECTION0
        )
        if existing_mode != selection_mode:
            empty_counts = value.get("usable_counts") == {
                "near": 0, "mid": 0, "far": 0,
            }
            safe_best_of_11_upgrade = (
                existing_mode == SELECTION_STATIONARY_BEST
                and selection_mode == SELECTION_BEST_OF_11
                and value.get("collections") == []
                and empty_counts
            )
            if not safe_best_of_11_upgrade:
                raise CollectorError(
                    "session selection mode differs; use the matching session or "
                    "an explicit separate --session-root"
                )
            value["collection_policy"].update(
                {
                    "selection_mode": SELECTION_BEST_OF_11,
                    "reason": (
                        "one deterministic best independently passing frame from "
                        "each 11-frame scan"
                    ),
                    "cross_frame_pose_gate": CROSS_FRAME_POSE_GATE_DISABLED,
                    "vehicle_motion_assumption": VEHICLE_MOTION_ASSUMPTION,
                }
            )
            value["updated_at"] = utc_now()
            write_json(path, value)
        return value
    copy_or_link(contracts["board_path"], session_root / "board" / "board_manifest.json")
    copy_or_link(contracts["board_image"], session_root / "board" / "board.png")
    value = {
        "schema_version": "stage7c_recalibration_session.v1",
        "session_id": "stage7c_recalibration_20260813",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "contract": current_contract,
        "collection_policy": {
            "required_roles": ["near", "mid", "far"],
            "required_usable_frames_per_role": 5,
            "required_total_usable_frames": 15,
            "accepted_direction_index": (
                0 if selection_mode == SELECTION_DIRECTION0 else None
            ),
            "reason": (
                "frame 0 is captured before rotation; frames 1..10 follow vehicle "
                "CCW rotation and cannot constrain the frozen world front"
                if selection_mode == SELECTION_DIRECTION0
                else "one deterministic best frame from each stationary 11-frame scan"
                if selection_mode == SELECTION_STATIONARY_BEST
                else (
                    "one deterministic best independently passing frame from "
                    "each 11-frame scan"
                )
            ),
        },
        "collections": [],
        "usable_counts": {"near": 0, "mid": 0, "far": 0},
        "candidate": None,
    }
    if selection_mode != SELECTION_DIRECTION0:
        value["collection_policy"]["selection_mode"] = selection_mode
        if selection_mode == SELECTION_BEST_OF_11:
            value["collection_policy"].update(
                {
                    "cross_frame_pose_gate": CROSS_FRAME_POSE_GATE_DISABLED,
                    "vehicle_motion_assumption": VEHICLE_MOTION_ASSUMPTION,
                }
            )
        value["status"] = {
            "near": "0/5",
            "mid": "0/5",
            "far": "0/5",
            "total": "0/15",
        }
    write_json(path, value)
    return value


def reject_duplicate_scan_id(session: dict[str, Any], scan_id: str) -> None:
    if any(item["scan_id"] == scan_id for item in session["collections"]):
        raise CollectorError(f"DUPLICATE_SCAN_ID_REJECTED: {scan_id}")


def archive_and_collect(
    args: argparse.Namespace,
    contracts: dict[str, Any],
    inspection: dict[str, Any],
    validated: Any,
) -> dict[str, Any]:
    selection_mode = (
        SELECTION_BEST_OF_11
        if args.best_of_11
        else SELECTION_STATIONARY_BEST
        if args.stationary_best
        else SELECTION_DIRECTION0
    )
    best_frame_mode = selection_mode != SELECTION_DIRECTION0
    session = load_or_create_session(
        args.session_root, contracts, selection_mode=selection_mode
    )
    reject_duplicate_scan_id(session, inspection["scan_id"])
    session_root = args.session_root.resolve()
    source_scan = Path(inspection["scan_dir"])
    manifest_archive = session_root / "manifest" / f"{inspection['scan_id']}_scan.json"
    copy_or_link(source_scan / "scan.json", manifest_archive)
    usable_rows = [row for row in inspection["frames"] if row["usable"]]
    if best_frame_mode and len(usable_rows) != 1:
        raise CollectorError(
            f"BEST_FRAME_MATERIALIZATION_REQUIRES_EXACTLY_ONE: got {len(usable_rows)}"
        )
    compatibility_scans: list[str] = []
    copy_methods: list[dict[str, str]] = []
    for row in usable_rows:
        source_frame = source_scan / "frames" / row["frame_id"]
        compat_id = f"stage7c_{args.role}_{inspection['scan_id']}"
        compat_root = session_root / args.role / compat_id
        compat_frame = compat_root / "frames" / "frame_000000"
        compat_frame.mkdir(parents=True, exist_ok=False)
        if best_frame_mode:
            frame_manifest = read_json(source_frame / "frame.json")
            frame_manifest["frame_id"] = "frame_000000"
            frame_manifest["sequence"] = 0
            tof_manifest = read_json(source_frame / "tof.json")
            tof_manifest["sequence"] = 0
            write_json(compat_frame / "frame.json", frame_manifest)
            write_json(compat_frame / "tof.json", tof_manifest)
            copy_methods.extend(
                [
                    {"file": "frame.json", "method": "generated_compatibility_manifest"},
                    {"file": "tof.json", "method": "generated_compatibility_manifest"},
                ]
            )
            method = copy_or_link(
                source_frame / "image.rgb565", compat_frame / "image.rgb565"
            )
            copy_methods.append({"file": "image.rgb565", "method": method})
        else:
            for leaf in ("frame.json", "tof.json", "image.rgb565"):
                method = copy_or_link(source_frame / leaf, compat_frame / leaf)
                copy_methods.append({"file": leaf, "method": method})
        synthetic_manifest = dict(validated.scan)
        synthetic_manifest.update(
            {
                "scan_id": compat_id,
                "frame_count": 1,
                "capture_mode": "single_frame",
                "notes": (
                    f"Stage7C PC-only compatibility view of {inspection['scan_id']} "
                    f"{row['frame_id']}; selection={selection_mode}; source raw RGB565 "
                    "is hardlinked/copied unchanged"
                ),
            }
        )
        write_json(compat_root / "scan.json", synthetic_manifest)
        validate_scan(
            compat_root,
            load_profile_bundle(args.profile_bundle, validate_model_hashes=False),
        )
        compatibility_scans.append(str(compat_root.resolve()))
        for leaf in ("frame.json", "tof.json"):
            copy_or_link(
                source_frame / leaf,
                session_root / "manifest"
                / f"{inspection['scan_id']}_{row['frame_id']}_{leaf}",
            )
    record = {
        "scan_id": inspection["scan_id"],
        "role": args.role,
        "collected_at": utc_now(),
        "source_scan_dir": inspection["scan_dir"],
        "scan_manifest_sha256": inspection["scan_manifest_sha256"],
        "usable_frame_count": len(usable_rows),
        "usable_frames": usable_rows,
        "compatibility_scan_dirs": compatibility_scans,
        "materialization": copy_methods,
    }
    if best_frame_mode:
        selection_manifest = {
            "schema_version": "stage7c_scan_selection.v1",
            "selection_mode": selection_mode,
            "source_scan_id": inspection["scan_id"],
            "selected_frame_id": inspection.get("selected_frame_id"),
            "selected_sequence": inspection.get("selected_sequence"),
            "ranking_reason": inspection.get("ranking_reason"),
            "detector_pass_frame_count": inspection["detector_pass_frame_count"],
            "failed_frame_count": inspection["failed_frame_count"],
            "strict_review": inspection.get("strict_review"),
        }
        if selection_mode == SELECTION_STATIONARY_BEST:
            selection_manifest["stationary_consistency"] = inspection.get(
                "stationary_consistency"
            )
        else:
            selection_manifest.update(
                {
                    "cross_frame_pose_gate": CROSS_FRAME_POSE_GATE_DISABLED,
                    "vehicle_motion_assumption": VEHICLE_MOTION_ASSUMPTION,
                }
            )
        selection_manifest_path = (
            session_root / "manifest" / f"{inspection['scan_id']}_selection.json"
        )
        write_json(selection_manifest_path, selection_manifest)
        record.update(selection_manifest)
        record["selection_manifest"] = str(selection_manifest_path.resolve())
    session["collections"].append(record)
    session["usable_counts"][args.role] += len(usable_rows)
    if best_frame_mode:
        session["status"] = {
            **{
                role: f"{session['usable_counts'][role]}/5"
                for role in ("near", "mid", "far")
            },
            "total": f"{sum(session['usable_counts'].values())}/15",
        }
    session["updated_at"] = utc_now()
    write_json(session_root / "session.json", session)
    return record


def calibrate_session(args: argparse.Namespace, contracts: dict[str, Any]) -> dict[str, Any]:
    session_path = args.session_root / "session.json"
    selection_mode = SELECTION_DIRECTION0
    if session_path.is_file():
        selection_mode = read_json(session_path).get("collection_policy", {}).get(
            "selection_mode", SELECTION_DIRECTION0
        )
    session = load_or_create_session(
        args.session_root, contracts, selection_mode=selection_mode
    )
    selected: dict[str, list[str]] = {}
    for role in ROLE_TO_FORMAL:
        paths = [
            path
            for collection in session["collections"]
            if collection["role"] == role
            for path in collection["compatibility_scan_dirs"]
        ]
        if len(paths) < 5:
            raise CollectorError(
                f"role {role} has {len(paths)} usable frames; 5 are required"
            )
        # A later retry is expected to supersede an earlier orientation
        # outlier, so use the five most recently collected passing frames.
        selected[role] = paths[-5:]

    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest_path = (
        args.session_root / "manifest"
        / f"camera_world_capture_{run_stamp}.v1.json"
    )
    manifest = manifest_template(
        manifest_path,
        camera_profile=contracts["camera_path"],
        camera_tof_profile=contracts["stage7b_path"],
        board_manifest=contracts["board_path"],
    )
    manifest["capture_id"] = "stage7c_remeasured_20260813"
    collected_modes = {
        collection.get("selection_mode", SELECTION_DIRECTION0)
        for collection in session["collections"]
    }
    for pose in manifest["poses"]:
        role = pose["role"].removeprefix("world_front_")
        pose.pop("scan_dir")
        pose["scan_dirs"] = selected[role]
        pose["approximate_distance_mm"] = ROLE_DISTANCE_MM[role]
        pose["notes"] = (
            "one stationary-best frame extracted from each of five independent "
            "validated 11-frame scans"
            if collected_modes == {SELECTION_STATIONARY_BEST}
            else (
                "one independently quality-gated best frame extracted from each "
                "of five validated 11-frame scans"
                if collected_modes == {SELECTION_BEST_OF_11}
                else "direction 0 extracted from five independent frozen 11-direction scans"
            )
        )
    write_json(manifest_path, manifest)

    original_output = args.session_root / "candidate" / f"original_calibrator_{run_stamp}"
    report = calibrate_capture(
        manifest_path,
        contracts["camera_path"],
        contracts["stage7b_path"],
        contracts["board_path"],
        original_output,
        profile_bundle_path=args.profile_bundle,
    )
    candidate_path = args.session_root / "candidate" / "camera_world_pose_remeasured.v1.json"
    if candidate_path.exists():
        raise CollectorError(f"candidate already exists: {candidate_path}")
    if report["status"] == "PASS":
        shutil.copy2(original_output / "camera_world_pose.v1.json", candidate_path)
    orientation = report.get("orientation_summary", {})
    quality = {
        "schema_version": "stage7c_candidate_quality.v1",
        "created_at": utc_now(),
        "candidate_only": True,
        "formal_stage7c_overwritten": False,
        "status": report["status"],
        "candidate_path": str(candidate_path.resolve()) if candidate_path.exists() else None,
        "original_calibrator_output": str(original_output.resolve()),
        "capture_summary": report.get("capture_summary"),
        "gates": report.get("gates"),
        "orientation_inlier_residual": orientation.get("inlier_residual"),
        "near_mid_far_pose_mean_pairwise": orientation.get("pose_mean_pairwise"),
        "near_mid_far_max_pairwise_deg": orientation.get("max_pose_mean_pairwise_deg"),
        "tof_plane_summary": report.get("tof_plane_summary"),
    }
    quality_path = args.session_root / "analysis" / "candidate_quality.json"
    write_json(quality_path, quality)
    session["candidate"] = {
        "status": report["status"],
        "path": str(candidate_path.resolve()) if candidate_path.exists() else None,
        "quality_path": str(quality_path.resolve()),
        "generated_at": utc_now(),
    }
    session["updated_at"] = utc_now()
    write_json(args.session_root / "session.json", session)
    return quality


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PC-only Stage7C collector for frozen 11-direction scans"
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--scan-id")
    source.add_argument("--latest", action="store_true")
    parser.add_argument("--role", choices=tuple(ROLE_TO_FORMAL))
    parser.add_argument("--dry-run", action="store_true")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--stationary-best",
        action="store_true",
        help="validate one stationary 11-frame scan and materialize only its best frame",
    )
    selection.add_argument(
        "--best-of-11",
        action="store_true",
        help=(
            "independently quality-gate all 11 frames and materialize only the "
            "deterministic best passing frame"
        ),
    )
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--session-root", type=Path, default=DEFAULT_SESSION_ROOT)
    parser.add_argument("--dry-run-output", type=Path)
    parser.add_argument("--board-manifest", type=Path, default=DEFAULT_BOARD_MANIFEST)
    parser.add_argument("--camera-profile", type=Path, default=DEFAULT_CAMERA_PROFILE)
    parser.add_argument(
        "--camera-tof-profile", type=Path, default=FROZEN_CAMERA_TOF_PROFILE
    )
    parser.add_argument(
        "--current-stage7c-profile", type=Path, default=DEFAULT_STAGE7C_PROFILE
    )
    parser.add_argument("--profile-bundle", type=Path, default=DEFAULT_PROFILE_BUNDLE)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        best_frame_mode = bool(args.stationary_best or args.best_of_11)
        backup_result: dict[str, Any] | None = None
        if args.calibrate:
            if (
                args.scan_id
                or args.latest
                or args.role
                or args.dry_run
                or best_frame_mode
            ):
                raise CollectorError(
                    "--calibrate is a session action and cannot be combined with "
                    "scan/role/dry-run/best-frame selection"
                )
        else:
            if not (args.scan_id or args.latest):
                raise CollectorError("select one scan with --scan-id or --latest")
            if args.role is None:
                raise CollectorError("--role near|mid|far is required")
        if best_frame_mode and not args.dry_run:
            backup_result = backup_existing_direction0_session(args.session_root)
        contracts = load_contracts(args)
        if args.calibrate:
            result = calibrate_session(args, contracts)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "PASS" else 8

        scan_dir = resolve_scan(args)
        output_root = (
            args.dry_run_output.resolve()
            if args.dry_run_output is not None
            else args.session_root.resolve()
        )
        inspection, validated = inspect_scan(
            args, contracts, scan_dir, output_root
        )
        output_root.mkdir(parents=True, exist_ok=True)
        log_path = output_root / "logs" / f"{inspection['scan_id']}_collector.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ranking_path: Path | None = None
        if best_frame_mode:
            ranking_path = (
                output_root / "logs" / f"{inspection['scan_id']}_ranking.csv"
            )
            write_ranking_csv(
                ranking_path,
                inspection["ranking"],
                include_stationary_diagnostic=bool(args.stationary_best),
            )
            inspection["ranking_csv"] = str(ranking_path.resolve())
        write_json(log_path, inspection)
        stationary_failed = bool(
            args.stationary_best
            and inspection["stationary_consistency"]["status"] != "PASS"
        )
        no_valid_best_frame = bool(
            args.best_of_11 and inspection["detector_pass_frame_count"] == 0
        )
        if not args.dry_run:
            if inspection["frame_count"] != 11:
                raise CollectorError("live collection requires a frozen 11-frame scan")
            if stationary_failed or no_valid_best_frame:
                record = None
            else:
                record = archive_and_collect(
                    args, contracts, inspection, validated
                )
        else:
            record = None
        if args.stationary_best:
            status = (
                inspection["stationary_consistency"]["failure_code"]
                if stationary_failed
                else inspection["review_status"]
            )
        elif args.best_of_11:
            status = (
                "NO_VALID_CHARUCO_FRAME"
                if no_valid_best_frame
                else inspection["review_status"]
            )
        else:
            status = (
                "DETECTOR_PASS"
                if args.dry_run and inspection["detector_pass_frame_count"]
                else "NO_BOARD"
                if args.dry_run
                else "PASS"
                if inspection["usable_frame_count"]
                else "NO_USABLE_FRAME"
            )
        summary = {
            "status": status,
            "dry_run": args.dry_run,
            "scan_id": inspection["scan_id"],
            "role": args.role,
            "selection_mode": inspection["selection_mode"],
            "hard_gate_status": inspection["hard_gate_status"],
            "frame_count": inspection["frame_count"],
            "detector_pass_frame_count": inspection["detector_pass_frame_count"],
            "failed_frame_count": inspection["failed_frame_count"],
            "usable_frame_count": inspection["usable_frame_count"],
            "stationary_consistency": inspection["stationary_consistency"],
            "cross_frame_pose_gate": inspection["cross_frame_pose_gate"],
            "vehicle_motion_assumption": inspection["vehicle_motion_assumption"],
            "selected_frame_id": inspection["selected_frame_id"],
            "selected_sequence": inspection["selected_sequence"],
            "best_candidate_frame_id": inspection["best_candidate_frame_id"],
            "best_candidate_sequence": inspection["best_candidate_sequence"],
            "top_3_ranking": inspection["top_3_ranking"],
            "ranking_reason": inspection["ranking_reason"],
            "strict_review": inspection["strict_review"],
            "selected_frame": (
                None
                if inspection["selected_frame_id"] is None
                else next(
                    ranking_summary(
                        row,
                        include_stationary_diagnostic=bool(args.stationary_best),
                    )
                    for row in inspection["ranking"]
                    if row["frame_id"] == inspection["selected_frame_id"]
                )
            ),
            "best_preview": inspection["best_preview_path"],
            "ranking_csv": (
                None if ranking_path is None else str(ranking_path.resolve())
            ),
            "log": str(log_path.resolve()),
            "old_session_backup": backup_result,
            "collection": record,
        }
        if args.role == "far":
            summary["pose_reference"] = inspection.get("pose_reference")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if args.role == "far":
            pose_reference = inspection.get("pose_reference", {})
            print("\nPOSE REFERENCE - DIAGNOSTIC ONLY")
            if pose_reference.get("availability") == "AVAILABLE":
                print(
                    f"Mid reference: {pose_reference['reference_sample_count']} samples"
                )
                print(
                    "Selected frame vs Mid: "
                    f"{pose_reference['selected_frame_error_vs_mid_deg']:.6f} deg"
                )
                print(
                    "Scan robust mean vs Mid: "
                    f"{pose_reference['scan_robust_pose_error_vs_mid_deg']:.6f} deg"
                )
                print(f"Diagnostic: {pose_reference['diagnostic_status']}")
            else:
                print("Mid reference: UNAVAILABLE")
                print(f"Reason: {pose_reference.get('reason', 'unknown')}")
        return 8 if stationary_failed or no_valid_best_frame else 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "ERROR", "error": str(exc), "type": type(exc).__name__},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
