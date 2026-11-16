from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np


WORKSPACE = Path(__file__).resolve().parent
REGISTRY_PATH = WORKSPACE / "observations" / "registry.json"
HEIGHT_PATH = WORKSPACE / "camera_height.json"
FIXED_BOARD_HEIGHT_PATH = WORKSPACE / "fixed_board_height.json"
DIAGNOSTICS = WORKSPACE / "diagnostics"
VALIDATION = WORKSPACE / "validation"
CANDIDATE = WORKSPACE / "candidate" / "camera_world_pose.stage3a_candidate.v1.json"
STAGE2_BUNDLE = Path(
    r"C:\ec\_calibration_workspaces\stage2_final_bundle_20260818_01"
    r"\calibration_bundle.stage2_final_candidate.v1.json"
)
STAGE2_SHA256 = "63c974cd2ff64dd0f135d47d8c92eb170d06658cb315563fb93588862ba8b057"
OLD_STAGE7C = Path(
    r"C:\calibration\stage7c_world_result_robustfix_20260724_044035"
    r"\camera_world_pose.v1.json"
)
OLD_STAGE7C_SHA256 = "5c730171f850ff3e05ab777eecca9849714aa20376c6d1f001da2b378ad24619"
SCAN_ROOT = Path(r"C:\ec\_deltar_scan_service_data\validated")
WIDTH, HEIGHT = 640, 360
FRAME_COUNT = 11
FRAME_IDS = tuple(f"frame_{index:06d}" for index in range(FRAME_COUNT))
BOARD_MODE = "FIXED_VERTICAL_BOARD"
CAPTURE_MODE = "VEHICLE_MULTI_DISTANCE"
PRIMARY_POSE_IDS = ("W01", "W02", "W03")
POSE_DISTANCE_ROLES = {"W01": "near", "W02": "mid", "W03": "far", "W04": "reposition_holdout"}

BOARD = {
    "family": "opencv_charuco",
    "dictionary": "DICT_5X5_100",
    "squares_x": 7,
    "squares_y": 5,
    "square_length_mm": 25.0,
    "marker_length_mm": 18.0,
    "active_width_mm": 175.0,
    "active_height_mm": 125.0,
}

# Reused exactly from the mature Stage7C semantics. OpenCV board +X is printed
# right, +Y is printed down, and +Z points through the board away from vehicle.
R_WORLD_FROM_BOARD = np.asarray(
    [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]],
    dtype=np.float64,
)


class Stage3AError(RuntimeError):
    pass


