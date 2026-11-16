#!/usr/bin/env python3
"""Stage2C independent Camera<->ToF validation.

This CLI can register new scans, estimate a ChArUco plane pose, and evaluate
the immutable Stage2A/Stage2B authorities. It intentionally exposes no
parameter-estimation, gauge-selection, candidate-update, or promotion path.
All writes are confined to this Stage2C workspace.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np

WORKSPACE = Path(__file__).resolve().parent
SCAN_ROOT = Path(r"C:\ec\_deltar_scan_service_data\validated")
STAGE2A = Path(r"C:\ec\_calibration_workspaces\stage2a_camera_intrinsic_20260817_01\candidate\camera_intrinsics.stage2a_candidate.v1.json")
STAGE2B = Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\candidate\camera_tof.stage2b_all16_candidate.v2.json")
DELTAR_FR = Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\candidate\deltar_fr.stage2b_all16_candidate.v2.json")
STAGE2B_OBSERVATIONS = Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\observations\observations.stage2b.json")
STAGE2B_MANIFEST = Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\workspace_manifest.json")
EXPECTED_STAGE2A_SHA256 = "99e5fe45e56b9f9a5a05f26179a9ee124610c3d4524b4e257b88c7b149979f6b"
WIDTH, HEIGHT, PAYLOAD_BYTES = 640, 360, 460800
STRICT_STATUSES = {5, 6, 9}
POSE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
MANIFEST_PATH = WORKSPACE / "workspace_manifest.json"
AUTHORITY_PATH = WORKSPACE / "frozen_authority_manifest.json"
OBS_PATH = WORKSPACE / "observations" / "observations.stage2c.json"
VALIDATION_PATH = WORKSPACE / "validation" / "validation.stage2c.json"
COVERAGE_PATH = WORKSPACE / "coverage" / "coverage.stage2c.json"
REFERENCE = {"median_mm": 3.120055107373446, "p95_mm": 9.042760056480166,
             "rms_mm": 4.682742765466878, "max_mm": 19.971951045488254}


class Stage2CError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise Stage2CError(f"cannot read strict JSON {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(raw, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def authority_entries() -> dict[str, dict[str, str]]:
    return {
        "stage2a_candidate": {"path": str(STAGE2A), "sha256": sha256_file(STAGE2A)},
        "stage2b_candidate": {"path": str(STAGE2B), "sha256": sha256_file(STAGE2B)},
        "deltar_fr_candidate": {"path": str(DELTAR_FR), "sha256": sha256_file(DELTAR_FR)},
    }


def validate_candidate_contracts() -> None:
    a, b, fr = load_json(STAGE2A), load_json(STAGE2B), load_json(DELTAR_FR)
    if a.get("schema_version") != "stage2a_camera_intrinsics_candidate.v1":
        raise Stage2CError("unsupported Stage2A candidate schema")
    if sha256_file(STAGE2A) != EXPECTED_STAGE2A_SHA256:
        raise Stage2CError("Stage2A SHA-256 differs from the frozen task authority")
    expected_b = {
        "schema_version": "camera_tof.stage2b_all16_candidate.v2",
        "status": "CANDIDATE_NOT_PROMOTED",
        "selected_solver_variant": "R3_core_square_centered",
    }
    for key, value in expected_b.items():
        if b.get(key) != value:
            raise Stage2CError(f"Stage2B sanity check failed: {key}")
    gauge = b.get("gauge", {})
    required_gauge = {"physical_D4_orientation": "UNRESOLVED_GAUGE", "canonical_gauge": "identity",
                      "mapping_authority": "raw_zone_index_to_camera_frame_ray"}
    if any(gauge.get(key) != value for key, value in required_gauge.items()):
        raise Stage2CError("Stage2B frozen gauge/mapping authority mismatch")
    if b.get("range_semantics", {}).get("selected") != "axial_z":
        raise Stage2CError("Stage2B frozen range semantics is not axial_z")
    mapping = b.get("raw_zone_camera_mapping")
    if not isinstance(mapping, list) or len(mapping) != 64 or [z.get("raw_index") for z in mapping] != list(range(64)):
        raise Stage2CError("Stage2B raw-zone mapping is malformed")
    transform = b.get("T_camera_from_tof", {})
    if transform.get("direction") != "T_camera_from_tof" or not str(transform.get("convention", "")).startswith("column_vector"):
        raise Stage2CError("Stage2B transform convention mismatch")
    if fr.get("schema_version") != "deltar_fr.stage2b_all16_candidate.v2" or fr.get("runtime_replaced") is not False:
        raise Stage2CError("unsupported or promoted DELTAR FR authority")


def ensure_layout() -> None:
    for name in ("imports", "manifests", "observations", "coverage", "validation", "plots", "logs", "evidence", "synthetic"):
        (WORKSPACE / name).mkdir(parents=True, exist_ok=True)
    for path in (STAGE2A, STAGE2B, DELTAR_FR):
        if not path.is_file():
            raise Stage2CError(f"frozen authority missing: {path}")
    validate_candidate_contracts()
    if not AUTHORITY_PATH.exists():
        write_json(AUTHORITY_PATH, {"schema_version": "stage2c_frozen_authorities.v1", "bound_at": now(),
                                   "authorities": authority_entries(), "rebind_supported": False})
    bound = load_json(AUTHORITY_PATH)
    current = authority_entries()
    if bound.get("authorities") != current:
        raise Stage2CError("frozen authority path/hash changed; create a new Stage2C workspace (no silent rebind)")
    if not MANIFEST_PATH.exists():
        write_json(MANIFEST_PATH, {"schema_version": "stage2c_workspace_manifest.v1", "created_at": now(),
                                   "workspace": str(WORKSPACE), "scan_root": str(SCAN_ROOT),
                                   "frozen_authority_manifest": str(AUTHORITY_PATH), "poses": {}})


def verify_authorities() -> dict[str, Any]:
    ensure_layout()
    bound = load_json(AUTHORITY_PATH)["authorities"]
    current = authority_entries()
    if bound != current:
        raise Stage2CError("frozen authorities changed")
    return {"frozen_authorities_unchanged": True, "authorities": current}


def manifest() -> dict[str, Any]:
    verify_authorities()
    value = load_json(MANIFEST_PATH)
    if value.get("schema_version") != "stage2c_workspace_manifest.v1" or not isinstance(value.get("poses"), dict):
        raise Stage2CError("malformed Stage2C workspace manifest")
    return value


def save_manifest(value: dict[str, Any]) -> None:
    value["updated_at"] = now()
    write_json(MANIFEST_PATH, value)


def valid_pose_id(value: str) -> str:
    if not POSE_RE.fullmatch(value):
        raise Stage2CError("invalid pose_id; expected [A-Za-z0-9][A-Za-z0-9_-]*")
    return value


def resolve_scan(scan_id: str | None, latest: bool) -> Path:
    if bool(scan_id) == bool(latest):
        raise Stage2CError("choose exactly one of --scan-id or --latest")
    if latest:
        scans = sorted(path for path in SCAN_ROOT.glob("scan_*") if path.is_dir() and (path / "scan.json").is_file())
        if not scans:
            raise Stage2CError(f"no formal scans under {SCAN_ROOT}")
        return scans[-1]
    if scan_id is None or not re.fullmatch(r"scan_[A-Za-z0-9_-]+", scan_id):
        raise Stage2CError("invalid scan_id")
    result = SCAN_ROOT / scan_id
    if not result.is_dir():
        raise Stage2CError(f"formal scan does not exist: {result}")
    return result


def validate_scan(path: Path) -> list[dict[str, Any]]:
    scan = load_json(path / "scan.json")
    errors: list[str] = []
    if scan.get("schema_version") != "deltar_scan_input.v1": errors.append("scan schema")
    if scan.get("scan_id") != path.name: errors.append("scan_id")
    if scan.get("frame_count") != 11: errors.append(f"frame_count={scan.get('frame_count')}, expected 11")
    rows = []
    for sequence in range(11):
        frame_id = f"frame_{sequence:06d}"
        folder = path / "frames" / frame_id
        fp, ip, tp = folder / "frame.json", folder / "image.rgb565", folder / "tof.json"
        if not all(item.is_file() for item in (fp, ip, tp)):
            errors.append(f"{frame_id}: required files"); continue
        frame, tof = load_json(fp), load_json(tp)
        image = frame.get("image", {})
        expected_image = {"filename": "image.rgb565", "width": 640, "height": 360, "pixel_format": "RGB565_LE",
                          "stride_bytes": 1280, "payload_bytes": 460800}
        if frame.get("schema_version") != "deltar_scan_frame.v1" or frame.get("frame_id") != frame_id or frame.get("sequence") != sequence:
            errors.append(f"{frame_id}: frame header")
        if image != expected_image or ip.stat().st_size != PAYLOAD_BYTES: errors.append(f"{frame_id}: raster contract")
        camera_ts, tof_ts, delta = frame.get("camera_timestamp_us"), frame.get("tof_timestamp_us"), frame.get("pair_delta_us")
        if not all(type(value) is int for value in (camera_ts, tof_ts, delta)) or delta != abs(camera_ts - tof_ts):
            errors.append(f"{frame_id}: timestamps")
        if tof.get("schema_version") != "rehab_tof_raw.v1" or tof.get("sequence") != sequence or tof.get("timestamp_us") != tof_ts:
            errors.append(f"{frame_id}: ToF header")
        for key in ("distance_mm", "range_sigma_mm", "target_status", "nb_target_detected"):
            if not isinstance(tof.get(key), list) or len(tof[key]) != 64: errors.append(f"{frame_id}: ToF {key}")
        rows.append({"frame_id": frame_id, "folder": folder, "frame": frame, "tof": tof})
    if errors:
        raise Stage2CError("invalid formal 11-frame scan: " + "; ".join(errors))
    return rows


def source_files(scan: Path) -> list[Path]:
    files = [scan / "scan.json"]
    for sequence in range(11):
        folder = scan / "frames" / f"frame_{sequence:06d}"
        files.extend(folder / name for name in ("frame.json", "image.rgb565", "tof.json"))
    return files


def link_or_copy(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
        return "hardlink"
    except OSError:
        shutil.copy2(source, destination)
        return "copy"


def cmd_register(args: argparse.Namespace) -> int:
    value, pose = manifest(), valid_pose_id(args.pose)
    if pose in value["poses"]:
        raise Stage2CError(f"pose_id already registered: {pose}; no silent replacement is allowed")
    source = resolve_scan(args.scan_id, args.latest)
    validate_scan(source)
    before = {str(path.relative_to(source)): sha256_file(path) for path in source_files(source)}
    if args.dry_run:
        print(f"DRY-RUN valid pose={pose} scan_id={source.name} frames=11 files={len(before)} source_unchanged=true")
        return 0
    destination = WORKSPACE / "imports" / pose / source.name
    if destination.exists():
        raise Stage2CError(f"import destination exists: {destination}")
    modes = {link_or_copy(path, destination / path.relative_to(source)) for path in source_files(source)}
    after = {str(path.relative_to(source)): sha256_file(path) for path in source_files(source)}
    if before != after:
        raise Stage2CError("formal source hash changed during registration")
    imported = {relative: sha256_file(destination / relative) for relative in before}
    if imported != before:
        raise Stage2CError("Stage2C import hash mismatch")
    entry = {"scan_id": source.name, "source_path": str(source.resolve()), "import_path": str(destination.resolve()),
             "registered_at": now(), "frame_count": 11, "link_mode": sorted(modes), "source_hashes": before,
             "analysis_state": "NOT_INSPECTED"}
    value["poses"][pose] = entry
    save_manifest(value)
    write_json(WORKSPACE / "manifests" / f"{pose}.json", {"pose_id": pose, **entry})
    print(f"registered pose={pose} scan_id={source.name} frames=11 mode={','.join(sorted(modes))} source_unchanged=true")
    return 0


def rgb565(path: Path) -> np.ndarray:
    raw = np.fromfile(path, dtype="<u2")
    if raw.size != WIDTH * HEIGHT:
        raise Stage2CError(f"RGB565 payload mismatch: {path}")
    red = ((raw >> 11) & 31).astype(np.uint8); green = ((raw >> 5) & 63).astype(np.uint8); blue = (raw & 31).astype(np.uint8)
    red = (red << 3) | (red >> 2); green = (green << 2) | (green >> 4); blue = (blue << 3) | (blue >> 2)
    return np.stack((red, green, blue), axis=-1).reshape(HEIGHT, WIDTH, 3)


def camera_parameters() -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    candidate = load_json(STAGE2A); intr = candidate["camera"]["intrinsics"]
    k = np.array([[intr["fx"], 0, intr["cx"]], [0, intr["fy"], intr["cy"]], [0, 0, 1]], dtype=np.float64)
    distortion = intr["distortion"]
    d = np.array([distortion[key] for key in ("k1", "k2", "p1", "p2", "k3")], dtype=np.float64)
    return k, d, candidate["board"]


def charuco_board() -> cv2.aruco.CharucoBoard:
    _, _, spec = camera_parameters()
    dictionary_name = spec["dictionary"]
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary_name))
    return cv2.aruco.CharucoBoard((int(spec["squares_x"]), int(spec["squares_y"])),
                                  float(spec["square_length_mm"]), float(spec["marker_length_mm"]), dictionary)


def detect_plane(image_rgb: np.ndarray, label: str) -> dict[str, Any]:
    board = charuco_board()
    parameters = cv2.aruco.DetectorParameters(); parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.CharucoDetector(board, cv2.aruco.CharucoParameters(), parameters)
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    corners, ids, marker_corners, marker_ids = detector.detectBoard(gray)
    corner_count = 0 if ids is None else int(len(ids)); marker_count = 0 if marker_ids is None else int(len(marker_ids))
    result: dict[str, Any] = {"frame_label": label, "detected": False, "corner_count": corner_count,
                              "marker_count": marker_count,
                              "charuco_corners_px": [] if corners is None else corners.reshape(-1, 2).tolist(),
                              "charuco_ids": [] if ids is None else ids.reshape(-1).astype(int).tolist(),
                              "marker_corners_px": [] if marker_corners is None else [x.reshape(-1, 2).tolist() for x in marker_corners],
                              "marker_ids": [] if marker_ids is None else marker_ids.reshape(-1).astype(int).tolist()}
    if ids is None or corner_count < 6:
        result["reason"] = "fewer_than_6_charuco_corners"; return result
    obj, img = board.matchImagePoints(corners, ids); k, d, _ = camera_parameters()
    ok, rvec, tvec = cv2.solvePnP(obj, img, k, d, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        result["reason"] = "solvePnP_failed"; return result
    projected, _ = cv2.projectPoints(obj, rvec, tvec, k, d)
    errors = np.linalg.norm(projected.reshape(-1, 2) - img.reshape(-1, 2), axis=1)
    rotation, _ = cv2.Rodrigues(rvec); normal = rotation[:, 2]; point = tvec.reshape(3)
    if float(normal @ point) > 0: normal = -normal
    offset = -float(normal @ point)
    hull = cv2.convexHull(img.astype(np.float32)); center = np.mean(img.reshape(-1, 2), axis=0)
    yaw = math.degrees(math.atan2(float(normal[0]), abs(float(normal[2]))))
    pitch = math.degrees(math.atan2(float(normal[1]), abs(float(normal[2]))))
    tilt = math.degrees(math.acos(np.clip(abs(float(normal[2])), -1.0, 1.0)))
    result.update({"detected": True, "pnp_rms_px": float(np.sqrt(np.mean(errors ** 2))),
                   "pnp_p95_px": float(np.percentile(errors, 95)),
                   "board_coverage_fraction": float(cv2.contourArea(hull) / (WIDTH * HEIGHT)),
                   "board_center_px": center.tolist(), "rvec": rvec.reshape(3).tolist(), "tvec_mm": point.tolist(),
                   "plane_normal_camera": normal.tolist(), "plane_offset_camera_mm": offset,
                   "plane_distance_camera_mm": abs(offset), "yaw_like_deg": yaw, "pitch_like_deg": pitch,
                   "combined_tilt_deg": tilt})
    return result


def tof_rows(tof: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for index in range(64):
        distance, status, count = float(tof["distance_mm"][index]), int(tof["target_status"][index]), int(tof["nb_target_detected"][index])
        broad = count != 0 and status != 255 and distance > 0
        strict = count != 0 and status in STRICT_STATUSES and distance > 0
        result.append({"raw_index": index, "raw_row": index // 8, "raw_col": index % 8, "distance_mm": distance,
                       "range_sigma_mm": float(tof["range_sigma_mm"][index]), "target_status": status,
                       "target_count": count, "broad_valid": broad, "strict_valid": strict})
    return result


def frozen_geometry() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    candidate = load_json(STAGE2B); mapping = candidate["raw_zone_camera_mapping"]
    tof = np.asarray([zone["tof_frame_center_ray"] for zone in mapping], dtype=float)
    camera = np.asarray([zone["camera_frame_center_ray"] for zone in mapping], dtype=float)
    transform = candidate["T_camera_from_tof"]
    return tof, camera, np.asarray(transform["R"], dtype=float), np.asarray(transform["t_mm"], dtype=float)


def geometry_residuals(normal: np.ndarray, offset: float, distance: np.ndarray,
                       tof_rays: np.ndarray, rotation: np.ndarray, translation: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rotated = tof_rays @ rotation.T
    denominator = rotated @ normal
    if np.any(np.abs(denominator) < 1e-9) or np.any(np.abs(tof_rays[:, 2]) < 1e-9):
        raise Stage2CError("frozen ray is parallel to plane or has zero axial component")
    expected_z = -(float(normal @ translation) + offset) * tof_rays[:, 2] / denominator
    points_tof = tof_rays * (distance / tof_rays[:, 2])[:, None]
    points_camera = points_tof @ rotation.T + translation
    point_plane = points_camera @ normal + offset
    return distance - expected_z, point_plane, expected_z


def project_camera(points_camera: np.ndarray) -> np.ndarray:
    k, d, _ = camera_parameters()
    pixels, _ = cv2.projectPoints(points_camera.astype(np.float64), np.zeros(3), np.zeros(3), k, d)
    return pixels.reshape(-1, 2)


def save_preview(image: np.ndarray, plane: dict[str, Any], zones: list[dict[str, Any]], path: Path) -> None:
    preview = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    corners = np.asarray(plane.get("charuco_corners_px", []), dtype=float)
    for point in corners:
        cv2.circle(preview, tuple(np.rint(point).astype(int)), 3, (0, 255, 0), -1)
    for zone in zones:
        pixel = zone.get("projected_camera_center_px")
        if pixel is None: continue
        x, y = (int(round(pixel[0])), int(round(pixel[1])))
        color = (0, 200, 0) if zone.get("likely_reference_plane") else (0, 0, 255)
        cv2.circle(preview, (x, y), 5, color, 1)
        if zone.get("point_to_plane_residual_mm") is not None:
            cv2.putText(preview, f"{zone['raw_index']}:{zone['point_to_plane_residual_mm']:+.0f}", (x + 3, y - 3),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.27, color, 1, cv2.LINE_AA)
    path.parent.mkdir(parents=True, exist_ok=True); cv2.imwrite(str(path), preview)


def inspect_registered() -> dict[str, Any]:
    value = manifest(); tof_rays_frozen, camera_rays, rotation, translation = frozen_geometry(); poses = []
    all_abs_broad: list[float] = []
    for pose_id, item in value["poses"].items():
        scan = Path(item["import_path"]); frames = []; normals = []; offsets = []
        for row in validate_scan(scan):
            image = rgb565(row["folder"] / "image.rgb565"); plane = detect_plane(image, f"{pose_id}/{row['frame_id']}")
            zones = tof_rows(row["tof"])
            if plane["detected"]:
                normal = np.asarray(plane["plane_normal_camera"], dtype=float); offset = float(plane["plane_offset_camera_mm"])
                normals.append(normal); offsets.append(offset)
                distance = np.asarray([zone["distance_mm"] for zone in zones], dtype=float)
                axial, point_plane, expected = geometry_residuals(normal, offset, distance, tof_rays_frozen, rotation, translation)
                expected_points = tof_rays_frozen * (expected / tof_rays_frozen[:, 2])[:, None]
                expected_camera = expected_points @ rotation.T + translation; pixels = project_camera(expected_camera)
                for index, zone in enumerate(zones):
                    zone.update({"axial_depth_residual_mm": float(axial[index]),
                                 "point_to_plane_residual_mm": float(point_plane[index]),
                                 "expected_axial_z_mm": float(expected[index]),
                                 "projected_camera_center_px": pixels[index].tolist(),
                                 "camera_frame_center_ray_authority": camera_rays[index].tolist(),
                                 "likely_reference_plane": None, "likely_outside_or_background": None})
                    if zone["broad_valid"]: all_abs_broad.append(abs(float(point_plane[index])))
            frames.append({"frame_id": row["frame_id"], "camera_timestamp_us": row["frame"]["camera_timestamp_us"],
                           "tof_timestamp_us": row["frame"]["tof_timestamp_us"], "pair_delta_us": row["frame"]["pair_delta_us"],
                           "camera_plane": plane, "tof_zones": zones})
        stability = {"usable_plane_frames": len(normals), "normal_max_deviation_deg": None, "offset_range_mm": None, "stable": False}
        if normals:
            reference = np.mean(normals, axis=0); reference /= np.linalg.norm(reference)
            angles = np.degrees(np.arccos(np.clip(np.abs(np.asarray(normals) @ reference), -1, 1)))
            stability.update({"normal_max_deviation_deg": float(np.max(angles)),
                              "normal_rms_deviation_deg": float(np.sqrt(np.mean(angles ** 2))),
                              "offset_range_mm": float(np.ptp(offsets)), "offset_std_mm": float(np.std(offsets)),
                              "stable": bool(np.max(angles) <= 1.0 and np.ptp(offsets) <= 15.0),
                              "thresholds_diagnostic_only": {"normal_max_deg": 1.0, "offset_range_mm": 15.0}})
        poses.append({"pose_id": pose_id, "scan_id": item["scan_id"], "frames": frames, "within_scan_plane_stability": stability})
        item["analysis_state"] = "INSPECTED" if normals else "NO_CAMERA_PLANE"
        item["usable_plane_frames"] = len(normals); item["within_scan_plane_stable"] = stability["stable"]
    absolute = np.asarray(all_abs_broad, dtype=float)
    threshold = 10.0
    if len(absolute):
        median = float(np.median(absolute)); mad = float(np.median(np.abs(absolute - median)))
        threshold = max(10.0, median + 4.5 * 1.4826 * mad)
    for pose in poses:
        for frame in pose["frames"]:
            for zone in frame["tof_zones"]:
                residual = zone.get("point_to_plane_residual_mm")
                if residual is not None and zone["broad_valid"]:
                    zone["likely_reference_plane"] = bool(abs(residual) <= threshold)
                    zone["likely_outside_or_background"] = bool(abs(residual) > threshold)
            if frame["camera_plane"]["detected"]:
                source = Path(value["poses"][pose["pose_id"]]["import_path"]) / "frames" / frame["frame_id"] / "image.rgb565"
                save_preview(rgb565(source), frame["camera_plane"], frame["tof_zones"],
                             WORKSPACE / "plots" / pose["pose_id"] / f"{frame['frame_id']}_overlay.png")
    output = {"schema_version": "stage2c_observations.v1", "generated_at": now(), "pose_count": len(poses), "poses": poses,
              "plane_consistency_rule": {"residual": "absolute orthogonal point-to-plane",
                                         "threshold_mm": threshold,
                                         "method": "max(10 mm, median(abs broad residual) + 4.5 * 1.4826 * MAD)",
                                         "classification_only": True, "untrimmed_layers_always_reported": True,
                                         "zone_frame_robust_not_whole_scan_rejection": True},
              "frozen_authorities": verify_authorities()}
    write_json(OBS_PATH, output); save_manifest(value)
    return output


def metric(values: Iterable[float]) -> dict[str, Any]:
    array = np.asarray(list(values), dtype=float)
    if not len(array):
        return {key: None for key in ("count", "median_abs_mm", "p90_abs_mm", "p95_abs_mm", "p99_abs_mm", "rms_mm", "max_abs_mm", "signed_mean_mm", "absolute_mean_mm")}
    absolute = np.abs(array)
    return {"count": int(len(array)), "median_abs_mm": float(np.median(absolute)), "p90_abs_mm": float(np.percentile(absolute, 90)),
            "p95_abs_mm": float(np.percentile(absolute, 95)), "p99_abs_mm": float(np.percentile(absolute, 99)),
            "rms_mm": float(np.sqrt(np.mean(array ** 2))), "max_abs_mm": float(np.max(absolute)),
            "signed_mean_mm": float(np.mean(array)), "absolute_mean_mm": float(np.mean(absolute))}


def detected_frames(observations: dict[str, Any]) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    for pose in observations["poses"]:
        for frame in pose["frames"]:
            if frame["camera_plane"].get("detected"):
                yield pose, frame


def bin_distance(value: float) -> str:
    return "near" if value < 550 else ("mid" if value < 850 else "far")


def bin_angle(value: float) -> str:
    return "low" if value < 10 else ("medium" if value < 25 else "high")


def spatial_region(center: list[float]) -> str:
    x, y = center
    if abs(x - WIDTH / 2) <= WIDTH * .15 and abs(y - HEIGHT / 2) <= HEIGHT * .15: return "center"
    if abs(x - WIDTH / 2) >= abs(y - HEIGHT / 2) * WIDTH / HEIGHT: return "left" if x < WIDTH / 2 else "right"
    return "top" if y < HEIGHT / 2 else "bottom"


def quadrant(center: list[float]) -> str:
    return ("top" if center[1] < HEIGHT / 2 else "bottom") + "_" + ("left" if center[0] < WIDTH / 2 else "right")


def observation_records(observations: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for pose, frame in detected_frames(observations):
        plane = frame["camera_plane"]; distance = float(plane["plane_distance_camera_mm"]); tilt = float(plane["combined_tilt_deg"])
        for zone in frame["tof_zones"]:
            if zone.get("point_to_plane_residual_mm") is None: continue
            result.append({**zone, "pose_id": pose["pose_id"], "frame_id": frame["frame_id"],
                           "pair_delta_us": frame["pair_delta_us"], "reference_distance_mm": distance,
                           "yaw_like_deg": plane["yaw_like_deg"], "pitch_like_deg": plane["pitch_like_deg"], "tilt_deg": tilt,
                           "distance_bin": bin_distance(distance), "angle_bin": bin_angle(tilt),
                           "spatial_region": spatial_region(plane["board_center_px"]), "image_quadrant": quadrant(plane["board_center_px"])})
    return result


def grouped_metrics(records: list[dict[str, Any]], key: str, residual_key: str, *, layer: str = "core") -> dict[str, Any]:
    groups: dict[str, list[float]] = {}
    for row in records:
        include = row.get("likely_reference_plane") is True if layer == "core" else (row["strict_valid"] if layer == "strict" else row["broad_valid"])
        if include: groups.setdefault(str(row[key]), []).append(float(row[residual_key]))
    return {name: metric(values) for name, values in sorted(groups.items())}


def pose_summary(observations: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for pose in observations["poses"]:
        frames = [frame for frame in pose["frames"] if frame["camera_plane"].get("detected")]
        planes = [frame["camera_plane"] for frame in frames]
        selected = [row for row in records if row["pose_id"] == pose["pose_id"] and row.get("likely_reference_plane") is True]
        values = [row["point_to_plane_residual_mm"] for row in selected]
        result.append({"pose_id": pose["pose_id"], "scan_id": pose["scan_id"], "usable_frames": len(frames),
                       "distance_mm": float(np.median([p["plane_distance_camera_mm"] for p in planes])) if planes else None,
                       "yaw_like_deg": float(np.median([p["yaw_like_deg"] for p in planes])) if planes else None,
                       "pitch_like_deg": float(np.median([p["pitch_like_deg"] for p in planes])) if planes else None,
                       "tilt_deg": float(np.median([p["combined_tilt_deg"] for p in planes])) if planes else None,
                       "board_center_px": np.median([p["board_center_px"] for p in planes], axis=0).tolist() if planes else None,
                       "board_coverage_fraction": float(np.median([p["board_coverage_fraction"] for p in planes])) if planes else None,
                       "core": metric(values), "stability": pose["within_scan_plane_stability"]})
    return result


def frame_summary(observations: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for pose, frame in detected_frames(observations):
        selected = [row for row in records if row["pose_id"] == pose["pose_id"] and row["frame_id"] == frame["frame_id"]
                    and row.get("likely_reference_plane") is True]
        result.append({"pose_id": pose["pose_id"], "frame_id": frame["frame_id"], "pair_delta_us": frame["pair_delta_us"],
                       "pnp_rms_px": frame["camera_plane"]["pnp_rms_px"],
                       "core_point_to_plane": metric(row["point_to_plane_residual_mm"] for row in selected),
                       "core_axial_depth": metric(row["axial_depth_residual_mm"] for row in selected)})
    return result


def training_pose_cloud() -> list[dict[str, Any]]:
    if not STAGE2B_OBSERVATIONS.is_file(): return []
    observations = load_json(STAGE2B_OBSERVATIONS); result = []
    for pose in observations.get("poses", []):
        planes = [frame["camera_plane"] for frame in pose["frames"] if frame["camera_plane"].get("detected")]
        if not planes: continue
        normal = np.median(np.asarray([p["plane_normal_camera"] for p in planes]), axis=0)
        yaw = math.degrees(math.atan2(float(normal[0]), abs(float(normal[2])))); pitch = math.degrees(math.atan2(float(normal[1]), abs(float(normal[2]))))
        result.append({"pose_id": pose["pose_id"], "distance_mm": float(np.median([p["plane_distance_camera_mm"] for p in planes])),
                       "yaw_like_deg": yaw, "pitch_like_deg": pitch,
                       "center_x_px": float(np.median([p["board_center_px"][0] for p in planes])),
                       "center_y_px": float(np.median([p["board_center_px"][1] for p in planes])),
                       "coverage": float(np.median([p["board_coverage_fraction"] for p in planes]))})
    return result


def novelty(validation_poses: list[dict[str, Any]]) -> dict[str, Any]:
    training = training_pose_cloud(); features = ("distance_mm", "yaw_like_deg", "pitch_like_deg", "center_x_px", "center_y_px", "coverage")
    if not training: return {"feature_order": list(features), "training_pose_count": 0, "poses": []}
    train_array = np.asarray([[row[key] for key in features] for row in training], dtype=float)
    scale = np.std(train_array, axis=0); fallback = np.asarray([150, 10, 10, 100, 70, .03], dtype=float); scale = np.where(scale > 1e-9, scale, fallback)
    rows = []
    for pose in validation_poses:
        if pose["distance_mm"] is None: continue
        vector = np.asarray([pose["distance_mm"], pose["yaw_like_deg"], pose["pitch_like_deg"], pose["board_center_px"][0], pose["board_center_px"][1], pose["board_coverage_fraction"]])
        distances = np.sqrt(np.mean(((train_array - vector) / scale) ** 2, axis=1)); index = int(np.argmin(distances))
        rows.append({"pose_id": pose["pose_id"], "nearest_stage2b_pose_id": training[index]["pose_id"],
                     "normalized_feature_distance": float(distances[index]),
                     "diagnostic_label": "LIKELY_REPEAT" if distances[index] < .35 else "NOVEL_RELATIVE_TO_STAGE2B"})
    return {"feature_order": list(features), "normalization": "Stage2B per-feature standard deviation with documented fallback scales",
            "repeat_diagnostic_threshold": .35, "hard_gate": False, "training_pose_count": len(training), "poses": rows}


def coverage_document(observations: dict[str, Any]) -> dict[str, Any]:
    records = observation_records(observations); poses = pose_summary(observations, records)
    usable = [pose for pose in poses if pose["distance_mm"] is not None]
    distances = [pose["distance_mm"] for pose in usable]; tilts = [pose["tilt_deg"] for pose in usable]
    centers = [pose["board_center_px"] for pose in usable]
    zones = []
    for index in range(64):
        rows = [row for row in records if row["raw_index"] == index]
        core = [row for row in rows if row.get("likely_reference_plane") is True]
        zone_metric = metric([row["point_to_plane_residual_mm"] for row in core])
        zones.append({"raw_index": index, "raw_row": index // 8, "raw_col": index % 8,
                      "core_count": len(core), "strict_count": sum(row["strict_valid"] for row in rows),
                      "broad_count": sum(row["broad_valid"] for row in rows),
                      "distance_spread_mm": float(np.ptp([row["reference_distance_mm"] for row in core])) if core else None,
                      "angle_spread_deg": float(np.ptp([row["tilt_deg"] for row in core])) if core else None,
                      **zone_metric})
    document = {"schema_version": "stage2c_coverage.v1", "generated_at": now(), "registered_validation_scans": len(observations["poses"]),
                "usable_validation_poses": len(usable),
                "distance": {"pose_bins": {name: sum(bin_distance(value) == name for value in distances) for name in ("near", "mid", "far")},
                             "min_mm": min(distances) if distances else None, "median_mm": float(np.median(distances)) if distances else None,
                             "max_mm": max(distances) if distances else None},
                "angle": {"pose_bins": {name: sum(bin_angle(value) == name for value in tilts) for name in ("low", "medium", "high")},
                          "min_deg": min(tilts) if tilts else None, "median_deg": float(np.median(tilts)) if tilts else None,
                          "max_deg": max(tilts) if tilts else None},
                "board_center": {"x_min_px": min((c[0] for c in centers), default=None), "x_max_px": max((c[0] for c in centers), default=None),
                                 "y_min_px": min((c[1] for c in centers), default=None), "y_max_px": max((c[1] for c in centers), default=None),
                                 "regions": {name: sum(spatial_region(c) == name for c in centers) for name in ("left", "right", "top", "bottom", "center")}},
                "raw_zones": zones, "novelty_vs_stage2b": novelty(poses),
                "pose_count_is_dynamic_and_unbounded": True, "coverage_is_diagnostic_not_accuracy_gate": True,
                "frozen_authorities": verify_authorities()}
    write_json(COVERAGE_PATH, document); return document


def correlation(rows: list[dict[str, Any]], x_key: str, y_key: str) -> float | None:
    selected = [(float(row[x_key]), float(row[y_key])) for row in rows if row.get("likely_reference_plane") is True]
    if len(selected) < 3: return None
    x, y = np.asarray(selected, dtype=float).T
    return float(np.corrcoef(x, y)[0, 1]) if np.std(x) and np.std(y) else None


def create_plots(records: list[dict[str, Any]], output_dir: Path | None = None) -> None:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    destination = WORKSPACE / "plots" if output_dir is None else output_dir
    destination.mkdir(parents=True, exist_ok=True)
    core = [row for row in records if row.get("likely_reference_plane") is True]
    for field, title, filename in (("median", "Median signed residual (mm)", "raw_zone_median.png"),
                                   ("p95", "P95 absolute residual (mm)", "raw_zone_p95.png"),
                                   ("bias", "Signed bias (mm)", "raw_zone_bias.png")):
        grid = np.full((8, 8), np.nan)
        for index in range(64):
            values = np.asarray([row["point_to_plane_residual_mm"] for row in core if row["raw_index"] == index], dtype=float)
            if len(values): grid[index // 8, index % 8] = (np.percentile(np.abs(values), 95) if field == "p95" else np.median(values) if field == "median" else np.mean(values))
        fig, ax = plt.subplots(figsize=(6, 5)); image = ax.imshow(grid, cmap="coolwarm"); ax.set(title=title, xlabel="raw col", ylabel="raw row")
        fig.colorbar(image, ax=ax); fig.tight_layout(); fig.savefig(destination / filename, dpi=130); plt.close(fig)
    for xkey, xlabel, filename in (("reference_distance_mm", "Plane distance (mm)", "residual_vs_distance.png"),
                                   ("tilt_deg", "Plane tilt (deg)", "residual_vs_tilt.png"),
                                   ("pair_delta_us", "Pair delta (us)", "residual_vs_pair_delta.png")):
        fig, ax = plt.subplots(figsize=(6, 4)); ax.scatter([r[xkey] for r in core], [r["point_to_plane_residual_mm"] for r in core], s=5, alpha=.35)
        ax.set(xlabel=xlabel, ylabel="Orthogonal residual (mm)"); fig.tight_layout(); fig.savefig(destination / filename, dpi=130); plt.close(fig)
    poses = {}
    for row in core: poses[row["pose_id"]] = (row["yaw_like_deg"], row["pitch_like_deg"], row["reference_distance_mm"])
    fig, ax = plt.subplots(figsize=(6, 4))
    if poses:
        data = np.asarray(list(poses.values())); scatter = ax.scatter(data[:, 0], data[:, 1], c=data[:, 2], cmap="viridis")
        fig.colorbar(scatter, ax=ax, label="distance (mm)")
    ax.set(xlabel="yaw-like (deg)", ylabel="pitch-like (deg)"); fig.tight_layout(); fig.savefig(destination / "pose_coverage.png", dpi=130); plt.close(fig)


def distribution(values: Iterable[float]) -> dict[str, Any]:
    array = np.asarray(list(values), dtype=float)
    if not len(array): return {key: None for key in ("count", "min", "median", "p90", "p95", "p99", "max", "mean")}
    return {"count": int(len(array)), "min": float(np.min(array)), "median": float(np.median(array)),
            "p90": float(np.percentile(array, 90)), "p95": float(np.percentile(array, 95)),
            "p99": float(np.percentile(array, 99)), "max": float(np.max(array)), "mean": float(np.mean(array))}


def validate_document(observations: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
    records = observation_records(observations); poses = pose_summary(observations, records)
    core = [row for row in records if row.get("likely_reference_plane") is True]
    broad = [row for row in records if row["broad_valid"]]; strict = [row for row in records if row["strict_valid"]]
    point_core = metric(row["point_to_plane_residual_mm"] for row in core)
    axial_core = metric(row["axial_depth_residual_mm"] for row in core)
    comparison = {}
    for key in ("median_abs_mm", "p95_abs_mm", "rms_mm", "max_abs_mm"):
        reference_key = {"median_abs_mm": "median_mm", "p95_abs_mm": "p95_mm", "rms_mm": "rms_mm", "max_abs_mm": "max_mm"}[key]
        value = point_core[key]; ref = REFERENCE[reference_key]
        comparison[key] = {"stage2c": value, "stage2b_scan_heldout": ref,
                           "delta_mm": None if value is None else value - ref, "ratio": None if value is None else value / ref}
    label = None if point_core["p95_abs_mm"] is None else ("WITHIN_STAGE2B_REFERENCE_RANGE" if point_core["p95_abs_mm"] <= REFERENCE["p95_mm"] else "WORSE_THAN_STAGE2B_REFERENCE")
    pose_core = [pose["core"] for pose in poses if pose["core"]["count"]]
    document = {"schema_version": "stage2c_validation.v1", "generated_at": now(),
                "primary_metric": "signed orthogonal point_to_plane_residual_mm; geometrically comparable across ray incidence",
                "secondary_metric": "axial_depth_residual_mm; preserved separately because scale differs by plane/ray incidence",
                "reference_plane_classifier": observations["plane_consistency_rule"],
                "layers": {"A_core_reference_plane": {"point_to_plane": point_core, "axial_depth": axial_core},
                           "B_all_broad_untrimmed": {"point_to_plane": metric(row["point_to_plane_residual_mm"] for row in broad),
                                                     "axial_depth": metric(row["axial_depth_residual_mm"] for row in broad)},
                           "C_strict_valid_untrimmed": {"point_to_plane": metric(row["point_to_plane_residual_mm"] for row in strict),
                                                        "axial_depth": metric(row["axial_depth_residual_mm"] for row in strict)}},
                "frame_level": frame_summary(observations, records),
                "per_pose_scan_equal": poses,
                "pose_level_distribution": {key: metric(pose["core"][key] for pose in poses if pose["core"][key] is not None)
                                             for key in ("median_abs_mm", "p95_abs_mm", "rms_mm", "max_abs_mm")},
                "per_distance": grouped_metrics(records, "distance_bin", "point_to_plane_residual_mm"),
                "per_angle": grouped_metrics(records, "angle_bin", "point_to_plane_residual_mm"),
                "spatial_bias": grouped_metrics(records, "spatial_region", "point_to_plane_residual_mm"),
                "camera_image_quadrants": grouped_metrics(records, "image_quadrant", "point_to_plane_residual_mm"),
                "raw_row": grouped_metrics(records, "raw_row", "point_to_plane_residual_mm"),
                "raw_col": grouped_metrics(records, "raw_col", "point_to_plane_residual_mm"),
                "raw_zone": grouped_metrics(records, "raw_index", "point_to_plane_residual_mm"),
                "timing": {"pair_delta_us": distribution(row["pair_delta_us"] for row in core),
                           "signed_residual_correlation": correlation(core, "pair_delta_us", "point_to_plane_residual_mm"),
                           "fixed_gate": False},
                "stage2b_reference": {"values": REFERENCE, "comparison": comparison, "diagnostic_label": label,
                                      "hard_pass_gate": False, "decision_owner": "GPT + user"},
                "scan_equal_weighting": "per-pose metrics are first computed within each physical 11-frame scan; pose distributions weight poses equally",
                "no_pose_or_zone_deleted": True, "automatic_blacklist": False, "runtime_policy_modified": False,
                "frozen_authorities": verify_authorities()}
    if persist:
        write_json(VALIDATION_PATH, document); create_plots(records)
    return document


def get_observations(refresh: bool = False) -> dict[str, Any]:
    value = manifest()
    if not value["poses"]:
        return {"schema_version": "stage2c_observations.v1", "generated_at": now(), "pose_count": 0, "poses": [],
                "plane_consistency_rule": {"threshold_mm": None, "classification_only": True, "untrimmed_layers_always_reported": True}}
    if refresh or not OBS_PATH.is_file(): return inspect_registered()
    return load_json(OBS_PATH)


def cmd_status(args: argparse.Namespace) -> int:
    value = manifest(); poses = value["poses"]
    inspected = sum(item.get("analysis_state") == "INSPECTED" for item in poses.values())
    print(f"registered_validation_scans={len(poses)} inspected={inspected} pose_capacity=dynamic_unbounded frozen_authorities_unchanged=true")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    observations = get_observations(refresh=True)
    usable = sum(any(frame["camera_plane"].get("detected") for frame in pose["frames"]) for pose in observations["poses"])
    print(f"inspected_scans={len(observations['poses'])} usable_physical_poses={usable} observations={OBS_PATH}")
    return 0


def cmd_coverage(args: argparse.Namespace) -> int:
    document = coverage_document(get_observations(refresh=args.refresh)); print(json.dumps(document, indent=2)); return 0


def cmd_validate(args: argparse.Namespace) -> int:
    observations = get_observations(refresh=args.refresh)
    if not observations["poses"]:
        print("registered_validation_scans=0 validation_evidence=NOT_AVAILABLE no_refit_path=true")
        return 0
    document = validate_document(observations)
    print(json.dumps({"primary": document["layers"]["A_core_reference_plane"]["point_to_plane"],
                      "diagnostic_label": document["stage2b_reference"]["diagnostic_label"],
                      "output": str(VALIDATION_PATH), "no_refit_path": True}, indent=2)); return 0


def coverage_readiness(coverage: dict[str, Any]) -> tuple[str, str]:
    if coverage["usable_validation_poses"] == 0: return "INSUFFICIENT_VALIDATION_COVERAGE", "capture one independent validation pose"
    missing_distance = [key for key, value in coverage["distance"]["pose_bins"].items() if value == 0]
    missing_angle = [key for key, value in coverage["angle"]["pose_bins"].items() if value == 0]
    if missing_distance: return "INSUFFICIENT_VALIDATION_COVERAGE", f"keep orientation; change distance to cover {missing_distance[0]}"
    if missing_angle: return "INSUFFICIENT_VALIDATION_COVERAGE", f"keep distance; add {missing_angle[0]} tilt"
    novelty_rows = coverage["novelty_vs_stage2b"]["poses"]
    if novelty_rows and all(row["diagnostic_label"] == "LIKELY_REPEAT" for row in novelty_rows):
        return "INSUFFICIENT_VALIDATION_COVERAGE", "keep distance; move right for a pose distinct from Stage2B"
    return "VALIDATION_EVIDENCE_READY", "coverage evidence is ready for GPT/user judgment"


def cmd_summary(args: argparse.Namespace) -> int:
    observations = get_observations(refresh=False); coverage = coverage_document(observations)
    validation = (load_json(VALIDATION_PATH) if VALIDATION_PATH.is_file() else validate_document(observations)) if observations["poses"] else None
    status, suggestion = coverage_readiness(coverage); auth = verify_authorities()
    summary = {"schema_version": "stage2c_summary.v1", "generated_at": now(), "status": status, "specific_next_suggestion": suggestion,
               "registered_validation_scans": coverage["registered_validation_scans"], "core_overall": None if validation is None else validation["layers"]["A_core_reference_plane"]["point_to_plane"],
               "pose_level_distribution": None if validation is None else validation["pose_level_distribution"],
               "novelty_coverage": coverage["novelty_vs_stage2b"], "frozen_authorities_unchanged": auth["frozen_authorities_unchanged"],
               "frozen_authority_hashes": {key: value["sha256"] for key, value in auth["authorities"].items()},
               "candidate_bytes_unchanged": True, "promotion": False, "stage3_started": False, "decision_owner": "GPT + user"}
    write_json(WORKSPACE / "validation" / "summary.stage2c.json", summary); print(json.dumps(summary, indent=2)); return 0


def cmd_suggest_next(args: argparse.Namespace) -> int:
    coverage = coverage_document(get_observations(refresh=False)); status, suggestion = coverage_readiness(coverage)
    print(f"status={status}\n- {suggestion}\nReason: fills independent validation coverage only; frozen parameters remain unchanged.\nCapture preference: snake path; one direction at a time; left/right before up/down; isolate distance changes.")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Stage2C independent validation of frozen Stage2A/Stage2B Camera<->ToF geometry")
    sub = root.add_subparsers(dest="command", required=True)
    command = sub.add_parser("register", help="register a complete formal 11-frame scan (hardlink preferred)")
    command.add_argument("--pose", required=True); choice = command.add_mutually_exclusive_group(required=True)
    choice.add_argument("--latest", action="store_true"); choice.add_argument("--scan-id")
    command.add_argument("--dry-run", action="store_true"); command.set_defaults(func=cmd_register)
    sub.add_parser("status", help="show dynamic registry and frozen-authority state").set_defaults(func=cmd_status)
    sub.add_parser("inspect", help="detect ChArUco planes and generate frame overlays").set_defaults(func=cmd_inspect)
    for name, function in (("coverage", cmd_coverage), ("validate", cmd_validate)):
        command = sub.add_parser(name); command.add_argument("--refresh", action="store_true"); command.set_defaults(func=function)
    sub.add_parser("summary").set_defaults(func=cmd_summary)
    sub.add_parser("suggest-next", help="suggest an independent-coverage capture, never a parameter-estimation pose").set_defaults(func=cmd_suggest_next)
    return root


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = parser().parse_args(argv); return int(arguments.func(arguments))
    except Stage2CError as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
