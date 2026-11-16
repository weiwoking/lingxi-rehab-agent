from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
from scipy.spatial.transform import Rotation


WORKSPACE = Path(__file__).resolve().parent
STAGE2_BUNDLE = Path(
    r"C:\ec\_calibration_workspaces\stage2_final_bundle_20260818_01"
    r"\calibration_bundle.stage2_final_candidate.v1.json"
)
LOPO_CSV = Path(
    r"C:\ec\_calibration_workspaces\stage2a_camera_intrinsic_20260817_01"
    r"\stability_diagnostics\lopo\lopo_per_pose.csv"
)
REGISTRY = WORKSPACE / "observations" / "registry.json"
POSE_IDS = ("W01", "W02", "W03")
WIDTH, HEIGHT = 640, 360
BOARD_WIDTH_M, BOARD_HEIGHT_M = 0.175, 0.125
CAMERA_HEIGHT_MM, BOARD_BOTTOM_MM = 972.0, 912.0
EXPECTED_STAGE2_SHA256 = "63c974cd2ff64dd0f135d47d8c92eb170d06658cb315563fb93588862ba8b057"

# This is intentionally permissive. The value is selected by study(), not by a
# field-angle requirement: through this region, straightening has little effect
# on the current gravity/height chain compared with realistic corner noise.
# Synthetic p95 height impact stays below 10 mm/m through the tested 3-degree
# condition. A 0.25-degree image-model guard (larger than the current LOPO
# half-range at the borderline W01 pose) prevents a meaningless hairline flip.
STRAIGHT_ENOUGH_ABS_PROXY_DEG = 3.25
BOARD_TOO_SMALL_SHORT_EDGE_PX = 55.0
BOARD_CLOSE_EDGE_MARGIN_PX = 12.0
BOARD_OFF_CENTER_FRACTION = 0.18
MIN_CORNERS = 6

R_WORLD_FROM_BOARD_FRONTAL = np.asarray(
    [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]], dtype=np.float64
)
OUTLINE_XY = np.asarray(
    [[0.0, 0.0], [BOARD_WIDTH_M, 0.0], [BOARD_WIDTH_M, BOARD_HEIGHT_M], [0.0, BOARD_HEIGHT_M]],
    dtype=np.float64,
)
BOARD_CENTER = np.asarray([BOARD_WIDTH_M / 2.0, BOARD_HEIGHT_M / 2.0, 0.0], dtype=np.float64)
BOTTOM_POINTS = np.asarray(
    [[0.0, BOARD_HEIGHT_M, 0.0], [BOARD_WIDTH_M, BOARD_HEIGHT_M, 0.0]], dtype=np.float64
)