@dataclass
class FrameObservation:
    pose_id: str
    frame_id: str
    R_camera_from_board: np.ndarray
    t_camera_from_board_mm: np.ndarray
    R_world_from_camera: np.ndarray
    marker_count: int
    corner_count: int
    reprojection_rms_px: float
    reprojection_p95_px: float
    board_center_px: list[float]
    board_coverage_fraction: float
    estimated_board_plane_distance_mm: float
    pair_delta_us: float | None
    source_hashes: dict[str, str]


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Stage3AError(f"cannot read strict JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise Stage3AError(f"expected a JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def verify_frozen_authorities() -> dict[str, Any]:
    if not STAGE2_BUNDLE.is_file():
        raise Stage3AError(f"Stage2 final bundle missing: {STAGE2_BUNDLE}")
    stage2_hash = sha256_file(STAGE2_BUNDLE)
    if stage2_hash.lower() != STAGE2_SHA256:
        raise Stage3AError(
            f"Stage2 bundle SHA mismatch: expected {STAGE2_SHA256}, got {stage2_hash}"
        )
    if not OLD_STAGE7C.is_file():
        raise Stage3AError(f"old Stage7C reference missing: {OLD_STAGE7C}")
    old_hash = sha256_file(OLD_STAGE7C)
    if old_hash.lower() != OLD_STAGE7C_SHA256:
        raise Stage3AError(
            f"old Stage7C SHA mismatch: expected {OLD_STAGE7C_SHA256}, got {old_hash}"
        )
    return {
        "stage2_bundle": {"path": str(STAGE2_BUNDLE), "sha256": stage2_hash},
        "old_stage7c_stale_reference": {
            "path": str(OLD_STAGE7C),
            "sha256": old_hash,
            "classification": "STALE_PRE_REMOUNT_REFERENCE_ONLY",
            "numeric_authority": False,
        },
    }


def camera_model() -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    verify_frozen_authorities()
    bundle = read_json(STAGE2_BUNDLE)
    camera_section = bundle.get("camera_intrinsics")
    if not isinstance(camera_section, dict):
        raise Stage3AError("Stage2 bundle camera_intrinsics object missing")
    camera = camera_section.get("camera")
    if not isinstance(camera, dict):
        raise Stage3AError("Stage2 bundle camera_intrinsics.camera missing")
    resolution = camera.get("resolution")
    raster = camera.get("source_raster_provenance")
    intrinsics = camera.get("intrinsics")
    if resolution != {"width": WIDTH, "height": HEIGHT}:
        raise Stage3AError(f"frozen camera resolution mismatch: {resolution!r}")
    if not isinstance(raster, dict) or any(
        [
            raster.get("pixel_format") != "RGB565_LE",
            raster.get("crop") is not None,
            raster.get("resize") is not None,
        ]
    ):
        raise Stage3AError("frozen raster must be RGB565_LE with no crop/resize")
    if not isinstance(intrinsics, dict) or intrinsics.get("model") != "opencv_brown_5":
        raise Stage3AError("Stage2 Brown-5 camera intrinsics missing")
    distortion = intrinsics.get("distortion")
    if not isinstance(distortion, dict):
        raise Stage3AError("Stage2 distortion object missing")
    K = np.asarray(
        [
            [intrinsics["fx"], 0.0, intrinsics["cx"]],
            [0.0, intrinsics["fy"], intrinsics["cy"]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    D = np.asarray(
        [distortion[key] for key in ("k1", "k2", "p1", "p2", "k3")],
        dtype=np.float64,
    )
    board = camera_section.get("board")
    if board != BOARD:
        raise Stage3AError(f"Stage2 board schema mismatch: {board!r}")
    provenance = {
        "stage2_bundle_path": str(STAGE2_BUNDLE),
        "stage2_bundle_sha256": STAGE2_SHA256,
        "stage2a_authority": camera_section.get("authority"),
        "K": K.tolist(),
        "D_brown5_k1_k2_p1_p2_k3": D.tolist(),
        "raster": {
            "width": WIDTH,
            "height": HEIGHT,
            "pixel_format": "RGB565_LE",
            "crop": None,
            "resize": None,
            "rotation": None,
        },
    }
    return K, D, provenance


def default_height() -> dict[str, Any]:
    return {
        "schema_version": "stage3a_camera_height.v1",
        "camera_height_mm": 972.0,
        "camera_height_uncertainty_mm": 10.0,
        "measurement_method": "manual_tape_visual",
        "updated_at": now(),
        "note": "88.9 cm + 8.3 cm ~= 97.2 cm; conservative engineering uncertainty",
    }


def load_height() -> dict[str, Any]:
    if not HEIGHT_PATH.exists():
        write_json(HEIGHT_PATH, default_height())
    value = read_json(HEIGHT_PATH)
    height = float(value.get("camera_height_mm", math.nan))
    uncertainty = float(value.get("camera_height_uncertainty_mm", math.nan))
    if not math.isfinite(height) or height <= 0 or not math.isfinite(uncertainty) or uncertainty < 0:
        raise Stage3AError("camera_height.json contains invalid values")
    return value


def default_fixed_board_height() -> dict[str, Any]:
    return {
        "schema_version": "stage3a_fixed_board_height.v1",
        "board_mode": BOARD_MODE,
        "board_moved_between_poses": False,
        "fixed_board_active_bottom_height_mm": 912.0,
        "fixed_board_height_uncertainty_mm": 5.0,
        "raw_manual_observation_mm": 911.5,
        "measurement_method": "manual_tape_visual",
        "frozen": True,
        "configured_at": now(),
        "measurement_definition": (
            "ground plane to both ends of the bottom edge of the 175x125 mm ACTIVE ChArUco rectangle"
        ),
        "physical_fixture": "rigid vertical cabinet door; board fixed permanently; does not cross a seam",
    }


def load_fixed_board_height() -> dict[str, Any]:
    if not FIXED_BOARD_HEIGHT_PATH.exists():
        write_json(FIXED_BOARD_HEIGHT_PATH, default_fixed_board_height())
    value = read_json(FIXED_BOARD_HEIGHT_PATH)
    if value.get("board_mode") != BOARD_MODE or value.get("board_moved_between_poses") is not False:
        raise Stage3AError("fixed_board_height.json violates FIXED_VERTICAL_BOARD contract")
    height = float(value.get("fixed_board_active_bottom_height_mm", math.nan))
    uncertainty = float(value.get("fixed_board_height_uncertainty_mm", math.nan))
    if not math.isfinite(height) or height < 0 or not math.isfinite(uncertainty) or uncertainty < 0:
        raise Stage3AError("fixed_board_height.json contains invalid values")
    if value.get("frozen") is not True:
        raise Stage3AError("fixed board height must be frozen")
    return value


def default_registry() -> dict[str, Any]:
    return {
        "schema_version": "stage3a_pose_registry.v1",
        "created_at": now(),
        "updated_at": now(),
        "dynamic_registry": True,
        "silent_replace_forbidden": True,
        "board_mode": BOARD_MODE,
        "board_moved_between_poses": False,
        "capture_mode": CAPTURE_MODE,
        "pose_weighting": "11 frames form one physical-pose estimate; physical poses are equal-weight",
        "primary_solve_pose_ids": list(PRIMARY_POSE_IDS),
        "optional_reposition_holdout": "W04 (excluded from primary solve)",
        "fixed_board_height_source": str(FIXED_BOARD_HEIGHT_PATH),
        "poses": {},
    }


def load_registry() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        write_json(REGISTRY_PATH, default_registry())
    registry = read_json(REGISTRY_PATH)
    if registry.get("dynamic_registry") is not True or not isinstance(registry.get("poses"), dict):
        raise Stage3AError("invalid Stage3A dynamic registry")
    if "board_mode" not in registry:
        if registry["poses"]:
            raise Stage3AError("refusing to migrate a non-empty legacy moving-board registry")
        created_at = registry.get("created_at")
        registry = default_registry()
        if created_at:
            registry["created_at"] = created_at
        registry["migrated_from"] = "legacy_empty_registry_before_fixed_board_contract"
        registry["updated_at"] = now()
        write_json(REGISTRY_PATH, registry)
    if registry.get("board_mode") != BOARD_MODE or registry.get("board_moved_between_poses") is not False:
        raise Stage3AError("registry is not in FIXED_VERTICAL_BOARD mode")
    return registry


def make_transform(rotation: np.ndarray, translation: Iterable[float]) -> np.ndarray:
    R = np.asarray(rotation, dtype=np.float64)
    t = np.asarray(list(translation), dtype=np.float64).reshape(3)
    if R.shape != (3, 3) or not np.isfinite(R).all() or not np.isfinite(t).all():
        raise Stage3AError("invalid R/t for transform")
    T = np.eye(4, dtype=np.float64)
    T[:3, :3] = R
    T[:3, 3] = t
    return T


def project_to_rotation(value: np.ndarray) -> np.ndarray:
    U, _, Vt = np.linalg.svd(np.asarray(value, dtype=np.float64))
    result = U @ Vt
    if np.linalg.det(result) < 0:
        U[:, -1] *= -1
        result = U @ Vt
    return result


def rotation_distance_deg(a: np.ndarray, b: np.ndarray) -> float:
    delta = np.asarray(a).T @ np.asarray(b)
    cosine = float(np.clip((np.trace(delta) - 1.0) / 2.0, -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def chordal_mean(rotations: Iterable[np.ndarray]) -> np.ndarray:
    values = list(rotations)
    if not values:
        raise Stage3AError("cannot average zero rotations")
    return project_to_rotation(np.sum(np.stack(values), axis=0))


def robust_rotation_mean(rotations: list[np.ndarray], minimum: int = 3) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if len(rotations) < minimum:
        raise Stage3AError(f"need at least {minimum} rotations, got {len(rotations)}")
    pairwise = np.asarray([[rotation_distance_deg(a, b) for b in rotations] for a in rotations])
    mean = rotations[int(np.argmin(np.median(pairwise, axis=1)))]
    keep = np.ones(len(rotations), dtype=bool)
    for _ in range(8):
        residual = np.asarray([rotation_distance_deg(mean, item) for item in rotations])
        centre = float(np.median(residual))
        mad = float(np.median(np.abs(residual - centre)))
        threshold = max(0.25, centre + 4.5 * max(1.4826 * mad, 0.01))
        updated = residual <= threshold
        if np.count_nonzero(updated) < minimum:
            updated[:] = False
            updated[np.argsort(residual)[:minimum]] = True
        candidate = chordal_mean([item for item, selected in zip(rotations, updated) if selected])
        if np.array_equal(updated, keep) and rotation_distance_deg(mean, candidate) < 1e-10:
            mean = candidate
            keep = updated
            break
        mean, keep = candidate, updated
    residual = np.asarray([rotation_distance_deg(mean, item) for item in rotations])
    return mean, residual, keep


def rotation_world_from_camera(R_camera_from_board: np.ndarray) -> np.ndarray:
    return project_to_rotation(R_WORLD_FROM_BOARD @ np.asarray(R_camera_from_board).T)


def orientation_diagnostics(R_world_from_camera: np.ndarray) -> dict[str, Any]:
    R = np.asarray(R_world_from_camera, dtype=np.float64)
    right, optical = R[:, 0], R[:, 2]
    pitch = math.atan2(float(optical[2]), float(np.hypot(optical[0], optical[1])))
    yaw = math.atan2(float(optical[0]), float(optical[1]))
    right_zero = np.cross(optical, np.asarray([0.0, 0.0, 1.0]))
    right_zero /= np.linalg.norm(right_zero)
    down_zero = np.cross(optical, right_zero)
    roll = math.atan2(float(right @ down_zero), float(right @ right_zero))
    return {
        "roll_deg": math.degrees(roll),
        "pitch_deg": math.degrees(pitch),
        "yaw_deg": math.degrees(yaw),
        "order": "yaw_from_world_XY -> pitch_from_world_Z -> roll_about_camera_optical_axis",
        "convention": "legacy Stage7C optical-axis diagnostic decomposition; not reconstruction authority",
        "matrix_first_authority": True,
    }


def rgb565_le_to_bgr(path: Path) -> np.ndarray:
    raw = np.fromfile(path, dtype="<u2")
    if raw.size != WIDTH * HEIGHT:
        raise Stage3AError(f"RGB565 payload size mismatch: {path} ({raw.size} pixels)")
    values = raw.reshape(HEIGHT, WIDTH)
    red = (((values >> 11) & 0x1F) * 255 + 15) // 31
    green = (((values >> 5) & 0x3F) * 255 + 31) // 63
    blue = ((values & 0x1F) * 255 + 15) // 31
    return np.dstack((blue, green, red)).astype(np.uint8)


def charuco_objects() -> tuple[Any, Any]:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
    board = cv2.aruco.CharucoBoard(
        (BOARD["squares_x"], BOARD["squares_y"]),
        BOARD["square_length_mm"] / 1000.0,
        BOARD["marker_length_mm"] / 1000.0,
        dictionary,
    )
    detector_parameters = cv2.aruco.DetectorParameters()
    detector_parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.CharucoDetector(
        board,
        cv2.aruco.CharucoParameters(),
        detector_parameters,
        cv2.aruco.RefineParameters(),
    )
    return board, detector


def polygon_area(points: np.ndarray) -> float:
    xy = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    return float(abs(np.dot(xy[:, 0], np.roll(xy[:, 1], -1)) - np.dot(xy[:, 1], np.roll(xy[:, 0], -1))) / 2.0)


def nested_number(value: Any, names: set[str]) -> float | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in names and isinstance(item, (int, float)) and math.isfinite(float(item)):
                return float(item)
            found = nested_number(item, names)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = nested_number(item, names)
            if found is not None:
                return found
    return None


def validate_scan(scan_dir: Path) -> list[Path]:
    if not scan_dir.is_dir() or not (scan_dir / "scan.json").is_file():
        raise Stage3AError(f"formal scan directory/scan.json missing: {scan_dir}")
    frame_dirs = []
    for frame_id in FRAME_IDS:
        frame_dir = scan_dir / "frames" / frame_id
        required = [frame_dir / "frame.json", frame_dir / "image.rgb565", frame_dir / "tof.json"]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise Stage3AError(f"incomplete formal frame {frame_id}: {missing}")
        if (frame_dir / "image.rgb565").stat().st_size != WIDTH * HEIGHT * 2:
            raise Stage3AError(f"RGB565 byte count mismatch: {frame_dir / 'image.rgb565'}")
        frame_dirs.append(frame_dir)
    actual = sorted(path.name for path in (scan_dir / "frames").iterdir() if path.is_dir())
    if actual != list(FRAME_IDS):
        raise Stage3AError(f"formal scan must contain exactly 11 canonical frames, got {actual}")
    return frame_dirs


def resolve_scan(scan_id: str | None, latest: bool) -> Path:
    if bool(scan_id) == bool(latest):
        raise Stage3AError("choose exactly one of --scan-id or --latest")
    if latest:
        scans = sorted(
            (path for path in SCAN_ROOT.glob("scan_*") if path.is_dir()),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
        )
        if not scans:
            raise Stage3AError(f"no validated scans under {SCAN_ROOT}")
        return scans[-1]
    assert scan_id is not None
    if Path(scan_id).name != scan_id or not scan_id.startswith("scan_"):
        raise Stage3AError(f"unsafe scan ID: {scan_id!r}")
    return SCAN_ROOT / scan_id


def scan_hashes(scan_dir: Path) -> dict[str, str]:
    validate_scan(scan_dir)
    files = [scan_dir / "scan.json"]
    for frame_id in FRAME_IDS:
        frame_dir = scan_dir / "frames" / frame_id
        files.extend(frame_dir / name for name in ("frame.json", "image.rgb565", "tof.json"))
    return {str(path.resolve()): sha256_file(path) for path in files}


def detect_frame(pose_id: str, frame_dir: Path, K: np.ndarray, D: np.ndarray) -> FrameObservation:
    board, detector = charuco_objects()
    image_path = frame_dir / "image.rgb565"
    image = rgb565_le_to_bgr(image_path)
    corners, ids, marker_corners, marker_ids = detector.detectBoard(image)
    marker_count = 0 if marker_ids is None else int(len(marker_ids))
    corner_count = 0 if ids is None else int(len(ids))
    if corners is None or ids is None or corner_count < 6:
        raise Stage3AError(f"{frame_dir.name}: insufficient ChArUco corners ({corner_count})")
    object_points, image_points = board.matchImagePoints(corners, ids)
    ok, rvec, tvec = cv2.solvePnP(object_points, image_points, K, D, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok or float(tvec.reshape(3)[2]) <= 0:
        raise Stage3AError(f"{frame_dir.name}: solvePnP failed/front-back invalid")
    projected, _ = cv2.projectPoints(object_points, rvec, tvec, K, D)
    errors = np.linalg.norm(projected.reshape(-1, 2) - image_points.reshape(-1, 2), axis=1)
    R_cb, _ = cv2.Rodrigues(rvec)
    board_normal_camera = R_cb[:, 2]
    board_plane_distance_mm = abs(float(board_normal_camera @ tvec.reshape(3))) * 1000.0
    outline_m = np.asarray(
        [[[0.0, 0.0, 0.0], [0.175, 0.0, 0.0], [0.175, 0.125, 0.0], [0.0, 0.125, 0.0]]],
        dtype=np.float32,
    ).reshape(-1, 3)
    outline_px, _ = cv2.projectPoints(outline_m, rvec, tvec, K, D)
    outline_px = outline_px.reshape(-1, 2)
    frame_json = read_json(frame_dir / "frame.json")
    tof_json = read_json(frame_dir / "tof.json")
    pair_delta = nested_number(frame_json, {"pair_delta_us", "camera_tof_pair_delta_us", "delta_us"})
    if pair_delta is None:
        pair_delta = nested_number(tof_json, {"pair_delta_us", "camera_tof_pair_delta_us", "delta_us"})
    sources = [frame_dir / name for name in ("frame.json", "image.rgb565", "tof.json")]
    return FrameObservation(
        pose_id=pose_id,
        frame_id=frame_dir.name,
        R_camera_from_board=R_cb,
        t_camera_from_board_mm=tvec.reshape(3) * 1000.0,
        R_world_from_camera=rotation_world_from_camera(R_cb),
        marker_count=marker_count,
        corner_count=corner_count,
        reprojection_rms_px=float(np.sqrt(np.mean(np.square(errors)))),
        reprojection_p95_px=float(np.percentile(errors, 95)),
        board_center_px=np.mean(outline_px, axis=0).tolist(),
        board_coverage_fraction=polygon_area(outline_px) / float(WIDTH * HEIGHT),
        estimated_board_plane_distance_mm=board_plane_distance_mm,
        pair_delta_us=pair_delta,
        source_hashes={str(path.resolve()): sha256_file(path) for path in sources},
    )


def frame_to_json(frame: FrameObservation) -> dict[str, Any]:
    rvec, _ = cv2.Rodrigues(frame.R_camera_from_board)
    return {
        "frame_id": frame.frame_id,
        "valid": True,
        "marker_count": frame.marker_count,
        "charuco_corner_count": frame.corner_count,
        "reprojection_rms_px": frame.reprojection_rms_px,
        "reprojection_p95_px": frame.reprojection_p95_px,
        "board_center_px": frame.board_center_px,
        "board_coverage_fraction": frame.board_coverage_fraction,
        "estimated_board_plane_distance_mm": frame.estimated_board_plane_distance_mm,
        "rvec_camera_from_board": rvec.reshape(3).tolist(),
        "tvec_camera_from_board_mm": frame.t_camera_from_board_mm.tolist(),
        "R_camera_from_board": frame.R_camera_from_board.tolist(),
        "R_world_from_camera": frame.R_world_from_camera.tolist(),
        "pair_delta_us_provenance_only": frame.pair_delta_us,
        "source_hashes": frame.source_hashes,
    }


def process_pose(pose_id: str, binding: dict[str, Any], *, write: bool = True) -> dict[str, Any]:
    K, D, _ = camera_model()
    scan_dir = Path(binding["scan_path"])
    frame_dirs = validate_scan(scan_dir)
    frames: list[FrameObservation] = []
    failures: list[dict[str, str]] = []
    for frame_dir in frame_dirs:
        try:
            frames.append(detect_frame(pose_id, frame_dir, K, D))
        except Stage3AError as exc:
            failures.append({"frame_id": frame_dir.name, "error": str(exc)})
    if len(frames) < 3:
        raise Stage3AError(f"{pose_id}: only {len(frames)}/11 Camera-valid frames; {failures}")
    pose_R, residual, inliers = robust_rotation_mean(
        [frame.R_world_from_camera for frame in frames], minimum=min(3, len(frames))
    )
    tvecs = np.stack([frame.t_camera_from_board_mm for frame in frames])
    report = {
        "schema_version": "stage3a_pose_diagnostics.v1",
        "generated_at": now(),
        "pose_id": pose_id,
        "scan_id": binding["scan_id"],
        "scan_path": str(scan_dir),
        "distance_role": binding["distance_role"],
        "board_mode": BOARD_MODE,
        "board_moved_between_poses": False,
        "physical_pose_equal_weight_unit": True,
        "frame_count_expected": FRAME_COUNT,
        "camera_valid_frame_count": len(frames),
        "invalid_frames": failures,
        "R_world_from_camera_pose_mean": pose_R.tolist(),
        "pose_orientation_frame_residual_deg": {
            "rms": float(np.sqrt(np.mean(np.square(residual[inliers])))),
            "p95": float(np.percentile(residual[inliers], 95)),
            "max": float(np.max(residual[inliers])),
            "inlier_count": int(np.count_nonzero(inliers)),
        },
        "translation_camera_from_board_spread_mm": {
            "axis_std": np.std(tvecs, axis=0).tolist(),
            "radial_p95_from_median": float(
                np.percentile(np.linalg.norm(tvecs - np.median(tvecs, axis=0), axis=1), 95)
            ),
            "motion_diagnostic_only": True,
        },
        "reprojection": {
            "frame_rms_median_px": float(np.median([frame.reprojection_rms_px for frame in frames])),
            "frame_rms_p95_px": float(np.percentile([frame.reprojection_rms_px for frame in frames], 95)),
            "frame_p95_max_px": float(np.max([frame.reprojection_p95_px for frame in frames])),
        },
        "distance_and_image_coverage": {
            "estimated_board_plane_distance_mm": {
                "median": float(np.median([frame.estimated_board_plane_distance_mm for frame in frames])),
                "p05": float(np.percentile([frame.estimated_board_plane_distance_mm for frame in frames], 5)),
                "p95": float(np.percentile([frame.estimated_board_plane_distance_mm for frame in frames], 95)),
            },
            "board_center_px_median": np.median(
                np.asarray([frame.board_center_px for frame in frames], dtype=np.float64), axis=0
            ).tolist(),
            "board_coverage_fraction_median": float(
                np.median([frame.board_coverage_fraction for frame in frames])
            ),
        },
        "frames": [frame_to_json(frame) for frame in frames],
    }
    if write:
        write_json(DIAGNOSTICS / f"{pose_id}.json", report)
    return report


def pose_rotations(pose_reports: dict[str, dict[str, Any]]) -> dict[str, np.ndarray]:
    return {
        pose_id: np.asarray(report["R_world_from_camera_pose_mean"], dtype=np.float64)
        for pose_id, report in pose_reports.items()
    }


def summarize_numbers(values: Iterable[float]) -> dict[str, float | int | None]:
    array = np.asarray(list(values), dtype=np.float64)
    if array.size == 0:
        return {"count": 0, "median": None, "p95": None, "max": None, "rms": None}
    absolute = np.abs(array)
    return {
        "count": int(array.size),
        "median": float(np.median(array)),
        "median_abs": float(np.median(absolute)),
        "p95_abs": float(np.percentile(absolute, 95)),
        "max_abs": float(np.max(absolute)),
        "rms": float(np.sqrt(np.mean(np.square(array)))),
    }


def pose_estimated_distance_mm(report: dict[str, Any]) -> float:
    coverage = report.get("distance_and_image_coverage", {})
    estimate = coverage.get("estimated_board_plane_distance_mm", {})
    if isinstance(estimate, dict) and isinstance(estimate.get("median"), (int, float)):
        return float(estimate["median"])
    distances = []
    for frame in report["frames"]:
        if isinstance(frame.get("estimated_board_plane_distance_mm"), (int, float)):
            distances.append(float(frame["estimated_board_plane_distance_mm"]))
            continue
        R_cb = np.asarray(frame["R_camera_from_board"], dtype=np.float64)
        t_cb = np.asarray(frame["tvec_camera_from_board_mm"], dtype=np.float64)
        distances.append(abs(float(R_cb[:, 2] @ t_cb)))
    return float(np.median(distances))


def linear_slope(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or float(np.ptp(xs)) <= 1e-9:
        return None
    return float(np.polyfit(xs, ys, 1)[0])


def fixed_height_residuals(
    final_R: np.ndarray,
    pose_reports: dict[str, dict[str, Any]],
    fixed_board: dict[str, Any],
    camera_height_mm: float,
) -> dict[str, Any]:
    board_points = np.asarray(
        [[0.0, 0.0, 0.0], [175.0, 0.0, 0.0], [175.0, 125.0, 0.0], [0.0, 125.0, 0.0]],
        dtype=np.float64,
    )
    labels = ["active_top_left", "active_top_right", "active_bottom_right", "active_bottom_left"]
    result: dict[str, Any] = {}
    all_bottom_values: list[float] = []
    distances: list[float] = []
    pose_medians: list[float] = []
    bottom = float(fixed_board["fixed_board_active_bottom_height_mm"])
    for pose_id, report in pose_reports.items():
        bottom_values = []
        per_frame = []
        for frame in report["frames"]:
            R_cb = np.asarray(frame["R_camera_from_board"], dtype=np.float64)
            t_cb = np.asarray(frame["tvec_camera_from_board_mm"], dtype=np.float64)
            p_camera = (R_cb @ board_points.T).T + t_cb
            p_world = (final_R @ p_camera.T).T + np.asarray([0.0, 0.0, camera_height_mm])
            expected_z = np.asarray([bottom + 125.0, bottom + 125.0, bottom, bottom])
            residual = p_world[:, 2] - expected_z
            reconstructed_bottom = float(np.mean(p_world[2:, 2]))
            bottom_residual = reconstructed_bottom - bottom
            bottom_values.append(bottom_residual)
            per_frame.append(
                {
                    "frame_id": frame["frame_id"],
                    "corner_labels": labels,
                    "recovered_world_z_mm": p_world[:, 2].tolist(),
                    "expected_world_z_mm": expected_z.tolist(),
                    "residual_mm": residual.tolist(),
                    "reconstructed_active_bottom_z_mm": reconstructed_bottom,
                    "active_bottom_signed_residual_mm": bottom_residual,
                }
            )
        pose_summary = summarize_numbers(bottom_values)
        distance = pose_estimated_distance_mm(report)
        result[pose_id] = {
            "distance_role": report.get("distance_role", POSE_DISTANCE_ROLES.get(pose_id, "supplemental")),
            "estimated_board_plane_distance_mm": distance,
            "fixed_active_bottom_height_mm": bottom,
            "fixed_height_uncertainty_mm": fixed_board["fixed_board_height_uncertainty_mm"],
            "reconstructed_active_bottom_z_mm": float(bottom + float(pose_summary["median"])),
            "signed_residual_mm": pose_summary["median"],
            "abs_residual_mm": abs(float(pose_summary["median"])),
            "frame_residual_summary": pose_summary,
            "frames": per_frame,
        }
        all_bottom_values.extend(bottom_values)
        distances.append(distance)
        pose_medians.append(float(pose_summary["median"]))
    return {
        "board_mode": BOARD_MODE,
        "board_moved_between_poses": False,
        "fixed_board_active_bottom_height_mm": bottom,
        "fixed_board_height_uncertainty_mm": fixed_board["fixed_board_height_uncertainty_mm"],
        "point_contract": "same fixed active bottom edge at 912 mm for every vehicle distance; top edge is bottom+125 mm",
        "per_pose": result,
        "overall_active_bottom_residual_mm": summarize_numbers(all_bottom_values),
        "residual_vs_distance_slope_mm_per_mm": linear_slope(distances, pose_medians),
        "residual_vs_distance_slope_mm_per_m": (
            None if linear_slope(distances, pose_medians) is None else 1000.0 * float(linear_slope(distances, pose_medians))
        ),
        "distance_trend_exists": True,
        "poses_are_equal_weight_for_orientation": True,
    }


def orientation_vs_distance(
    rotations: dict[str, np.ndarray],
    pose_reports: dict[str, dict[str, Any]],
    aggregate_R: np.ndarray,
) -> dict[str, Any]:
    rows = {}
    distances: list[float] = []
    geodesic: list[float] = []
    rpy_values = {"roll_deg": [], "pitch_deg": [], "yaw_deg": []}
    for pose_id, rotation in rotations.items():
        distance = pose_estimated_distance_mm(pose_reports[pose_id])
        rpy = orientation_diagnostics(rotation)
        angle = rotation_distance_deg(aggregate_R, rotation)
        rows[pose_id] = {
            "distance_role": pose_reports[pose_id].get("distance_role", POSE_DISTANCE_ROLES.get(pose_id, "supplemental")),
            "estimated_board_plane_distance_mm": distance,
            "R_world_from_camera_pose_mean": rotation.tolist(),
            "diagnostic_rpy": rpy,
            "geodesic_angle_to_aggregate_deg": angle,
        }
        distances.append(distance)
        geodesic.append(angle)
        for key in rpy_values:
            rpy_values[key].append(float(rpy[key]))
    return {
        "per_pose": rows,
        "geodesic_spread_deg": summarize_numbers(geodesic),
        "rpy_peak_to_peak_deg": {
            key: float(np.ptp(values)) if values else None for key, values in rpy_values.items()
        },
        "geodesic_residual_vs_distance_slope_deg_per_m": (
            None if linear_slope(distances, geodesic) is None else 1000.0 * float(linear_slope(distances, geodesic))
        ),
        "rpy_vs_distance_slope_deg_per_m": {
            key: (None if linear_slope(distances, values) is None else 1000.0 * float(linear_slope(distances, values)))
            for key, values in rpy_values.items()
        },
    }


def sign_checks(R: np.ndarray, camera_height_mm: float) -> dict[str, Any]:
    t = np.asarray([0.0, 0.0, camera_height_mm])
    T = make_transform(R, t)
    camera_origin = (T @ np.asarray([0.0, 0.0, 0.0, 1.0]))[:3]
    ground_camera = R.T @ (-t)
    recovered_ground = (T @ np.r_[ground_camera, 1.0])[:3]
    right_world = R @ np.asarray([1.0, 0.0, 0.0])
    forward_world = R @ np.asarray([0.0, 0.0, 1.0])
    checks = {
        "camera_optical_centre_world": {
            "value": camera_origin.tolist(),
            "expected": [0.0, 0.0, camera_height_mm],
            "pass": bool(np.allclose(camera_origin, [0.0, 0.0, camera_height_mm], atol=1e-9)),
        },
        "ground_point_below_camera_world": {
            "camera_coordinates": ground_camera.tolist(),
            "recovered_world": recovered_ground.tolist(),
            "expected": [0.0, 0.0, 0.0],
            "pass": bool(np.allclose(recovered_ground, 0.0, atol=1e-8)),
        },
        "world_z_up": {"value": [0.0, 0.0, 1.0], "pass": True},
        "camera_image_right_has_positive_world_x": {
            "mapped_direction": right_world.tolist(),
            "pass": bool(right_world[0] > 0.0),
        },
        "camera_optical_forward_has_positive_world_y": {
            "mapped_direction": forward_world.tolist(),
            "pass": bool(forward_world[1] > 0.0),
        },
        "rotation_orthonormal": {
            "max_abs_error": float(np.max(np.abs(R.T @ R - np.eye(3)))),
            "pass": bool(np.allclose(R.T @ R, np.eye(3), atol=1e-8)),
        },
        "rotation_determinant_positive_one": {
            "determinant": float(np.linalg.det(R)),
            "pass": bool(abs(float(np.linalg.det(R)) - 1.0) < 1e-8),
        },
        "column_vector_direction": {
            "equation": "p_world = R_world_from_camera * p_camera + t_world_from_camera",
            "pass": True,
        },
    }
    checks["all_pass"] = all(item.get("pass", False) for item in checks.values() if isinstance(item, dict))
    return checks


def lopo_diagnostics(rotations: dict[str, np.ndarray], full_R: np.ndarray) -> dict[str, Any]:
    rows = []
    if len(rotations) < 3:
        return {"available": False, "reason": "at least 3 physical poses required", "rows": rows}
    for omitted in rotations:
        remaining = [rotation for pose_id, rotation in rotations.items() if pose_id != omitted]
        lopo_R = chordal_mean(remaining)
        rows.append(
            {
                "omitted_pose_id": omitted,
                "solution_delta_from_full_deg": rotation_distance_deg(lopo_R, full_R),
                "omitted_pose_residual_to_lopo_deg": rotation_distance_deg(lopo_R, rotations[omitted]),
            }
        )
    return {
        "available": True,
        "rows": rows,
        "max_solution_delta_from_full_deg": max(row["solution_delta_from_full_deg"] for row in rows),
        "max_omitted_pose_residual_to_lopo_deg": max(row["omitted_pose_residual_to_lopo_deg"] for row in rows),
    }


def height_sensitivity(camera_height_mm: float) -> dict[str, Any]:
    values = [camera_height_mm - 10.0, camera_height_mm, camera_height_mm + 10.0]
    return {
        "orientation_fixed": True,
        "evaluated_camera_heights_mm": values,
        "world_z_constant_offset_from_nominal_mm": [-10.0, 0.0, 10.0],
        "support_height_expected_effect": "same signed constant world-Z offset, bounded by +/-10 mm from this source",
        "object_height_expected_effect": (
            "absolute world-Z endpoints shift together by the same constant; a pure height difference cancels "
            "if support and object use the same transform, but downstream selection/plane effects require Stage3B"
        ),
        "engineering_boundary_mm": 10.0,
    }


def solve_registered(*, write: bool = True) -> dict[str, Any]:
    authorities = verify_frozen_authorities()
    _, _, camera_provenance = camera_model()
    height = load_height()
    fixed_board = load_fixed_board_height()
    registry = load_registry()
    pose_reports = {
        pose_id: process_pose(pose_id, binding, write=write)
        for pose_id, binding in registry["poses"].items()
    }
    missing_primary = [pose_id for pose_id in PRIMARY_POSE_IDS if pose_id not in pose_reports]
    if missing_primary:
        raise Stage3AError(
            f"need W01 near, W02 mid, and W03 far before solve; missing={missing_primary}"
        )
    primary_reports = {pose_id: pose_reports[pose_id] for pose_id in PRIMARY_POSE_IDS}
    primary_rotations = pose_rotations(primary_reports)
    # Each physical pose contributes exactly one rotation, irrespective of how
    # many of its 11 frames succeeded.
    final_R, pose_residual, pose_inliers = robust_rotation_mean(
        list(primary_rotations.values()), minimum=3
    )
    camera_height_mm = float(height["camera_height_mm"])
    t = np.asarray([0.0, 0.0, camera_height_mm])
    pose_ids = list(primary_rotations)
    per_pose_orientation = {
        pose_id: {
            "residual_to_final_deg": float(pose_residual[index]),
            "orientation_inlier": bool(pose_inliers[index]),
            "equal_weight": True,
        }
        for index, pose_id in enumerate(pose_ids)
    }
    height_result = fixed_height_residuals(
        final_R, primary_reports, fixed_board, camera_height_mm
    )
    orientation_distance_result = orientation_vs_distance(
        primary_rotations, primary_reports, final_R
    )
    lopo = lopo_diagnostics(primary_rotations, final_R)
    signs = sign_checks(final_R, camera_height_mm)
    reprojection_medians = [report["reprojection"]["frame_rms_median_px"] for report in primary_reports.values()]
    pose_inlier_residuals = pose_residual[pose_inliers]
    height_summary = height_result["overall_active_bottom_residual_mm"]
    primary_distances = {
        pose_id: pose_estimated_distance_mm(primary_reports[pose_id]) for pose_id in PRIMARY_POSE_IDS
    }
    monotonic_distance = (
        primary_distances["W01"] < primary_distances["W02"] < primary_distances["W03"]
    )
    distance_span = primary_distances["W03"] - primary_distances["W01"]
    gates = {
        "primary_physical_pose_count": {
            "pass": all(pose_id in pose_reports for pose_id in PRIMARY_POSE_IDS),
            "value": list(primary_reports),
            "guideline": "W01 near + W02 mid + W03 far",
        },
        "valid_frames_per_pose": {
            "pass": all(report["camera_valid_frame_count"] >= 5 for report in primary_reports.values()),
            "value": {pose_id: report["camera_valid_frame_count"] for pose_id, report in primary_reports.items()},
            "guideline": ">=5/11 Camera-valid frames per physical pose (soft engineering gate)",
        },
        "reprojection": {
            "pass": float(np.percentile(reprojection_medians, 95)) <= 1.5,
            "value_pose_median_p95_px": float(np.percentile(reprojection_medians, 95)),
            "guideline": "pose-median reprojection RMS P95 <=1.5 px",
        },
        "pose_orientation_consistency": {
            "pass": float(np.max(pose_inlier_residuals)) <= 1.5,
            "value_max_inlier_deg": float(np.max(pose_inlier_residuals)),
            "guideline": "max inlier physical-pose orientation residual <=1.5 deg",
        },
        "near_mid_far_distance_coverage": {
            "pass": bool(monotonic_distance and distance_span >= 250.0),
            "estimated_board_plane_distance_mm": primary_distances,
            "near_to_far_span_mm": distance_span,
            "guideline": "PnP distances increase W01<W02<W03 with >=250 mm near-to-far span; targets ~600/~800/~1000 mm",
        },
        "fixed_known_height_residual": {
            "pass": height_summary["p95_abs"] is not None and float(height_summary["p95_abs"]) <= 30.0,
            "value_p95_abs_mm": height_summary["p95_abs"],
            "guideline": "fixed active-bottom reconstructed-Z residual P95 <=30 mm across distance",
        },
        "lopo": {
            "pass": bool(lopo.get("available")) and float(lopo["max_solution_delta_from_full_deg"]) <= 1.0,
            "value_max_solution_delta_deg": lopo.get("max_solution_delta_from_full_deg"),
            "guideline": "LOPO solution delta <=1.0 deg",
        },
        "sign_and_matrix_contract": {"pass": bool(signs["all_pass"]), "guideline": "all analytic checks pass"},
    }
    holdout = None
    all_rotations = pose_rotations(pose_reports)
    if "W04" in all_rotations:
        holdout_height = fixed_height_residuals(
            final_R, {"W04": pose_reports["W04"]}, fixed_board, camera_height_mm
        )
        holdout_angle = rotation_distance_deg(final_R, all_rotations["W04"])
        holdout_distance = pose_estimated_distance_mm(pose_reports["W04"])
        holdout = {
            "pose_id": "W04",
            "role": "reposition_holdout",
            "excluded_from_primary_solve": True,
            "primary_solve_pose_ids": list(PRIMARY_POSE_IDS),
            "orientation_residual_to_primary_solution_deg": holdout_angle,
            "estimated_board_plane_distance_mm": holdout_distance,
            "distance_delta_from_W02_mid_mm": holdout_distance - primary_distances["W02"],
            "fixed_height_consistency": holdout_height,
        }
        holdout["pass"] = holdout_angle <= 1.5
        gates["W04_reposition_holdout"] = {
            "pass": holdout["pass"],
            "value_deg": holdout_angle,
            "guideline": "optional W04, excluded from primary solve; orientation repeatability <=1.5 deg",
        }
    required = [value["pass"] for key, value in gates.items() if key != "W04_reposition_holdout"]
    if all(required) and (holdout is None or holdout["pass"]):
        decision = "EVIDENCE_READY_FOR_OWNER_JUDGMENT"
    else:
        decision = "NEEDS_ONE_MORE_POSE"
    solution = {
        "schema_version": "stage3a_camera_world_solution.v1",
        "generated_at": now(),
        "decision": decision,
        "world_pose_resolved": decision == "EVIDENCE_READY_FOR_OWNER_JUDGMENT",
        "absolute_geometry_valid": False,
        "promotion": False,
        "matrix_first_authority": True,
        "board_mode": BOARD_MODE,
        "board_moved_between_poses": False,
        "capture_mode": CAPTURE_MODE,
        "distance_coverage": "near_mid_far",
        "primary_solve_pose_ids": list(PRIMARY_POSE_IDS),
        "optional_reposition_holdout": "W04",
        "fixed_board_height": fixed_board,
        "world_contract": {
            "x_axis": "vehicle_right",
            "y_axis": "vehicle_forward",
            "z_axis": "up",
            "origin": "ground_point_directly_below_camera_optical_centre",
            "transform": "T_world_from_camera",
            "equation": "p_world = R_world_from_camera * p_camera + t_world_from_camera",
            "vector_convention": "column-vector",
            "json_matrix_order": "row-major",
            "translation_unit": "mm",
        },
        "board_world_contract": {
            "opencv_board_x": "printed_active_board_horizontal_right = +X_world",
            "opencv_board_y": "printed_active_board_vertical_down = -Z_world",
            "opencv_board_z": "through board away from vehicle = +Y_world",
            "printed_front_normal": "-opencv_board_z = -Y_world (toward vehicle)",
            "R_world_from_board": R_WORLD_FROM_BOARD.tolist(),
        },
        "R_world_from_camera": final_R.tolist(),
        "t_world_from_camera_mm": t.tolist(),
        "T_world_from_camera": make_transform(final_R, t).tolist(),
        "camera_height": height,
        "diagnostic_rpy": orientation_diagnostics(final_R),
        "camera_provenance": camera_provenance,
        "frozen_authorities": authorities,
        "old_stage7c_reused_as_numeric_truth": False,
        "physical_pose_weighting": (
            "W01/W02/W03 each form one robust frame mean and are equal-weight; W04 is holdout only"
        ),
        "per_pose_orientation": per_pose_orientation,
        "per_pose_quality": pose_reports,
        "distance_coverage_diagnostics": {
            "estimated_board_plane_distance_mm": primary_distances,
            "near_to_far_span_mm": distance_span,
            "monotonic_near_mid_far": monotonic_distance,
        },
        "fixed_height_consistency": height_result,
        "orientation_vs_distance": orientation_distance_result,
        "leave_one_physical_pose_out": lopo,
        "W04_reposition_holdout": holdout,
        "height_sensitivity": height_sensitivity(camera_height_mm),
        "sign_and_convention_checks": signs,
        "gates": gates,
        "fixture_alignment_uncertainty": {
            "quantified": False,
            "sources": [
                "cabinet plane verticality",
                "active board horizontal-edge levelling",
                "vehicle squareness/yaw to cabinet plane",
                "flat-floor assumption",
                "active-bottom height manual measurement",
            ],
            "policy": "do not report false 0.01-degree fixture precision; retain as engineering uncertainty",
        },
    }
    if write:
        write_json(DIAGNOSTICS / "solution.json", solution)
        write_json(VALIDATION / "validation_summary.json", solution)
    return solution


def candidate_from_solution(solution: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    if solution["decision"] != "EVIDENCE_READY_FOR_OWNER_JUDGMENT":
        raise Stage3AError(f"candidate export forbidden: decision={solution['decision']}")
    source_scans = []
    for pose_id, binding in registry["poses"].items():
        source_scans.append(
            {
                "pose_id": pose_id,
                "scan_id": binding["scan_id"],
                "scan_path": binding["scan_path"],
                "scan_hashes": binding["source_hashes"],
                "distance_role": binding["distance_role"],
                "primary_solve_input": pose_id in PRIMARY_POSE_IDS,
                "holdout_only": pose_id == "W04",
                "vehicle_alignment_delta_mm": binding.get("vehicle_alignment_delta_mm"),
            }
        )
    return {
        "schema_version": "camera_world_pose.stage3a_candidate.v1",
        "candidate_status": "CANDIDATE_NOT_PROMOTED",
        "generated_at": now(),
        "world_pose_resolved": True,
        "absolute_geometry_valid": False,
        "no_promotion": True,
        "ready_for": "OWNER_JUDGMENT_BEFORE_STAGE3B",
        "board_mode": BOARD_MODE,
        "board_moved_between_poses": False,
        "capture_mode": CAPTURE_MODE,
        "distance_coverage": "near_mid_far",
        "optional_reposition_holdout": "W04",
        "primary_solve_pose_ids": list(PRIMARY_POSE_IDS),
        "fixed_board_active_bottom_height_mm": solution["fixed_board_height"][
            "fixed_board_active_bottom_height_mm"
        ],
        "fixed_board_height_uncertainty_mm": solution["fixed_board_height"][
            "fixed_board_height_uncertainty_mm"
        ],
        "fixed_board_height_measurement_method": solution["fixed_board_height"][
            "measurement_method"
        ],
        "stage2_bundle": solution["frozen_authorities"]["stage2_bundle"],
        "camera_intrinsics_provenance": solution["camera_provenance"],
        "camera_height": solution["camera_height"],
        "camera_height_mm": solution["camera_height"]["camera_height_mm"],
        "camera_height_uncertainty_mm": solution["camera_height"]["camera_height_uncertainty_mm"],
        "camera_height_measurement_method": solution["camera_height"]["measurement_method"],
        "world_contract": solution["world_contract"],
        "board_world_contract": solution["board_world_contract"],
        "matrix_first_authority": True,
        "R_world_from_camera": solution["R_world_from_camera"],
        "t_world_from_camera": solution["t_world_from_camera_mm"],
        "t_world_from_camera_mm": solution["t_world_from_camera_mm"],
        "T_world_from_camera": solution["T_world_from_camera"],
        "diagnostic_rpy": solution["diagnostic_rpy"],
        "source_physical_poses": source_scans,
        "per_pose_quality": solution["per_pose_quality"],
        "distance_coverage_diagnostics": solution["distance_coverage_diagnostics"],
        "fixed_height_consistency": solution["fixed_height_consistency"],
        "orientation_vs_distance": solution["orientation_vs_distance"],
        "leave_one_physical_pose_out": solution["leave_one_physical_pose_out"],
        "W04_reposition_holdout": solution["W04_reposition_holdout"],
        "height_sensitivity": solution["height_sensitivity"],
        "fixture_alignment_uncertainty": solution["fixture_alignment_uncertainty"],
        "sign_and_convention_checks": solution["sign_and_convention_checks"],
        "gates": solution["gates"],
        "old_stage7c": {
            **solution["frozen_authorities"]["old_stage7c_stale_reference"],
            "reused_as_numeric_truth": False,
        },
        "warnings": [
            "Do not reconstruct runtime orientation from diagnostic RPY; use R/T matrices.",
            "This candidate is not promoted and does not validate absolute Geometry.",
            "Stage3B must validate raw Geometry by support_source before stabilizer tuning.",
        ],
    }


def cmd_set_height(args: argparse.Namespace) -> int:
    if args.mm <= 0 or args.uncertainty_mm < 0:
        raise Stage3AError("height must be positive and uncertainty non-negative")
    value = {
        "schema_version": "stage3a_camera_height.v1",
        "camera_height_mm": float(args.mm),
        "camera_height_uncertainty_mm": float(args.uncertainty_mm),
        "measurement_method": args.measurement_method,
        "updated_at": now(),
        "note": args.note,
    }
    write_json(HEIGHT_PATH, value)
    print(json.dumps(value, indent=2, ensure_ascii=False))
    return 0


def cmd_set_board_height(args: argparse.Namespace) -> int:
    if args.active_bottom_mm < 0 or args.uncertainty_mm < 0:
        raise Stage3AError("fixed board active-bottom height and uncertainty must be non-negative")
    requested_height = float(args.active_bottom_mm)
    requested_uncertainty = float(args.uncertainty_mm)
    if FIXED_BOARD_HEIGHT_PATH.exists():
        existing = load_fixed_board_height()
        same = (
            abs(float(existing["fixed_board_active_bottom_height_mm"]) - requested_height) <= 1e-9
            and abs(float(existing["fixed_board_height_uncertainty_mm"]) - requested_uncertainty) <= 1e-9
        )
        if not same:
            raise Stage3AError(
                "fixed board height is already frozen; mismatch refused without overwrite"
            )
        print(json.dumps({"idempotent": True, "fixed_board_height": existing}, indent=2, ensure_ascii=False))
        return 0
    value = default_fixed_board_height()
    value["fixed_board_active_bottom_height_mm"] = requested_height
    value["fixed_board_height_uncertainty_mm"] = requested_uncertainty
    value["measurement_method"] = args.measurement_method
    value["raw_manual_observation_mm"] = args.raw_observation_mm
    write_json(FIXED_BOARD_HEIGHT_PATH, value)
    print(json.dumps({"configured_and_frozen": True, "fixed_board_height": value}, indent=2, ensure_ascii=False))
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    pose_id = args.pose.strip().upper()
    if not pose_id or not pose_id.replace("_", "").isalnum():
        raise Stage3AError("pose ID must contain only letters, digits, or underscore")
    fixed_board = load_fixed_board_height()
    compatibility_height = getattr(args, "board_active_bottom_height_mm", None)
    compatibility_uncertainty = getattr(args, "board_height_uncertainty_mm", None)
    if compatibility_height is not None and abs(
        float(compatibility_height) - float(fixed_board["fixed_board_active_bottom_height_mm"])
    ) > 1e-9:
        raise Stage3AError("register board height mismatches frozen fixed-board height")
    if compatibility_uncertainty is not None and abs(
        float(compatibility_uncertainty) - float(fixed_board["fixed_board_height_uncertainty_mm"])
    ) > 1e-9:
        raise Stage3AError("register board uncertainty mismatches frozen fixed-board uncertainty")
    alignment_delta = getattr(args, "vehicle_alignment_delta_mm", None)
    if alignment_delta is not None and alignment_delta < 0:
        raise Stage3AError("vehicle alignment left/right distance delta must be non-negative")
    registry = load_registry()
    if pose_id in registry["poses"]:
        raise Stage3AError(f"duplicate pose ID refused (silent replace forbidden): {pose_id}")
    scan_dir = resolve_scan(args.scan_id, args.latest)
    hashes = scan_hashes(scan_dir)
    if any(binding.get("scan_id") == scan_dir.name for binding in registry["poses"].values()):
        raise Stage3AError(f"scan already registered to another physical pose: {scan_dir.name}")
    binding = {
        "pose_id": pose_id,
        "scan_id": scan_dir.name,
        "scan_path": str(scan_dir.resolve()),
        "registered_at": now(),
        "board_mode": BOARD_MODE,
        "board_moved_between_poses": False,
        "capture_mode": CAPTURE_MODE,
        "distance_role": POSE_DISTANCE_ROLES.get(pose_id, "supplemental_distance"),
        "fixed_board_height_manifest": str(FIXED_BOARD_HEIGHT_PATH),
        "fixed_board_active_bottom_height_mm": fixed_board["fixed_board_active_bottom_height_mm"],
        "fixed_board_height_uncertainty_mm": fixed_board["fixed_board_height_uncertainty_mm"],
        "vehicle_alignment_delta_mm": alignment_delta,
        "vehicle_alignment_policy": "<=2-3 mm if practical; <=5 mm acceptable and recorded, not a hard rejection gate",
        "capture_note": args.capture_note,
        "source_hashes": hashes,
        "raw_retention": "all 11 formal frames remain at immutable validated scan paths and are hash-bound here",
        "tof_role": "retained for provenance only; excluded from Camera world orientation fit",
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, "would_register": binding}, indent=2, ensure_ascii=False))
        return 0
    registry["poses"][pose_id] = binding
    registry["updated_at"] = now()
    write_json(REGISTRY_PATH, registry)
    write_json(WORKSPACE / "observations" / pose_id / "scan_reference.json", binding)
    print(json.dumps({"registered": binding}, indent=2, ensure_ascii=False))
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    authorities = verify_frozen_authorities()
    registry = load_registry()
    height = load_height()
    fixed_board = load_fixed_board_height()
    output = {
        "stage3a_toolchain_ready": True,
        "workspace": str(WORKSPACE),
        "registered_physical_pose_count": len(registry["poses"]),
        "registered_pose_ids": list(registry["poses"]),
        "camera_height": height,
        "board_mode": BOARD_MODE,
        "board_moved_between_poses": False,
        "capture_mode": CAPTURE_MODE,
        "fixed_board_active_bottom_height_mm": fixed_board["fixed_board_active_bottom_height_mm"],
        "fixed_board_height_uncertainty_mm": fixed_board["fixed_board_height_uncertainty_mm"],
        "fixed_board_measurement_method": fixed_board["measurement_method"],
        "stage2_bundle_sha_verified": authorities["stage2_bundle"]["sha256"],
        "old_stage7c_reused_as_numeric_truth": False,
        "real_data_solved": (DIAGNOSTICS / "solution.json").exists(),
        "candidate_exists": CANDIDATE.exists(),
        "formal_runtime_modified": False,
        "next": suggest(registry),
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    registry = load_registry()
    if args.pose:
        pose_id = args.pose.upper()
        if pose_id not in registry["poses"]:
            raise Stage3AError(f"pose not registered: {pose_id}")
        output = process_pose(pose_id, registry["poses"][pose_id], write=True)
    elif args.scan_id or args.latest:
        scan_dir = resolve_scan(args.scan_id, args.latest)
        output = {
            "scan_id": scan_dir.name,
            "scan_path": str(scan_dir),
            "formal_frame_count": len(validate_scan(scan_dir)),
            "source_hashes": scan_hashes(scan_dir),
            "read_only": True,
            "not_registered": True,
        }
    else:
        output = registry
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


def coverage(
    registry: dict[str, Any], pose_reports: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    pose_reports = pose_reports or {}
    rows = {}
    for pose_id, binding in registry["poses"].items():
        report = pose_reports.get(pose_id)
        rows[pose_id] = {
            "distance_role": binding["distance_role"],
            "estimated_board_plane_distance_mm": (
                None if report is None else pose_estimated_distance_mm(report)
            ),
            "board_center_px_median": (
                None if report is None else report["distance_and_image_coverage"]["board_center_px_median"]
            ),
            "board_coverage_fraction_median": (
                None if report is None else report["distance_and_image_coverage"]["board_coverage_fraction_median"]
            ),
            "reprojection": None if report is None else report["reprojection"],
            "orientation_pose_mean": None if report is None else report["R_world_from_camera_pose_mean"],
            "vehicle_alignment_delta_mm": binding.get("vehicle_alignment_delta_mm"),
        }
    primary_complete = all(pose_id in registry["poses"] for pose_id in PRIMARY_POSE_IDS)
    result = {
        "board_mode": BOARD_MODE,
        "capture_mode": CAPTURE_MODE,
        "physical_pose_count": len(registry["poses"]),
        "pose_ids": list(registry["poses"]),
        "per_pose": rows,
        "distance_bins": {"W01": "near ~600 mm", "W02": "mid ~800 mm", "W03": "far ~1000 mm"},
        "initial_capture_target": "fixed board; vehicle at near, mid, far; optional W04 vehicle reposition holdout",
        "dynamic_registry_no_pose_limit": True,
        "primary_pose_set_complete": primary_complete,
        "enough_for_first_solve": primary_complete,
    }
    if primary_complete and all(pose_id in pose_reports for pose_id in PRIMARY_POSE_IDS):
        primary_reports = {pose_id: pose_reports[pose_id] for pose_id in PRIMARY_POSE_IDS}
        primary_rotations = pose_rotations(primary_reports)
        aggregate_R = robust_rotation_mean(list(primary_rotations.values()), minimum=3)[0]
        result["orientation_vs_distance"] = orientation_vs_distance(
            primary_rotations, primary_reports, aggregate_R
        )
        result["fixed_height_consistency_vs_distance"] = fixed_height_residuals(
            aggregate_R,
            primary_reports,
            load_fixed_board_height(),
            float(load_height()["camera_height_mm"]),
        )
    else:
        result["orientation_vs_distance"] = None
        result["fixed_height_consistency_vs_distance"] = None
    return result


def cmd_coverage(_: argparse.Namespace) -> int:
    registry = load_registry()
    reports = {
        pose_id: process_pose(pose_id, binding, write=True)
        for pose_id, binding in registry["poses"].items()
    }
    output = coverage(registry, reports)
    write_json(DIAGNOSTICS / "coverage.json", output)
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


def suggest(registry: dict[str, Any]) -> dict[str, Any]:
    if "W01" not in registry["poses"]:
        return {
            "decision": "NEEDS_ONE_MORE_POSE",
            "instruction": (
                "Capture W01 near (~0.6 m): ChArUco remains permanently fixed; place the vehicle near the cabinet, "
                "equalize left/right symmetric vehicle-reference distances for yaw, wait 2-3 s, capture one formal 11-frame scan."
            ),
        }
    if "W02" not in registry["poses"]:
        return {
            "decision": "NEEDS_ONE_MORE_POSE",
            "instruction": (
                "Move the vehicle backward only to W02 mid (~0.8 m); ChArUco remains fixed. "
                "Realign vehicle yaw from equal left/right reference distances, wait 2-3 s, capture one scan."
            ),
        }
    if "W03" not in registry["poses"]:
        return {
            "decision": "NEEDS_ONE_MORE_POSE",
            "instruction": (
                "Move the vehicle backward only to W03 far (~1.0 m); ChArUco remains fixed. "
                "Realign vehicle yaw, wait 2-3 s, capture one scan."
            ),
        }
    try:
        solution = solve_registered(write=False)
    except Stage3AError as exc:
        return {"decision": "UNRESOLVED", "instruction": f"Inspect/replace the weak pose before adding data: {exc}"}
    if solution["decision"] == "EVIDENCE_READY_FOR_OWNER_JUDGMENT":
        if "W04" in registry["poses"]:
            return {"decision": "EVIDENCE_READY_FOR_OWNER_JUDGMENT", "instruction": "Fixed-board multi-distance evidence and W04 holdout are stable; stop Stage3A capture for owner judgment."}
        return {
            "decision": "EVIDENCE_READY_FOR_OWNER_JUDGMENT",
            "instruction": "W01/W02/W03 are stable. W04 is optional: drive away, return the vehicle to ~0.8 m, redo yaw alignment, then capture; ChArUco remains fixed.",
        }
    weakest = max(
        solution["per_pose_orientation"],
        key=lambda pose_id: solution["per_pose_orientation"][pose_id]["residual_to_final_deg"],
    )
    return {
        "decision": "NEEDS_ONE_MORE_POSE",
        "instruction": (
            f"Inspect weak pose {weakest}; return the vehicle to ~0.8 m and capture W04 after fresh yaw alignment "
            "if reposition repeatability evidence is needed. ChArUco remains fixed."
        ),
    }


def cmd_suggest(_: argparse.Namespace) -> int:
    output = suggest(load_registry())
    write_json(DIAGNOSTICS / "suggest_next.json", output)
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


def cmd_solve(_: argparse.Namespace) -> int:
    solution = solve_registered(write=True)
    print(json.dumps(solution, indent=2, ensure_ascii=False))
    return 0 if solution["world_pose_resolved"] else 2


def cmd_validate(_: argparse.Namespace) -> int:
    solution = solve_registered(write=True)
    output = {
        "decision": solution["decision"],
        "world_pose_resolved": solution["world_pose_resolved"],
        "absolute_geometry_valid": False,
        "gates": solution["gates"],
        "distance_coverage": solution["distance_coverage_diagnostics"],
        "fixed_height_consistency": solution["fixed_height_consistency"],
        "orientation_vs_distance": solution["orientation_vs_distance"],
        "sign_and_convention_checks": solution["sign_and_convention_checks"],
        "lopo": solution["leave_one_physical_pose_out"],
        "holdout": solution["W04_reposition_holdout"],
        "height_sensitivity": solution["height_sensitivity"],
    }
    write_json(VALIDATION / "validation_summary.json", output)
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0 if solution["world_pose_resolved"] else 2


def cmd_summary(_: argparse.Namespace) -> int:
    registry = load_registry()
    if (DIAGNOSTICS / "solution.json").exists():
        solution = read_json(DIAGNOSTICS / "solution.json")
        output = {
            "decision": solution["decision"],
            "world_pose_resolved": solution["world_pose_resolved"],
            "absolute_geometry_valid": False,
            "physical_pose_count": len(registry["poses"]),
            "R_world_from_camera": solution["R_world_from_camera"],
            "t_world_from_camera_mm": solution["t_world_from_camera_mm"],
            "candidate_exists": CANDIDATE.exists(),
            "promotion": False,
            "board_mode": BOARD_MODE,
            "capture_mode": CAPTURE_MODE,
        }
    else:
        output = {
            "decision": "UNRESOLVED" if not registry["poses"] else "NEEDS_ONE_MORE_POSE",
            "world_pose_resolved": False,
            "absolute_geometry_valid": False,
            "physical_pose_count": len(registry["poses"]),
            "candidate_exists": False,
            "promotion": False,
            "board_mode": BOARD_MODE,
            "capture_mode": CAPTURE_MODE,
            "next": suggest(registry),
        }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


def cmd_export(_: argparse.Namespace) -> int:
    solution = solve_registered(write=True)
    candidate = candidate_from_solution(solution, load_registry())
    write_json(CANDIDATE, candidate)
    digest = sha256_file(CANDIDATE)
    (CANDIDATE.parent / "candidate_file_sha256.txt").write_text(digest + "\n", encoding="ascii")
    print(json.dumps({"candidate": str(CANDIDATE), "sha256": digest, "status": candidate["candidate_status"]}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stage3A final-body Camera->World matrix-first tool. FIXED_VERTICAL_BOARD at frozen active-bottom "
            "height; vehicle captures near/mid/far. The ChArUco is never moved. No promotion/runtime writes."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)
    item = sub.add_parser("status", help="show frozen bindings and dynamic registry status")
    item.set_defaults(func=cmd_status)
    item = sub.add_parser("set-height", help="set manual optical-centre height and uncertainty")
    item.add_argument("--mm", type=float, required=True)
    item.add_argument("--uncertainty-mm", type=float, required=True)
    item.add_argument("--measurement-method", default="manual_tape_visual")
    item.add_argument("--note", default="user-supplied final-body optical-centre height")
    item.set_defaults(func=cmd_set_height)
    item = sub.add_parser("set-board-height", help="configure fixed active-bottom height once; later mismatches fail closed")
    item.add_argument("--active-bottom-mm", type=float, required=True)
    item.add_argument("--uncertainty-mm", type=float, required=True)
    item.add_argument("--measurement-method", default="manual_tape_visual")
    item.add_argument("--raw-observation-mm", type=float, default=911.5)
    item.set_defaults(func=cmd_set_board_height)
    item = sub.add_parser("register", help="bind a unique physical pose to a complete formal 11-frame scan")
    item.add_argument("--pose", required=True)
    choice = item.add_mutually_exclusive_group(required=True)
    choice.add_argument("--latest", action="store_true")
    choice.add_argument("--scan-id")
    item.add_argument(
        "--board-active-bottom-height-mm",
        "--board-bottom-height-mm",
        dest="board_active_bottom_height_mm",
        type=float,
        help="compatibility assertion only; if supplied it must equal frozen 912 mm",
    )
    item.add_argument("--board-height-uncertainty-mm", type=float, help="compatibility assertion only; must equal frozen uncertainty")
    item.add_argument("--vehicle-alignment-delta-mm", type=float, help="absolute left/right symmetric reference distance difference")
    item.add_argument("--capture-note")
    item.add_argument("--dry-run", action="store_true")
    item.set_defaults(func=cmd_register)
    item = sub.add_parser("inspect", help="inspect registry, one pose, or one validated scan read-only")
    item.add_argument("--pose")
    choice = item.add_mutually_exclusive_group()
    choice.add_argument("--latest", action="store_true")
    choice.add_argument("--scan-id")
    item.set_defaults(func=cmd_inspect)
    item = sub.add_parser("coverage", help="show PnP near/mid/far distance, image coverage, and pose quality")
    item.set_defaults(func=cmd_coverage)
    item = sub.add_parser("solve", help="solve matrix-first orientation from >=3 equally weighted physical poses")
    item.set_defaults(func=cmd_solve)
    item = sub.add_parser("validate", help="run fixed-height-vs-distance, orientation, LOPO/holdout, sensitivity, and sign diagnostics")
    item.set_defaults(func=cmd_validate)
    item = sub.add_parser("summary", help="summarize current unresolved/solved state")
    item.set_defaults(func=cmd_summary)
    item = sub.add_parser("suggest-next", help="recommend the minimum useful next physical pose")
    item.set_defaults(func=cmd_suggest)
    item = sub.add_parser("export-candidate", help="export only at EVIDENCE_READY_FOR_OWNER_JUDGMENT; never promotes")
    item.set_defaults(func=cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.command == "inspect" and args.pose and (args.latest or args.scan_id):
            raise Stage3AError("inspect accepts either --pose or a scan selector, not both")
        return int(args.func(args))
    except Stage3AError as exc:
        print(json.dumps({"error": str(exc), "type": "Stage3AError"}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