@dataclass(frozen=True)
class DetectedFrame:
    name: str
    object_points: np.ndarray
    image_points: np.ndarray
    ids: np.ndarray
    marker_count: int


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def matrices(params: dict[str, float]) -> tuple[np.ndarray, np.ndarray]:
    K = np.asarray(
        [[params["fx"], 0.0, params["cx"]], [0.0, params["fy"], params["cy"]], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    D = np.asarray([params[key] for key in ("k1", "k2", "p1", "p2", "k3")], dtype=np.float64)
    return K, D


def nominal_camera_model() -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    bundle = read_json(STAGE2_BUNDLE)
    intrinsics = bundle["camera_intrinsics"]["camera"]["intrinsics"]
    params = {
        "fx": float(intrinsics["fx"]),
        "fy": float(intrinsics["fy"]),
        "cx": float(intrinsics["cx"]),
        "cy": float(intrinsics["cy"]),
        **{key: float(value) for key, value in intrinsics["distortion"].items()},
    }
    K, D = matrices(params)
    return K, D, params


def lopo_camera_models() -> list[tuple[str, np.ndarray, np.ndarray]]:
    models: list[tuple[str, np.ndarray, np.ndarray]] = []
    with LOPO_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            params = {key: float(row[key]) for key in ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3")}
            K, D = matrices(params)
            models.append((f"lopo_remove_{row['removed_pose_id']}", K, D))
    return models


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


def read_image(path: Path) -> np.ndarray:
    if path.suffix.lower() == ".rgb565":
        values = np.fromfile(path, dtype="<u2")
        if values.size != WIDTH * HEIGHT:
            raise ValueError(f"RGB565 image must contain {WIDTH}x{HEIGHT} pixels: {path}")
        values = values.reshape(HEIGHT, WIDTH)
        red = (((values >> 11) & 0x1F) * 255 + 15) // 31
        green = (((values >> 5) & 0x3F) * 255 + 31) // 63
        blue = ((values & 0x1F) * 255 + 15) // 31
        return np.dstack((blue, green, red)).astype(np.uint8)
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"unable to read image: {path}")
    if image.shape[:2] != (HEIGHT, WIDTH):
        raise ValueError(f"image must be {WIDTH}x{HEIGHT} for the frozen camera model: {path}")
    return image


def detect_frame(path: Path, board: Any, detector: Any) -> DetectedFrame | None:
    corners, ids, _, marker_ids = detector.detectBoard(read_image(path))
    if corners is None or ids is None or len(ids) < MIN_CORNERS:
        return None
    objects, images = board.matchImagePoints(corners, ids)
    return DetectedFrame(
        name=path.parent.name if path.name == "image.rgb565" else path.name,
        object_points=np.asarray(objects, dtype=np.float64).reshape(-1, 3),
        image_points=np.asarray(images, dtype=np.float64).reshape(-1, 2),
        ids=np.asarray(ids, dtype=np.int32).reshape(-1),
        marker_count=0 if marker_ids is None else int(len(marker_ids)),
    )


def scan_images(scan: Path) -> list[Path]:
    frames = sorted((scan / "frames").glob("frame_*/image.rgb565"))
    if frames:
        return frames
    images: list[Path] = []
    for pattern in ("*.png", "*.jpg", "*.jpeg", "*.bmp", "*.rgb565"):
        images.extend(scan.glob(pattern))
    return sorted(set(images))


def detect_scan(scan: Path) -> tuple[list[DetectedFrame], int]:
    board, detector = board_and_detector()
    images = scan_images(scan)
    frames = [item for path in images if (item := detect_frame(path, board, detector)) is not None]
    return frames, len(images)


def _line_intersection(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> list[float] | None:
    first = np.cross(np.r_[a, 1.0], np.r_[b, 1.0])
    second = np.cross(np.r_[c, 1.0], np.r_[d, 1.0])
    point = np.cross(first, second)
    if abs(float(point[2])) < 1e-12:
        return None
    return (point[:2] / point[2]).astype(float).tolist()


def frame_geometry(frame: DetectedFrame, K: np.ndarray, D: np.ndarray) -> dict[str, Any]:
    undistorted = cv2.undistortPoints(frame.image_points.reshape(-1, 1, 2), K, D, P=K).reshape(-1, 2)
    H, _ = cv2.findHomography(frame.object_points[:, :2], undistorted, method=0)
    if H is None:
        raise ValueError(f"homography failed: {frame.name}")
    outline = cv2.perspectiveTransform(OUTLINE_XY.reshape(-1, 1, 2), H).reshape(-1, 2)
    predicted = cv2.perspectiveTransform(frame.object_points[:, :2].reshape(-1, 1, 2), H).reshape(-1, 2)
    homography_rms = float(np.sqrt(np.mean(np.sum((predicted - undistorted) ** 2, axis=1))))

    top_vector = outline[1] - outline[0]
    right_vector = outline[2] - outline[1]
    bottom_vector = outline[2] - outline[3]
    left_vector = outline[3] - outline[0]
    top = float(np.linalg.norm(top_vector))
    bottom = float(np.linalg.norm(bottom_vector))
    left = float(np.linalg.norm(left_vector))
    right = float(np.linalg.norm(right_vector))

    calibrated = np.linalg.inv(K) @ H
    if calibrated[2, 2] < 0:
        calibrated = -calibrated
    scale = 2.0 / (np.linalg.norm(calibrated[:, 0]) + np.linalg.norm(calibrated[:, 1]))
    r1 = calibrated[:, 0] * scale
    r2 = calibrated[:, 1] * scale
    r1 /= np.linalg.norm(r1)
    r2 -= r1 * float(np.dot(r1, r2))
    r2 /= np.linalg.norm(r2)
    provisional = np.column_stack((r1, r2, np.cross(r1, r2)))
    u, _, vt = np.linalg.svd(provisional)
    R_h = u @ vt
    if np.linalg.det(R_h) < 0:
        u[:, -1] *= -1
        R_h = u @ vt
    t_h = calibrated[:, 2] * scale
    center_depth = float((R_h @ BOARD_CENTER + t_h)[2])

    # Positive means the camera/vehicle optical axis is aimed to image/world right
    # relative to the board, matching the existing Stage3A PnP yaw sign.
    depth_gradient_proxy_deg = math.degrees(math.atan2(float(R_h[2, 0]), float(R_h[0, 0])))
    edge_asymmetry = (left - right) / max(1e-12, 0.5 * (left + right))
    edge_r1z = float(np.clip(edge_asymmetry * center_depth / BOARD_WIDTH_M, -0.999999, 0.999999))
    edge_proxy_deg = math.degrees(math.asin(edge_r1z))
    signed_proxy_deg = float(np.median([depth_gradient_proxy_deg, edge_proxy_deg]))

    ok, rvec, tvec = cv2.solvePnP(
        frame.object_points, frame.image_points, K, D, flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not ok:
        pnp_yaw = None
        normal_x = None
    else:
        R_pnp, _ = cv2.Rodrigues(rvec)
        normal_x = float(R_pnp[0, 2])
        pnp_yaw = math.degrees(math.atan2(-normal_x, float(R_pnp[2, 2])))

    center = outline.mean(axis=0)
    margin = float(
        min(outline[:, 0].min(), WIDTH - 1 - outline[:, 0].max(), outline[:, 1].min(), HEIGHT - 1 - outline[:, 1].max())
    )
    vanishing = _line_intersection(outline[0], outline[1], outline[3], outline[2])
    return {
        "frame": frame.name,
        "corner_count": int(len(frame.ids)),
        "marker_count": frame.marker_count,
        "homography_rms_px": homography_rms,
        "outer_quadrilateral_px": outline.tolist(),
        "left_edge_length_px": left,
        "right_edge_length_px": right,
        "top_edge_length_px": top,
        "bottom_edge_length_px": bottom,
        "left_right_edge_ratio": left / right,
        "top_bottom_width_ratio": top / bottom,
        "horizontal_vanishing_point_px": vanishing,
        "horizontal_vanishing_convergence": float(R_h[2, 0] / max(1e-12, abs(R_h[0, 0]))),
        "quadrilateral_horizontal_trapezoid_asymmetry": edge_asymmetry,
        "normalized_homography_perspective_x": float(R_h[2, 0]),
        "board_normal_horizontal_component": float(R_h[0, 2]),
        "board_center_x_px": float(center[0]),
        "board_center_y_px": float(center[1]),
        "board_center_x_offset_from_image_center_px": float(center[0] - WIDTH / 2.0),
        "minimum_image_edge_margin_px": margin,
        "short_edge_px": min(left, right),
        "depth_gradient_proxy_deg": depth_gradient_proxy_deg,
        "edge_asymmetry_proxy_deg": edge_proxy_deg,
        "signed_straightness_proxy_deg": signed_proxy_deg,
        "pnp_board_normal_yaw_aux_deg": pnp_yaw,
        "pnp_board_normal_x_aux": normal_x,
    }


def robust_stats(values: Iterable[float]) -> dict[str, float]:
    a = np.asarray(list(values), dtype=np.float64)
    median = float(np.median(a))
    return {
        "median": median,
        "mad": float(np.median(np.abs(a - median))),
        "std": float(np.std(a)),
        "min": float(np.min(a)),
        "max": float(np.max(a)),
    }


def action_for(decision: str) -> str:
    return {
        "STRAIGHT_ENOUGH": "Proceed; no yaw correction is needed.",
        "TOO_LEFT": "Steer the front slightly right until the board shape becomes more symmetric.",
        "TOO_RIGHT": "Steer the front slightly left until the board shape becomes more symmetric.",
        "BOARD_OFF_CENTER_BUT_STRAIGHT": "No yaw correction needed; lateral centering is optional.",
        "BOARD_TOO_SMALL": "Move close enough that the board is clearly detected, then score again.",
        "BOARD_TOO_CLOSE_TO_EDGE": "Shift the view so the whole board has a clear image margin, then score again.",
        "DETECTION_WEAK": "Improve board visibility and lighting, then score again.",
        "UNRESOLVED": "Use another still frame with the full board clearly visible.",
    }[decision]


def aggregate_score(
    frames: list[DetectedFrame], K: np.ndarray, D: np.ndarray, total_input_frames: int | None = None
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for frame in frames:
        try:
            rows.append(frame_geometry(frame, K, D))
        except (cv2.error, ValueError, np.linalg.LinAlgError):
            continue
    total = len(frames) if total_input_frames is None else total_input_frames
    valid_fraction = len(rows) / max(1, total)
    if not rows:
        decision = "DETECTION_WEAK"
        return {
            "decision": decision,
            "straightness_score_0_100": 0.0,
            "confidence": "LOW",
            "action": action_for(decision),
            "valid_frame_count": 0,
            "input_frame_count": total,
            "frames": [],
        }

    proxy = robust_stats(row["signed_straightness_proxy_deg"] for row in rows)
    center_x = robust_stats(row["board_center_x_px"] for row in rows)
    short_edge = robust_stats(row["short_edge_px"] for row in rows)
    margins = robust_stats(row["minimum_image_edge_margin_px"] for row in rows)
    homography_rms = robust_stats(row["homography_rms_px"] for row in rows)
    corners = robust_stats(float(row["corner_count"]) for row in rows)
    abs_proxy = abs(proxy["median"])
    score = float(100.0 * math.exp(-math.log(2.0) * (abs_proxy / STRAIGHT_ENOUGH_ABS_PROXY_DEG) ** 2))

    if valid_fraction < 0.6 or corners["median"] < MIN_CORNERS or homography_rms["median"] > 0.8:
        decision = "DETECTION_WEAK"
    elif short_edge["median"] < BOARD_TOO_SMALL_SHORT_EDGE_PX:
        decision = "BOARD_TOO_SMALL"
    elif margins["median"] < BOARD_CLOSE_EDGE_MARGIN_PX:
        decision = "BOARD_TOO_CLOSE_TO_EDGE"
    elif abs_proxy > STRAIGHT_ENOUGH_ABS_PROXY_DEG:
        decision = "TOO_RIGHT" if proxy["median"] > 0 else "TOO_LEFT"
    elif abs(center_x["median"] - WIDTH / 2.0) > WIDTH * BOARD_OFF_CENTER_FRACTION:
        decision = "BOARD_OFF_CENTER_BUT_STRAIGHT"
    else:
        decision = "STRAIGHT_ENOUGH"

    spread = 1.4826 * proxy["mad"]
    confidence_value = min(1.0, valid_fraction) * min(1.0, corners["median"] / 12.0)
    confidence_value *= math.exp(-max(0.0, homography_rms["median"] - 0.15))
    confidence_value *= math.exp(-spread / 3.0)
    confidence = "HIGH" if confidence_value >= 0.72 else "MEDIUM" if confidence_value >= 0.45 else "LOW"
    if decision in {"DETECTION_WEAK", "UNRESOLVED"}:
        confidence = "LOW"
    return {
        "schema_version": "stage3a_image_straightness_score.v1",
        "decision": decision,
        "straightness_score_0_100": score,
        "confidence": confidence,
        "confidence_value_0_1": float(confidence_value),
        "action": action_for(decision),
        "valid_frame_count": len(rows),
        "input_frame_count": total,
        "valid_frame_fraction": valid_fraction,
        "straight_enough_region_internal": {
            "absolute_signed_proxy_limit_deg": STRAIGHT_ENOUGH_ABS_PROXY_DEG,
            "basis": "synthetic paired height/gravity impact plus current-image robustness",
        },
        "aggregate": {
            "signed_straightness_proxy_deg": proxy,
            "board_center_x_px": center_x,
            "short_edge_px": short_edge,
            "minimum_image_edge_margin_px": margins,
            "homography_rms_px": homography_rms,
            "corner_count": corners,
        },
        "method": {
            "primary": "undistorted outer-quadrilateral homography depth gradient plus edge asymmetry",
            "pnp_role": "board-normal auxiliary only",
            "center_x_role": "lateral-placement decision only; never used as yaw evidence",
        },
        "frames": rows,
    }


def print_human(score: dict[str, Any], label: str | None = None) -> None:
    print(f"decision = {score['decision']}")
    if label:
        print(f"pose = {label}")
    print(f"straightness = {score['straightness_score_0_100']:.1f}/100")
    print(f"confidence = {score['confidence']}")
    print(f"action = {score['action']}")


def collect_existing() -> tuple[dict[str, list[DetectedFrame]], dict[str, Any]]:
    registry = read_json(REGISTRY)
    board, detector = board_and_detector()
    result: dict[str, list[DetectedFrame]] = {}
    for pose in POSE_IDS:
        scan = Path(registry["poses"][pose]["scan_path"])
        result[pose] = [
            item for path in scan_images(scan) if (item := detect_frame(path, board, detector)) is not None
        ]
    return result, registry


def score_existing(include_sensitivity: bool = True) -> dict[str, Any]:
    detected, registry = collect_existing()
    K, D, _ = nominal_camera_model()
    scores = {}
    for pose in POSE_IDS:
        scan = Path(registry["poses"][pose]["scan_path"])
        scores[pose] = aggregate_score(detected[pose], K, D, len(scan_images(scan)))

    sensitivity: dict[str, Any] = {"evaluated": False}
    if include_sensitivity:
        rows: dict[str, list[dict[str, Any]]] = {pose: [] for pose in POSE_IDS}
        for label, model_K, model_D in lopo_camera_models():
            for pose in POSE_IDS:
                score = aggregate_score(detected[pose], model_K, model_D, len(detected[pose]))
                rows[pose].append(
                    {
                        "model": label,
                        "decision": score["decision"],
                        "signed_proxy_deg": score["aggregate"]["signed_straightness_proxy_deg"]["median"],
                    }
                )
        sensitivity = {
            "evaluated": True,
            "model_count": len(lopo_camera_models()),
            "per_pose": {
                pose: {
                    "decision_set": sorted({row["decision"] for row in rows[pose]}),
                    "all_decisions_match_nominal": all(row["decision"] == scores[pose]["decision"] for row in rows[pose]),
                    "signed_proxy_deg": robust_stats(row["signed_proxy_deg"] for row in rows[pose]),
                }
                for pose in POSE_IDS
            },
        }
    return {
        "schema_version": "stage3a_existing_straightness.v1",
        "stage2_sha256": sha256(STAGE2_BUNDLE),
        "registry_sha256": sha256(REGISTRY),
        "scan_ids": {pose: registry["poses"][pose]["scan_id"] for pose in POSE_IDS},
        "scores": scores,
        "w03_is_image_frontal": scores["W03"]["decision"] in {"STRAIGHT_ENOUGH", "BOARD_OFF_CENTER_BUT_STRAIGHT"},
        "intrinsics_ensemble_sensitivity": sensitivity,
        "writes_or_capture_performed": False,
    }


def chordal_mean(rotations: list[np.ndarray]) -> np.ndarray:
    u, _, vt = np.linalg.svd(np.sum(rotations, axis=0))
    result = u @ vt
    if np.linalg.det(result) < 0:
        u[:, -1] *= -1
        result = u @ vt
    return result


def camera_orientation(R_wc: np.ndarray) -> dict[str, float]:
    optical = R_wc[:, 2]
    return {
        "pitch_deg": math.degrees(math.atan2(float(optical[2]), float(np.hypot(optical[0], optical[1])))),
        "yaw_deg": math.degrees(math.atan2(float(optical[0]), float(optical[1]))),
    }


def _synthetic_detected(
    object_points: np.ndarray,
    distance_m: float,
    yaw_deg: float,
    lateral_m: float,
    noise: np.ndarray,
    K: np.ndarray,
    D: np.ndarray,
    name: str,
) -> tuple[DetectedFrame, np.ndarray, np.ndarray]:
    R_cb = Rotation.from_euler("y", -yaw_deg, degrees=True).as_matrix()
    center_camera = np.asarray([lateral_m, -0.0025, distance_m], dtype=np.float64)
    t_cb = center_camera - R_cb @ BOARD_CENTER
    rvec, _ = cv2.Rodrigues(R_cb)
    image, _ = cv2.projectPoints(object_points, rvec, t_cb, K, D)
    image = image.reshape(-1, 2) + noise
    ids = np.arange(len(object_points), dtype=np.int32)
    frame = DetectedFrame(name, object_points, image, ids, marker_count=17)
    return frame, R_cb, t_cb


def synthetic_single_score(
    yaw_deg: float,
    lateral_m: float,
    distance_m: float,
    noise_px: float = 0.0,
    seed: int = 1,
) -> dict[str, Any]:
    K, D, _ = nominal_camera_model()
    board, _ = board_and_detector()
    objects = np.asarray(board.getChessboardCorners(), dtype=np.float64).reshape(-1, 3)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, noise_px, (len(objects), 2))
    frame, _, _ = _synthetic_detected(objects, distance_m, yaw_deg, lateral_m, noise, K, D, "synthetic")
    return aggregate_score([frame], K, D, 1)


def _solve_frame(frame: DetectedFrame, K: np.ndarray, D: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ok, rvec, tvec = cv2.solvePnP(
        frame.object_points, frame.image_points, K, D, flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not ok:
        raise RuntimeError("synthetic solvePnP failed")
    R_cb, _ = cv2.Rodrigues(rvec)
    return R_cb, np.asarray(tvec, dtype=np.float64).reshape(3)


def _simulate_series(
    yaws: list[float],
    laterals: list[float],
    noise_px: float,
    seed: int,
    visibility: dict[str, list[np.ndarray]],
    K: np.ndarray,
    D: np.ndarray,
    objects: np.ndarray,
    frames_per_pose: int = 5,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    distances = [0.6, 0.8, 1.0]
    rows: list[dict[str, Any]] = []
    for pose_index, (pose, distance, yaw, lateral) in enumerate(zip(POSE_IDS, distances, yaws, laterals)):
        choices = visibility[pose]
        for frame_index in range(frames_per_pose):
            ids = choices[frame_index % len(choices)]
            selected = objects[ids]
            noise = rng.normal(0.0, noise_px, (len(selected), 2))
            frame, _, _ = _synthetic_detected(
                selected, distance, yaw, lateral, noise, K, D, f"{pose}_{frame_index}"
            )
            R_cb, t_cb = _solve_frame(frame, K, D)
            rows.append({"pose_index": pose_index, "R_cb": R_cb, "t_cb": t_cb, "proxy": frame_geometry(frame, K, D)["signed_straightness_proxy_deg"]})

    all_R_wc = [R_WORLD_FROM_BOARD_FRONTAL @ row["R_cb"].T for row in rows]
    aggregate_R_wc = chordal_mean(all_R_wc)
    residuals: list[float] = []
    pitches: list[float] = []
    inferred_yaws: list[float] = []
    proxies: list[float] = []
    for pose_index in range(3):
        selected = [row for row in rows if row["pose_index"] == pose_index]
        pose_R_wc = chordal_mean([R_WORLD_FROM_BOARD_FRONTAL @ row["R_cb"].T for row in selected])
        orientation = camera_orientation(pose_R_wc)
        pitches.append(orientation["pitch_deg"])
        inferred_yaws.append(orientation["yaw_deg"])
        proxies.append(float(np.median([row["proxy"] for row in selected])))
        heights = []
        for row in selected:
            p_camera = (row["R_cb"] @ BOTTOM_POINTS.T).T + row["t_cb"]
            z_world = (aggregate_R_wc @ p_camera.T).T[:, 2] * 1000.0 + CAMERA_HEIGHT_MM
            heights.append(float(np.mean(z_world)))
        residuals.append(float(np.mean(heights) - BOARD_BOTTOM_MM))
    return {
        "height_residuals_mm": residuals,
        "height_slope_mm_per_m": float(np.polyfit(distances, residuals, 1)[0]),
        "inferred_pitch_deg": pitches,
        "inferred_pitch_span_deg": float(max(pitches) - min(pitches)),
        "inferred_yaw_deg": inferred_yaws,
        "image_proxy_deg": proxies,
    }


def synthetic_study(repetitions: int = 12) -> dict[str, Any]:
    K, D, _ = nominal_camera_model()
    board, _ = board_and_detector()
    objects = np.asarray(board.getChessboardCorners(), dtype=np.float64).reshape(-1, 3)
    existing, _ = collect_existing()
    visibility = {pose: [frame.ids.copy() for frame in existing[pose]] for pose in POSE_IDS}
    amplitudes = [0.0, 0.5, 1.0, 2.0, 3.0, 5.0]
    noises = [0.05, 0.15, 0.30]
    patterns = {
        "constant": lambda a: [a, a, a],
        "current_like": lambda a: [a, 0.2 * a, 0.0],
        "alternating": lambda a: [a, -a, a],
        "reverse": lambda a: [0.0, 0.2 * a, a],
    }
    lateral_patterns = ([0.0, 0.0, 0.0], [-0.08, 0.0, 0.08], [0.08, 0.0, -0.08])
    by_amplitude: dict[str, dict[str, list[float]]] = {
        str(a): {"paired_slope": [], "raw_slope": [], "paired_pitch": [], "proxy_error": [], "yaw_error": []}
        for a in amplitudes
    }
    worst: dict[str, Any] | None = None
    scenario_count = 0
    for amplitude in amplitudes:
        for pattern_index, (pattern_name, make_pattern) in enumerate(patterns.items()):
            for sign in (-1.0, 1.0):
                if amplitude == 0.0 and sign > 0:
                    continue
                yaws = [sign * value for value in make_pattern(amplitude)]
                for noise_index, noise in enumerate(noises):
                    for repeat in range(repetitions):
                        laterals = list(lateral_patterns[repeat % len(lateral_patterns)])
                        seed = 1_000_000 + int(amplitude * 10_000) + pattern_index * 1000 + noise_index * 100 + repeat
                        current = _simulate_series(yaws, laterals, noise, seed, visibility, K, D, objects)
                        aligned = _simulate_series([0.0, 0.0, 0.0], laterals, noise, seed, visibility, K, D, objects)
                        paired_slope = abs(current["height_slope_mm_per_m"] - aligned["height_slope_mm_per_m"])
                        paired_pitch = abs(current["inferred_pitch_span_deg"] - aligned["inferred_pitch_span_deg"])
                        bucket = by_amplitude[str(amplitude)]
                        bucket["paired_slope"].append(paired_slope)
                        bucket["raw_slope"].append(abs(current["height_slope_mm_per_m"]))
                        bucket["paired_pitch"].append(paired_pitch)
                        bucket["proxy_error"].extend(abs(p - y) for p, y in zip(current["image_proxy_deg"], yaws))
                        bucket["yaw_error"].extend(abs(p - y) for p, y in zip(current["inferred_yaw_deg"], yaws))
                        scenario_count += 1
                        if worst is None or paired_slope > worst["paired_height_slope_delta_mm_per_m"]:
                            worst = {
                                "amplitude_deg": amplitude,
                                "pattern": pattern_name,
                                "signed_true_yaws_deg": yaws,
                                "noise_px": noise,
                                "laterals_m": laterals,
                                "paired_height_slope_delta_mm_per_m": paired_slope,
                                "current": current,
                                "aligned_same_noise": aligned,
                            }

    summaries = {}
    qualified = []
    for amplitude in amplitudes:
        bucket = by_amplitude[str(amplitude)]
        summary = {
            "paired_height_slope_delta_mm_per_m": robust_stats(bucket["paired_slope"]),
            "absolute_raw_height_slope_mm_per_m": robust_stats(bucket["raw_slope"]),
            "paired_inferred_pitch_span_delta_deg": robust_stats(bucket["paired_pitch"]),
            "image_proxy_absolute_error_deg": robust_stats(bucket["proxy_error"]),
            "pnp_yaw_absolute_error_deg": robust_stats(bucket["yaw_error"]),
            "sample_count": len(bucket["paired_slope"]),
        }
        summary["paired_height_slope_p95"] = float(np.percentile(bucket["paired_slope"], 95))
        summary["paired_pitch_span_p95"] = float(np.percentile(bucket["paired_pitch"], 95))
        summaries[str(amplitude)] = summary
        if (
            summary["paired_height_slope_p95"] <= 10.0
            and summary["paired_inferred_pitch_span_delta_deg"]["median"] <= 0.25
        ):
            qualified.append(amplitude)

    # Exact image-shape controls, isolated from corner noise.
    lateral_control = [
        {
            "lateral_m": lateral,
            "decision": (score := synthetic_single_score(0.0, lateral, 0.8))["decision"],
            "signed_proxy_deg": score["aggregate"]["signed_straightness_proxy_deg"]["median"],
        }
        for lateral in (-0.15, 0.0, 0.15)
    ]
    monotonic_control = []
    for yaw in (-5.0, -3.0, -2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 3.0, 5.0):
        score = synthetic_single_score(yaw, 0.0, 0.8)
        monotonic_control.append(
            {"true_yaw_deg": yaw, "signed_proxy_deg": score["aggregate"]["signed_straightness_proxy_deg"]["median"]}
        )
    zero_noise_isolation = {}
    for amplitude in amplitudes:
        cases = {}
        for pattern_name in ("constant", "current_like", "alternating"):
            yaws = patterns[pattern_name](amplitude)
            cases[pattern_name] = _simulate_series(
                yaws, [0.0, 0.0, 0.0], 0.0, 7, visibility, K, D, objects
            )
        zero_noise_isolation[str(amplitude)] = cases
    selected_limit = max(qualified) if qualified else 0.0
    measurement_guard = 0.25
    return {
        "schema_version": "stage3a_straightness_synthetic_study.v1",
        "deterministic_seed_policy": True,
        "scenario_count": scenario_count,
        "corner_noise_px": noises,
        "distance_m": [0.6, 0.8, 1.0],
        "yaw_grid_deg": [-5, -3, -2, -1, -0.5, 0, 0.5, 1, 2, 3, 5],
        "lateral_m": [-0.15, -0.08, 0.0, 0.08, 0.15],
        "camera_height_mm": CAMERA_HEIGHT_MM,
        "board_bottom_mm": BOARD_BOTTOM_MM,
        "board_size_mm": [175.0, 125.0],
        "gravity_orientation_fixed": True,
        "current_pipeline": "per-frame SOLVEPNP_ITERATIVE, pose/full-rotation chordal mean, aggregate-R fixed-height validation",
        "paired_design": "same visibility and identical corner noise with yaw pattern versus zero-yaw baseline",
        "by_absolute_yaw_amplitude": summaries,
        "worst_paired_case": worst,
        "pure_lateral_translation_control": lateral_control,
        "signed_monotonic_control": monotonic_control,
        "zero_noise_yaw_isolation": zero_noise_isolation,
        "threshold_selection": {
            "small_height_benefit_limit_mm_per_m": 10.0,
            "small_median_pitch_benefit_limit_deg": 0.25,
            "largest_qualified_tested_yaw_deg": selected_limit,
            "current_intrinsics_and_rounding_guard_deg": measurement_guard,
            "implemented_absolute_proxy_limit_deg": STRAIGHT_ENOUGH_ABS_PROXY_DEG,
            "matches_study": abs(selected_limit + measurement_guard - STRAIGHT_ENOUGH_ABS_PROXY_DEG) < 1e-12,
            "straight_enough_region": f"absolute image proxy <= {STRAIGHT_ENOUGH_ABS_PROXY_DEG:.2f} internal degrees",
            "expected_user_difficulty": "LOW",
        },
        "field_requirement": {
            "user_needs_to_measure_angle": False,
            "user_needs_to_measure_left_right_distance": False,
        },
        "capture_or_registration_performed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only image-space vehicle straightness scorer")
    sub = parser.add_subparsers(dest="command", required=True)
    item = sub.add_parser("score-scan", help="score an existing scan directory read-only")
    item.add_argument("--scan", type=Path, required=True)
    item.add_argument("--json", action="store_true")
    item = sub.add_parser("score-frame", help="score one exported image or RGB565 frame read-only")
    item.add_argument("--image", type=Path, required=True)
    item.add_argument("--json", action="store_true")
    item = sub.add_parser("score-existing", help="score frozen W01/W02/W03 read-only")
    item.add_argument("--json", action="store_true")
    item.add_argument("--output", type=Path)
    item = sub.add_parser("study", help="run deterministic synthetic attribution study")
    item.add_argument("--json", action="store_true")
    item.add_argument("--output", type=Path)
    item.add_argument("--repetitions", type=int, default=12)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    K, D, _ = nominal_camera_model()
    if args.command == "score-frame":
        board, detector = board_and_detector()
        frame = detect_frame(args.image.resolve(), board, detector)
        result = aggregate_score([] if frame is None else [frame], K, D, 1)
        print(json.dumps(result, indent=2) if args.json else "", end="" if args.json else "")
        if not args.json:
            print_human(result)
        return 0
    if args.command == "score-scan":
        frames, total = detect_scan(args.scan.resolve())
        result = aggregate_score(frames, K, D, total)
        print(json.dumps(result, indent=2) if args.json else "", end="" if args.json else "")
        if not args.json:
            print_human(result)
        return 0
    if args.command == "score-existing":
        result = score_existing(include_sensitivity=True)
        if args.output:
            write_json(args.output.resolve(), result)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for index, pose in enumerate(POSE_IDS):
                if index:
                    print()
                print_human(result["scores"][pose], pose)
        return 0
    result = synthetic_study(args.repetitions)
    if args.output:
        write_json(args.output.resolve(), result)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        threshold = result["threshold_selection"]
        worst = result["worst_paired_case"]
        print("decision = STUDY_COMPLETE")
        print(f"straight_enough_region = {threshold['straight_enough_region']}")
        print(f"expected_user_difficulty = {threshold['expected_user_difficulty']}")
        print(f"worst_paired_height_slope_delta = {worst['paired_height_slope_delta_mm_per_m']:.3f} mm/m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
