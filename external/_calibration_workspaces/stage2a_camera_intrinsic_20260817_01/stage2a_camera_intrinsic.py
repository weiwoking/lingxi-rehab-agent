#!/usr/bin/env python3
"""Stage 2A camera intrinsic recalibration workspace tool.

This tool is deliberately read-only with respect to formal scans and calibration.
All generated data stays below the directory containing this file.
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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

WORKSPACE = Path(__file__).resolve().parent
SCAN_ROOT = Path(r"C:\ec\_deltar_scan_service_data\validated")
OLD_STAGE7A = Path(r"C:\calibration\result_20260719_172703\calibration_profile.v2.json")
RUNTIME_LAUNCHER = Path(r"C:\ec\esp-claw\tools\Start-VisionAndScanPC\start_vision_and_scan_services.ps1")
WIDTH, HEIGHT, PAYLOAD_BYTES = 640, 360, 460800
LEGACY_POSE_IDS = tuple([f"A{i:02d}" for i in range(1, 5)] + [f"B{i:02d}" for i in range(1, 9)] + [f"C{i:02d}" for i in range(1, 9)])
POSE_ID_PATTERN = re.compile(r"^[A-Z0-9](?:[A-Z0-9_-]{0,62}[A-Z0-9])?$")
BOARD = {
    "family": "opencv_charuco", "dictionary": "DICT_5X5_100",
    "squares_x": 7, "squares_y": 5, "square_length_mm": 25.0,
    "marker_length_mm": 18.0, "active_width_mm": 175.0, "active_height_mm": 125.0,
}
MANIFEST_PATH = WORKSPACE / "workspace_manifest.json"
CANDIDATE_PATH = WORKSPACE / "candidate" / "camera_intrinsics.stage2a_candidate.v1.json"
SCHEMA_PATH = WORKSPACE / "candidate_schema.json"


class Stage2AError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    os.replace(temp, path)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def ensure_layout() -> None:
    for name in ("imports", "manifests", "detections", "rankings", "previews", "plots", "candidate", "comparison", "validation", "logs", "tmp", "pycache"):
        (WORKSPACE / name).mkdir(parents=True, exist_ok=True)
    if not MANIFEST_PATH.exists():
        write_json(MANIFEST_PATH, {
            "schema_version": "stage2a_workspace_manifest.v1", "created_at": now(),
            "workspace": str(WORKSPACE), "scan_root": str(SCAN_ROOT),
            "camera_raster": {"width": WIDTH, "height": HEIGHT, "pixel_format": "RGB565_LE", "stride_bytes": 1280, "payload_bytes": PAYLOAD_BYTES},
            "board": BOARD, "legacy_planned_pose_ids": list(LEGACY_POSE_IDS), "poses": {},
        })


def manifest() -> dict[str, Any]:
    ensure_layout()
    value = load_json(MANIFEST_PATH)
    if value.get("schema_version") != "stage2a_workspace_manifest.v1":
        raise Stage2AError("unsupported workspace manifest")
    if not isinstance(value.get("poses"), dict):
        raise Stage2AError("workspace manifest poses must be an object")
    return value


def save_manifest(value: dict[str, Any]) -> None:
    value["updated_at"] = now()
    write_json(MANIFEST_PATH, value)


def require_pose(pose: str) -> str:
    pose = pose.upper()
    if not POSE_ID_PATTERN.fullmatch(pose):
        raise Stage2AError("invalid pose_id; use 1-64 ASCII letters/digits with internal '_' or '-', starting and ending with a letter/digit")
    return pose


def registered_pose_ids(value: dict[str, Any]) -> tuple[str, ...]:
    """Return the actual dataset, in manifest registration order."""
    return tuple(
        pose for pose, item in value["poses"].items()
        if isinstance(item, dict) and bool(item.get("scan_id"))
    )


def discover_scans() -> list[Path]:
    if not SCAN_ROOT.is_dir():
        return []
    return sorted((p for p in SCAN_ROOT.iterdir() if p.is_dir() and p.name.startswith("scan_")), key=lambda p: p.name)


def resolve_scan(scan_id: str | None, latest: bool) -> Path:
    if bool(scan_id) == bool(latest):
        raise Stage2AError("choose exactly one of --scan-id or --latest")
    if latest:
        scans = discover_scans()
        if not scans:
            raise Stage2AError(f"no scans found under {SCAN_ROOT}")
        return scans[-1]
    assert scan_id is not None
    if Path(scan_id).name != scan_id or not scan_id.startswith("scan_"):
        raise Stage2AError("--scan-id must be a leaf scan_... identifier")
    path = (SCAN_ROOT / scan_id).resolve()
    try:
        path.relative_to(SCAN_ROOT.resolve())
    except ValueError as exc:
        raise Stage2AError("scan resolves outside the formal scan root") from exc
    if not path.is_dir():
        raise Stage2AError(f"scan not found: {path}")
    return path


def validate_scan(scan_dir: Path, require_11: bool = True, with_hashes: bool = True) -> dict[str, Any]:
    errors: list[str] = []
    scan_path = scan_dir / "scan.json"
    if not scan_path.is_file():
        raise Stage2AError(f"missing {scan_path}")
    scan = load_json(scan_path)
    if scan.get("schema_version") != "deltar_scan_input.v1": errors.append("scan schema_version")
    if scan.get("scan_id") != scan_dir.name: errors.append("scan_id/directory mismatch")
    count = scan.get("frame_count")
    if type(count) is not int: errors.append("frame_count is not integer"); count = 0
    if require_11 and count != 11: errors.append(f"frame_count={count}, expected 11")
    frames: list[dict[str, Any]] = []
    frame_dirs = sorted((scan_dir / "frames").glob("frame_*")) if (scan_dir / "frames").is_dir() else []
    if len(frame_dirs) != count: errors.append(f"frame directory count={len(frame_dirs)}, declared={count}")
    for sequence in range(count):
        frame_id = f"frame_{sequence:06d}"
        folder = scan_dir / "frames" / frame_id
        fp, ip, tp = folder / "frame.json", folder / "image.rgb565", folder / "tof.json"
        if not all(p.is_file() for p in (fp, ip, tp)):
            errors.append(f"{frame_id}: required file missing"); continue
        f, tof = load_json(fp), load_json(tp)
        if f.get("frame_id") != frame_id or f.get("sequence") != sequence: errors.append(f"{frame_id}: identity/sequence")
        image = f.get("image", {})
        if image.get("width") != WIDTH or image.get("height") != HEIGHT or image.get("pixel_format") != "RGB565_LE" or image.get("stride_bytes") != 1280: errors.append(f"{frame_id}: raster contract")
        if ip.stat().st_size != PAYLOAD_BYTES or image.get("payload_bytes") != PAYLOAD_BYTES: errors.append(f"{frame_id}: payload size")
        cam_ts, tof_ts, delta = f.get("camera_timestamp_us"), f.get("tof_timestamp_us"), f.get("pair_delta_us")
        if not all(type(x) is int for x in (cam_ts, tof_ts, delta)) or delta != abs(cam_ts - tof_ts): errors.append(f"{frame_id}: timestamp/pair_delta")
        if tof.get("sequence") != sequence or tof.get("timestamp_us") != tof_ts: errors.append(f"{frame_id}: ToF sequence/timestamp")
        row = {"frame_id": frame_id, "frame_sequence": sequence, "camera_file": str(ip), "camera_timestamp_us": cam_ts, "tof_timestamp_us": tof_ts, "pair_delta_us": delta}
        if with_hashes:
            row["source_sha256"] = {p.name: sha256_file(p) for p in (fp, ip, tp)}
        frames.append(row)
    return {"valid": not errors, "errors": errors, "scan_id": scan_dir.name, "scan_dir": str(scan_dir.resolve()), "scan": scan, "frames": frames, "scan_json_sha256": sha256_file(scan_path) if with_hashes else None}


def decode_rgb565(path: Path):
    import cv2
    import numpy as np
    raw = np.fromfile(str(path), dtype="<u2")
    if raw.size != WIDTH * HEIGHT:
        raise Stage2AError(f"RGB565 payload is {raw.size * 2} bytes, expected {PAYLOAD_BYTES}: {path}")
    r5, g6, b5 = (raw >> 11) & 0x1F, (raw >> 5) & 0x3F, raw & 0x1F
    rgb = np.stack(((r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4), (b5 << 3) | (b5 >> 2)), axis=-1).astype(np.uint8).reshape(HEIGHT, WIDTH, 3)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def cv_board():
    import cv2
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
    board = cv2.aruco.CharucoBoard((7, 5), 0.025, 0.018, dictionary)
    return dictionary, board


def detect_image(path: Path, old_profile: dict[str, Any] | None = None) -> tuple[dict[str, Any], Any, Any]:
    import cv2
    import numpy as np
    bgr = decode_rgb565(path)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    dictionary, board = cv_board()
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.CharucoDetector(board, cv2.aruco.CharucoParameters(), params)
    corners, ids, marker_corners, marker_ids = detector.detectBoard(gray)
    marker_count = 0 if marker_ids is None else len(marker_ids)
    corner_count = 0 if ids is None else len(ids)
    points = np.empty((0, 2), np.float32) if corners is None else corners.reshape(-1, 2).astype(np.float32)
    hull_area = float(cv2.contourArea(cv2.convexHull(points))) if len(points) >= 3 else 0.0
    center = points.mean(axis=0) if len(points) else None
    margin = float(min(points[:, 0].min(), WIDTH - 1 - points[:, 0].max(), points[:, 1].min(), HEIGHT - 1 - points[:, 1].max())) if len(points) else -1.0
    cells = set((min(3, int(x / WIDTH * 4)), min(2, int(y / HEIGHT * 3))) for x, y in points)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    black = float((gray <= 5).mean())
    white = float((gray >= 250).mean())
    saturation = float((hsv[:, :, 1] >= 250).mean())
    pnp_rms = None
    viewing_angle = None
    if corner_count >= 6 and old_profile:
        intr = old_profile["camera"]["intrinsics"]
        K = np.array([[intr["fx"], 0, intr["cx"]], [0, intr["fy"], intr["cy"]], [0, 0, 1]], np.float64)
        d = intr["distortion"]
        dist = np.array([d["k1"], d["k2"], d["p1"], d["p2"], d["k3"]], np.float64)
        obj = board.getChessboardCorners()[ids.reshape(-1)].reshape(-1, 3)
        ok, rv, tv = cv2.solvePnP(obj, points, K, dist)
        if ok:
            projected, _ = cv2.projectPoints(obj, rv, tv, K, dist)
            pnp_rms = float(np.sqrt(np.mean(np.sum((projected.reshape(-1, 2) - points) ** 2, axis=1))))
            rotation, _ = cv2.Rodrigues(rv)
            normal = rotation[:, 2]
            viewing_angle = float(np.degrees(np.arccos(np.clip(abs(normal[2]) / np.linalg.norm(normal), 0.0, 1.0))))
    coverage = hull_area / (WIDTH * HEIGHT)
    exposure_mean = float(gray.mean())
    corner_term = min(1.0, corner_count / 24.0)
    coverage_term = min(1.0, coverage / 0.15)
    blur_term = min(1.0, math.log1p(max(0.0, blur)) / math.log(1001.0))
    exposure_term = max(0.0, 1.0 - abs(exposure_mean - 128.0) / 128.0)
    border_term = max(0.0, min(1.0, margin / 20.0))
    residual_term = 0.5 if pnp_rms is None else 1.0 / (1.0 + pnp_rms)
    score = 100.0 * (0.31 * corner_term + 0.18 * coverage_term + 0.17 * blur_term + 0.10 * exposure_term + 0.08 * (1.0 - min(1.0, black + white + saturation)) + 0.07 * border_term + 0.09 * residual_term)
    metrics = {
        "decode_status": "OK", "detected_marker_count": marker_count, "detected_charuco_corner_count": corner_count,
        "board_coverage": coverage, "board_center_x": (float(center[0]) if center is not None else None), "board_center_y": (float(center[1]) if center is not None else None),
        "convex_hull_area_px": hull_area, "corner_spatial_cells_4x3": len(cells), "blur_laplacian_variance": blur,
        "exposure_mean": exposure_mean, "saturation_fraction": saturation, "black_clipping_fraction": black,
        "white_clipping_fraction": white, "border_margin_px": margin, "provisional_pnp_rms_px": pnp_rms,
        "viewing_angle_proxy_deg": viewing_angle,
        "quality_score": score,
    }
    return metrics, (None if corners is None else corners.astype(np.float32)), (None if ids is None else ids.astype(np.int32))


def cmd_inspect(args: argparse.Namespace) -> int:
    ensure_layout()
    scans = discover_scans()
    chosen = resolve_scan(args.scan_id, args.latest) if (args.scan_id or args.latest) else None
    result = {
        "generated_at": now(), "workspace": str(WORKSPACE), "scan_root": str(SCAN_ROOT), "scan_count": len(scans),
        "latest_scan_id": scans[-1].name if scans else None, "formal_contract": {"frame_count": 11, "frame_ids": [f"frame_{i:06d}" for i in range(11)], "camera": {"width": WIDTH, "height": HEIGHT, "pixel_format": "RGB565_LE", "byte_order": "little_endian", "stride_bytes": 1280, "payload_bytes": PAYLOAD_BYTES}, "files": ["scan.json", "frames/<frame_id>/frame.json", "frames/<frame_id>/image.rgb565", "frames/<frame_id>/tof.json"], "jpeg_preview_in_validated_input": False},
        "active_stage7a": {"path": str(OLD_STAGE7A), "exists": OLD_STAGE7A.is_file(), "sha256": sha256_file(OLD_STAGE7A) if OLD_STAGE7A.is_file() else None, "runtime_launcher": str(RUNTIME_LAUNCHER)},
        "selected_scan": validate_scan(chosen, with_hashes=False) if chosen else None,
    }
    if chosen and args.detect_charuco:
        old = load_json(OLD_STAGE7A)
        dry_rows = []
        for row in result["selected_scan"]["frames"]:
            try:
                metrics, _, _ = detect_image(Path(row["camera_file"]), old)
                dry_rows.append({"frame_id": row["frame_id"], **metrics})
            except Exception as exc:
                dry_rows.append({"frame_id": row["frame_id"], "decode_status": "CAMERA_DECODE_FAIL", "error": str(exc)})
        result["charuco_dry_run"] = {"source_read_only": True, "frames_checked": len(dry_rows), "frames": dry_rows}
        write_json(WORKSPACE / "logs" / "historical_dry_run.json", result["charuco_dry_run"])
    write_json(WORKSPACE / "logs" / "inspect.json", result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def copy_or_link(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha256_file(target) != sha256_file(source): raise Stage2AError(f"existing imported file hash mismatch: {target}")
        return "existing"
    try:
        os.link(source, target)
        return "hardlink"
    except OSError:
        shutil.copy2(source, target)
        return "copy"


def cmd_register(args: argparse.Namespace) -> int:
    pose = require_pose(args.pose)
    value = manifest()
    current = value["poses"].get(pose, {})
    if current.get("scan_id") and not args.replace:
        raise Stage2AError(f"{pose} already registered to {current['scan_id']}; use --replace explicitly")
    source = resolve_scan(args.scan_id, args.latest)
    checked = validate_scan(source, require_11=True, with_hashes=True)
    if not checked["valid"]:
        raise Stage2AError("INVALID_SCAN: " + "; ".join(checked["errors"]))
    if args.dry_run:
        print(json.dumps({"pose_id": pose, "scan_id": source.name, "valid_11_frame_scan": True, "source_path": str(source), "source_tree_sha256": canonical_sha({f["frame_id"]: f["source_sha256"] for f in checked["frames"]}), "action": "DRY_RUN_NO_IMPORT_NO_MANIFEST_CHANGE"}, indent=2))
        return 0
    destination = WORKSPACE / "imports" / pose / source.name
    modes: set[str] = set()
    sources = [source / "scan.json"]
    for i in range(11):
        folder = source / "frames" / f"frame_{i:06d}"
        sources.extend([folder / "frame.json", folder / "image.rgb565", folder / "tof.json"])
    source_hashes_before = {str(p.relative_to(source)): sha256_file(p) for p in sources}
    for p in sources: modes.add(copy_or_link(p, destination / p.relative_to(source)))
    source_hashes_after = {str(p.relative_to(source)): sha256_file(p) for p in sources}
    if source_hashes_before != source_hashes_after: raise Stage2AError("source scan changed during registration")
    frames = []
    for row in checked["frames"]:
        imported = destination / "frames" / row["frame_id"] / "image.rgb565"
        frames.append({**row, "camera_file": str(imported.resolve()), "source_camera_file": row["camera_file"]})
    value["poses"][pose] = {
        "state": "REGISTERED", "registered_at": now(), "scan_id": source.name, "source_path": str(source.resolve()),
        "import_path": str(destination.resolve()), "import_mode": sorted(modes), "scan_json_sha256": checked["scan_json_sha256"],
        "source_tree_sha256": canonical_sha(source_hashes_before), "source_hashes": source_hashes_before, "frames": frames,
    }
    save_manifest(value)
    write_json(WORKSPACE / "manifests" / f"{pose}.json", {"pose_id": pose, **value["poses"][pose]})
    print(f"{pose}: REGISTERED {source.name} ({'/'.join(sorted(modes))}); source hashes unchanged")
    return 0


def registered_count(value: dict[str, Any]) -> int:
    return len(registered_pose_ids(value))


def cmd_status(args: argparse.Namespace) -> int:
    value = manifest(); pose_ids = registered_pose_ids(value)
    selected = sum(bool(value["poses"][pose].get("selected_frame")) for pose in pose_ids)
    heldout = sum(bool(value["poses"][pose].get("heldout_frame")) for pose in pose_ids)
    extras = sum(pose not in LEGACY_POSE_IDS for pose in pose_ids)
    legacy_registered = sum(pose in pose_ids for pose in LEGACY_POSE_IDS)
    print(f"registered={len(pose_ids)} selected={selected} heldout={heldout} extras={extras}")
    print(f"legacy/planned registered={legacy_registered}/{len(LEGACY_POSE_IDS)} (informational only; no maximum)")
    for pose in pose_ids:
        item = value["poses"][pose]
        suffix = f" scan={item.get('scan_id')}" if item.get("scan_id") else ""
        selected = item.get("selected_frame")
        heldout = item.get("heldout_frame")
        if selected: suffix += f" fit={selected} heldout={heldout or '-'}"
        print(f"{pose} {item.get('state', 'MISSING')}{suffix}")
    return 0


def contact_sheet(pose: str, entries: list[dict[str, Any]]) -> None:
    import cv2
    import numpy as np
    thumb_w, thumb_h = 320, 180
    canvas = np.full((3 * (thumb_h + 35), 4 * thumb_w, 3), 245, np.uint8)
    for idx, entry in enumerate(entries):
        image = decode_rgb565(Path(entry["camera_file"]))
        corners = entry.get("_corners"); ids = entry.get("_ids")
        if corners is not None and ids is not None: cv2.aruco.drawDetectedCornersCharuco(image, corners, ids)
        image = cv2.resize(image, (thumb_w, thumb_h))
        row, col = divmod(idx, 4); y, x = row * (thumb_h + 35), col * thumb_w
        canvas[y:y + thumb_h, x:x + thumb_w] = image
        cv2.putText(canvas, f"{entry['frame_id']} rank={entry['rank']} score={entry['quality_score']:.1f} c={entry['detected_charuco_corner_count']}", (x + 4, y + thumb_h + 22), cv2.FONT_HERSHEY_SIMPLEX, .38, (20, 20, 20), 1, cv2.LINE_AA)
    cv2.imwrite(str(WORKSPACE / "previews" / f"{pose}_contact_sheet.png"), canvas)


def rank_all() -> list[dict[str, Any]]:
    old = load_json(OLD_STAGE7A)
    value = manifest(); all_rows: list[dict[str, Any]] = []
    for pose in registered_pose_ids(value):
        item = value["poses"][pose]
        if not item.get("scan_id"): continue
        entries = []
        for frame in item["frames"]:
            try:
                metrics, corners, ids = detect_image(Path(frame["camera_file"]), old)
            except Exception as exc:
                metrics = {"decode_status": "CAMERA_DECODE_FAIL", "quality_score": -1.0, "detected_charuco_corner_count": 0, "error": str(exc)}
                corners = ids = None
            entry = {"pose_id": pose, "scan_id": item["scan_id"], **frame, **metrics, "_corners": corners, "_ids": ids}
            entries.append(entry)
        entries.sort(key=lambda e: (e["decode_status"] == "OK", e["detected_charuco_corner_count"], e["quality_score"]), reverse=True)
        for rank, entry in enumerate(entries, 1): entry["rank"] = rank
        contact_sheet(pose, entries)
        serial = []
        for entry in entries:
            corners, ids = entry.pop("_corners"), entry.pop("_ids")
            detection_file = WORKSPACE / "detections" / pose / f"{entry['frame_id']}.npz"
            detection_file.parent.mkdir(parents=True, exist_ok=True)
            if corners is not None and ids is not None:
                import numpy as np
                np.savez_compressed(detection_file, corners=corners, ids=ids)
                entry["detection_file"] = str(detection_file)
            serial.append(entry); all_rows.append(entry)
        strong = [e for e in serial if e["decode_status"] == "OK" and e["detected_charuco_corner_count"] >= 8]
        if not strong:
            item["state"] = "CHARUCO_WEAK" if any(e["decode_status"] == "OK" for e in serial) else "CAMERA_DECODE_FAIL"
            item.pop("selected_frame", None); item.pop("heldout_frame", None)
        else:
            item["selected_frame"] = strong[0]["frame_id"]
            item["heldout_frame"] = strong[1]["frame_id"] if len(strong) > 1 else None
            item["heldout_state"] = "HELD_OUT" if len(strong) > 1 else None
            item["state"] = "SELECTED" if len(strong) > 1 else "READY"
        item["ranking_file"] = str(WORKSPACE / "rankings" / f"{pose}.json")
        write_json(Path(item["ranking_file"]), serial)
    save_manifest(value)
    columns = sorted({k for row in all_rows for k in row if k not in {"source_sha256"}})
    with (WORKSPACE / "pose_frame_ranking.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore"); writer.writeheader()
        for row in all_rows: writer.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in row.items()})
    coverage_plot(all_rows, value)
    return all_rows


def coverage_plot(rows: list[dict[str, Any]], value: dict[str, Any]) -> None:
    import cv2
    import numpy as np
    selected = []
    for pose in registered_pose_ids(value):
        frame_id = value["poses"][pose].get("selected_frame")
        selected.extend(r for r in rows if r["pose_id"] == pose and r["frame_id"] == frame_id)
    canvas = np.full((720, 1280, 3), 255, np.uint8)
    for row in selected:
        x, y = row.get("board_center_x"), row.get("board_center_y")
        if x is not None and y is not None and math.isfinite(x) and math.isfinite(y):
            digest = hashlib.sha256(row["pose_id"].encode("ascii")).digest()
            color = tuple(55 + component % 151 for component in digest[:3])
            cv2.circle(canvas, (int(x), int(y)), 7, color, -1); cv2.putText(canvas, row["pose_id"], (int(x)+8, int(y)), cv2.FONT_HERSHEY_SIMPLEX, .4, color, 1)
    cv2.rectangle(canvas, (0, 0), (WIDTH-1, HEIGHT-1), (0, 0, 0), 2)
    cv2.putText(canvas, "Selected board centres / image-region coverage", (10, 345), cv2.FONT_HERSHEY_SIMPLEX, .62, (0,0,0), 2)
    heat = np.zeros((HEIGHT, WIDTH), np.float32)
    for row in selected:
        npz = WORKSPACE / "detections" / row["pose_id"] / f"{row['frame_id']}.npz"
        if npz.is_file():
            points = np.load(npz)["corners"].reshape(-1, 2)
            for x, y in points: cv2.circle(heat, (int(x), int(y)), 18, 1.0, -1)
    heat = cv2.GaussianBlur(heat, (0,0), 13)
    if float(heat.max()) > 0: heat = heat / heat.max() * 255
    colored = cv2.applyColorMap(heat.astype(np.uint8), cv2.COLORMAP_TURBO)
    canvas[0:HEIGHT, WIDTH:2*WIDTH] = colored
    cv2.putText(canvas, "Selected ChArUco corner coverage heatmap", (WIDTH+10, 345), cv2.FONT_HERSHEY_SIMPLEX, .62, (255,255,255), 2)
    scales = [r.get("board_coverage", 0.0) for r in selected]
    if scales:
        step = 600.0 / len(scales); bar_width = max(2, min(18, int(step * .65)))
        for i, val in enumerate(scales):
            x0 = 20 + int(i * step); h = min(260, int(val * 1400))
            cv2.rectangle(canvas, (x0, 680-h), (x0+bar_width, 680), (80,120,210), -1)
        cv2.putText(canvas, "Board area / scale distribution", (20, 410), cv2.FONT_HERSHEY_SIMPLEX, .65, (0,0,0), 2)
    angles=[r.get("viewing_angle_proxy_deg") for r in selected if r.get("viewing_angle_proxy_deg") is not None]
    angle_step = 600.0 / max(1, len(angles)); angle_bar_width = max(2, min(18, int(angle_step * .65)))
    for i,val in enumerate(angles):
        x0=660+int(i*angle_step); h=min(240,int(val*5)); cv2.rectangle(canvas,(x0,680-h),(x0+angle_bar_width,680),(190,90,70),-1)
    extra_count=sum(r["pose_id"] not in LEGACY_POSE_IDS for r in selected)
    cv2.putText(canvas, f"Viewing-angle proxy; selected={len(selected)}, extras={extra_count}", (660,410), cv2.FONT_HERSHEY_SIMPLEX, .58, (0,0,0), 2)
    cv2.imwrite(str(WORKSPACE / "dataset_coverage.png"), canvas)
    with (WORKSPACE / "coverage_metrics.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        cols = ["pose_id","group","frame_id","board_center_x","board_center_y","board_coverage","corner_spatial_cells_4x3","viewing_angle_proxy_deg","provisional_pnp_rms_px"]
        w = csv.DictWriter(stream, fieldnames=cols); w.writeheader()
        for r in selected:
            row = {**r, "group": "legacy" if r["pose_id"] in LEGACY_POSE_IDS else "extra"}
            w.writerow({column: row.get(column) for column in cols})


def cmd_rank(args: argparse.Namespace) -> int:
    rows = rank_all(); print(f"ranked {len(rows)} frames; outputs: pose_frame_ranking.csv, previews/, dataset_coverage.png")
    return 0


def load_observation(pose: str, frame_id: str) -> tuple[Any, Any]:
    import numpy as np
    data = np.load(WORKSPACE / "detections" / pose / f"{frame_id}.npz")
    return data["corners"].astype(np.float32), data["ids"].astype(np.int32)


def calibrate_set(selection: list[tuple[str, str]]) -> dict[str, Any]:
    import cv2
    import numpy as np
    _, board = cv_board()
    corners, ids, object_points, image_points = [], [], [], []
    for pose, frame in selection:
        c, i = load_observation(pose, frame)
        obj, img = board.matchImagePoints(c, i)
        corners.append(c); ids.append(i); object_points.append(obj.astype(np.float32)); image_points.append(img.astype(np.float32))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_COUNT, 100, 1e-9)
    result = cv2.calibrateCameraExtended(object_points, image_points, (WIDTH, HEIGHT), None, None, flags=0, criteria=criteria)
    rms, K, dist, rvecs, tvecs = result[:5]
    dist = dist.reshape(-1)
    per_views = []
    chess = board.getChessboardCorners()
    all_errors = []
    for (pose, frame), c, i, rv, tv in zip(selection, corners, ids, rvecs, tvecs):
        obj = chess[i.reshape(-1)].reshape(-1, 3)
        proj, _ = cv2.projectPoints(obj, rv, tv, K, dist)
        err = np.linalg.norm(proj.reshape(-1,2) - c.reshape(-1,2), axis=1); all_errors.extend(err.tolist())
        per_views.append({"pose_id": pose, "frame_id": frame, "corner_count": len(err), "rms_px": float(np.sqrt(np.mean(err**2))), "p95_px": float(np.percentile(err,95)), "max_px": float(err.max())})
    return {"rms": float(rms), "K": K, "dist": dist, "rvecs": rvecs, "tvecs": tvecs, "per_views": per_views, "all_errors": np.array(all_errors)}


def heldout_metrics(K, dist, selection: list[tuple[str, str]]) -> dict[str, Any]:
    import cv2
    import numpy as np
    _, board = cv_board(); chess = board.getChessboardCorners(); rows=[]; all_err=[]; radii=[]; vectors=[]
    for pose, frame in selection:
        c, ids = load_observation(pose, frame); obj = chess[ids.reshape(-1)].reshape(-1,3)
        ok, rv, tv = cv2.solvePnP(obj, c.reshape(-1,2), K, dist)
        if not ok: continue
        proj,_=cv2.projectPoints(obj,rv,tv,K,dist); observed=c.reshape(-1,2); projected=proj.reshape(-1,2)
        delta=projected-observed; err=np.linalg.norm(delta,axis=1); all_err.extend(err.tolist())
        norm_radius=np.linalg.norm((observed-np.array([K[0,2],K[1,2]]))/np.array([K[0,0],K[1,1]]),axis=1)
        radii.extend(norm_radius.tolist())
        vectors.extend([[pose,frame,float(x),float(y),float(dx),float(dy),float(e),float(r)] for (x,y),(dx,dy),e,r in zip(observed,delta,err,norm_radius)])
        rows.append({"pose_id":pose,"frame_id":frame,"corner_count":len(err),"rms_px":float(np.sqrt(np.mean(err**2))),"p95_px":float(np.percentile(err,95)),"max_px":float(err.max())})
    arr=np.asarray(all_err,np.float64)
    return {"frames":len(rows),"corners":len(arr),"rms_px":float(np.sqrt(np.mean(arr**2))) if len(arr) else None,"p95_px":float(np.percentile(arr,95)) if len(arr) else None,"max_px":float(arr.max()) if len(arr) else None,"per_pose":rows,"vectors":vectors,"radii":radii,"errors":all_err}


def parameter_dict(fit: dict[str, Any]) -> dict[str, float]:
    K, d = fit["K"], fit["dist"]
    values=list(d)+[0]*5
    return {"fx":float(K[0,0]),"fy":float(K[1,1]),"cx":float(K[0,2]),"cy":float(K[1,2]),"k1":float(values[0]),"k2":float(values[1]),"p1":float(values[2]),"p2":float(values[3]),"k3":float(values[4])}


def save_residual_outputs(held: dict[str, Any]) -> None:
    import cv2
    import numpy as np
    with (WORKSPACE/"validation"/"heldout_residuals.csv").open("w",encoding="utf-8-sig",newline="") as s:
        w=csv.writer(s); w.writerow(["pose_id","frame_id","x","y","dx","dy","error_px","normalized_radius"]); w.writerows(held["vectors"])
    canvas=np.full((HEIGHT,WIDTH,3),255,np.uint8)
    for _,_,x,y,dx,dy,e,_ in held["vectors"]:
        p=(int(x),int(y)); q=(int(x+dx*12),int(y+dy*12)); cv2.arrowedLine(canvas,p,q,(0,0,220),1,tipLength=.25)
    cv2.imwrite(str(WORKSPACE/"validation"/"heldout_residual_vector_field.png"),canvas)
    plot=np.full((480,720,3),255,np.uint8); radii=held["radii"]; errors=held["errors"]
    if radii and errors:
        xmax=max(radii) or 1; ymax=max(max(errors),.25)
        for r,e in zip(radii,errors): cv2.circle(plot,(50+int(r/xmax*640),430-int(e/ymax*380)),2,(190,70,30),-1)
    cv2.line(plot,(50,430),(690,430),(0,0,0),1); cv2.line(plot,(50,50),(50,430),(0,0,0),1)
    cv2.putText(plot,"held-out residual vs normalized image radius",(80,30),cv2.FONT_HERSHEY_SIMPLEX,.65,(0,0,0),2)
    cv2.imwrite(str(WORKSPACE/"validation"/"residual_vs_image_radius.png"),plot)


def cmd_calibrate(args: argparse.Namespace) -> int:
    rank_all(); value=manifest(); count=registered_count(value)
    if count < 12: raise Stage2AError(f"calibration gated: {count} poses registered; minimum 12")
    primary=[]; held=[]; top2=[]
    weak=[]
    for pose in registered_pose_ids(value):
        item=value["poses"][pose]; first=item.get("selected_frame"); second=item.get("heldout_frame")
        if not first: weak.append(pose); continue
        primary.append((pose,first)); top2.append((pose,first))
        if second: held.append((pose,second)); top2.append((pose,second))
    if weak and not args.allow_weak: raise Stage2AError("CHARUCO_WEAK poses: "+", ".join(weak)+"; recapture or explicitly use --allow-weak")
    if len(primary)<12 or len(held)<8: raise Stage2AError(f"insufficient usable diversity: primary={len(primary)}, heldout={len(held)}")
    primary_fit=calibrate_set(primary); top2_fit=calibrate_set(top2)
    rms_values=[r["rms_px"] for r in primary_fit["per_views"]]; med=float(__import__('numpy').median(rms_values)); mad=float(__import__('numpy').median(__import__('numpy').abs(__import__('numpy').array(rms_values)-med)))
    threshold=max(med+3*max(mad,1e-6),float(__import__('numpy').percentile(rms_values,95)))
    filtered_sel=[s for s,r in zip(primary,primary_fit["per_views"]) if r["rms_px"]<=threshold]
    filtered_fit=calibrate_set(filtered_sel) if len(filtered_sel)>=12 and len(filtered_sel)<len(primary) else primary_fit
    held_metrics=heldout_metrics(primary_fit["K"],primary_fit["dist"],held)
    params=parameter_dict(primary_fit); top2_params=parameter_dict(top2_fit); filtered_params=parameter_dict(filtered_fit)
    sensitivity={k:{"top2_abs_delta":top2_params[k]-params[k],"filtered_abs_delta":filtered_params[k]-params[k],"top2_relative_delta":(top2_params[k]-params[k])/params[k] if params[k] else None,"filtered_relative_delta":(filtered_params[k]-params[k])/params[k] if params[k] else None} for k in params}
    manifest_sha=sha256_file(MANIFEST_PATH); old_sha=sha256_file(OLD_STAGE7A); tool_sha=sha256_file(Path(__file__))
    candidate={
      "schema_version":"stage2a_camera_intrinsics_candidate.v1","candidate_status":"CANDIDATE_NOT_PROMOTED","created_at":now(),
      "camera":{"resolution":{"width":WIDTH,"height":HEIGHT},"source_raster_provenance":{"formal_scan_root":str(SCAN_ROOT),"pixel_format":"RGB565_LE","byte_order":"little_endian","stride_bytes":1280,"payload_bytes":PAYLOAD_BYTES,"decode_color_semantics":"RGB","crop":None,"resize":None},"intrinsics":{"model":"opencv_brown_5","fx":params["fx"],"fy":params["fy"],"cx":params["cx"],"cy":params["cy"],"distortion":{k:params[k] for k in ("k1","k2","p1","p2","k3")}}},
      "board":BOARD,"source_pose_ids":[p for p,_ in primary],"source_scan_ids":[value["poses"][p]["scan_id"] for p,_ in primary],
      "selected_frames":[{"pose_id":p,"frame_id":f} for p,f in primary],"heldout_frames":[{"pose_id":p,"frame_id":f} for p,f in held],
      "selection_strategy":{"primary":"best-1 quality-ranked ChArUco frame per pose","heldout":"second strong frame per pose; never used in primary fit","top2":"sensitivity only; maximum two frames per pose","outliers":"one diagnostic pass at max(median+3*MAD,p95); candidate remains unfiltered primary"},
      "solver":{"opencv_version":__import__('cv2').__version__,"api":"cv2.aruco.CharucoDetector + CharucoBoard.matchImagePoints + cv2.calibrateCameraExtended","flags":0,"distortion_model":"Brown-Conrady k1,k2,p1,p2,k3","termination":{"max_iterations":100,"epsilon":1e-9},"corner_refinement":"CORNER_REFINE_SUBPIX"},
      "metrics":{"train_rms_px":primary_fit["rms"],"heldout_rms_px":held_metrics["rms_px"],"heldout_p95_px":held_metrics["p95_px"],"heldout_max_px":held_metrics["max_px"],"fit_frames":len(primary),"heldout_frames":len(held),"fx_fy_relative_difference":abs(params["fx"]-params["fy"])/((params["fx"]+params["fy"])/2),"principal_point_offset_px":[params["cx"]-(WIDTH-1)/2,params["cy"]-(HEIGHT-1)/2]},
      "outlier_diagnostics":{"rule_threshold_rms_px":threshold,"excluded_for_diagnostic_only":[{"pose_id":p,"frame_id":f} for (p,f) in primary if (p,f) not in filtered_sel],"before_rms_px":primary_fit["rms"],"after_rms_px":filtered_fit["rms"],"no_iterative_trimming":True},
      "strategy_stability":{"primary":params,"top2":top2_params,"small_outlier_filtered":filtered_params,"parameter_sensitivity":sensitivity},
      "per_fit_frame":primary_fit["per_views"],"heldout":{k:v for k,v in held_metrics.items() if k not in {"vectors","radii","errors"}},
      "provenance":{"workspace_manifest_sha256":manifest_sha,"tool_sha256":tool_sha,"tool_git":"not applicable; standalone workspace file hash is authoritative","old_stage7a_path":str(OLD_STAGE7A),"old_stage7a_sha256":old_sha},
    }
    candidate["candidate_sha256"]=canonical_sha(candidate)
    write_json(CANDIDATE_PATH,candidate); write_text(WORKSPACE/"candidate"/"candidate_file_sha256.txt",sha256_file(CANDIDATE_PATH)+"  "+CANDIDATE_PATH.name+"\n")
    write_json(WORKSPACE/"validation"/"calibration_diagnostics.json",{"primary":candidate["metrics"],"outliers":candidate["outlier_diagnostics"],"stability":candidate["strategy_stability"],"per_fit_frame":candidate["per_fit_frame"],"heldout":candidate["heldout"]})
    save_residual_outputs(held_metrics)
    print(f"candidate written: {CANDIDATE_PATH}")
    return 0


def candidate_intrinsics(candidate: dict[str,Any]):
    import numpy as np
    i=candidate["camera"]["intrinsics"]; d=i["distortion"]
    return np.array([[i["fx"],0,i["cx"]],[0,i["fy"],i["cy"]],[0,0,1]],np.float64),np.array([d["k1"],d["k2"],d["p1"],d["p2"],d["k3"]],np.float64)


def validate_candidate(candidate: dict[str,Any]) -> list[str]:
    errors=[]
    required=("schema_version","candidate_status","created_at","camera","board","source_pose_ids","source_scan_ids","selected_frames","heldout_frames","selection_strategy","solver","metrics","provenance","candidate_sha256")
    for k in required:
        if k not in candidate: errors.append(f"missing {k}")
    if candidate.get("schema_version")!="stage2a_camera_intrinsics_candidate.v1": errors.append("schema_version")
    if candidate.get("camera",{}).get("resolution")!={"width":WIDTH,"height":HEIGHT}: errors.append("resolution")
    board=candidate.get("board",{})
    for k,v in BOARD.items():
        if board.get(k)!=v: errors.append(f"board.{k}")
    check=dict(candidate); declared=check.pop("candidate_sha256",None)
    if declared!=canonical_sha(check): errors.append("candidate_sha256")
    return errors


def cmd_validate(args: argparse.Namespace) -> int:
    if not CANDIDATE_PATH.is_file(): raise Stage2AError("candidate does not exist; run calibrate after the minimum usable dataset is registered")
    candidate=load_json(CANDIDATE_PATH); errors=validate_candidate(candidate)
    result={"validated_at":now(),"candidate":str(CANDIDATE_PATH),"valid":not errors,"errors":errors,"file_sha256":sha256_file(CANDIDATE_PATH)}
    write_json(WORKSPACE/"validation"/"candidate_validation.json",result); print(json.dumps(result,indent=2)); return 0 if not errors else 2


def old_intrinsics():
    import numpy as np
    p=load_json(OLD_STAGE7A); i=p["camera"]["intrinsics"]; d=i["distortion"]
    params={"fx":i["fx"],"fy":i["fy"],"cx":i["cx"],"cy":i["cy"],**d}
    K=np.array([[i["fx"],0,i["cx"]],[0,i["fy"],i["cy"]],[0,0,1]],np.float64); dist=np.array([d["k1"],d["k2"],d["p1"],d["p2"],d["k3"]],np.float64)
    return p,params,K,dist


def cmd_compare(args: argparse.Namespace) -> int:
    import cv2
    import numpy as np
    if not CANDIDATE_PATH.is_file(): raise Stage2AError("candidate does not exist")
    cand=load_json(CANDIDATE_PATH); errors=validate_candidate(cand)
    if errors: raise Stage2AError("candidate invalid: "+", ".join(errors))
    old,op,oldK,oldD=old_intrinsics(); newK,newD=candidate_intrinsics(cand); ni={"fx":newK[0,0],"fy":newK[1,1],"cx":newK[0,2],"cy":newK[1,2],"k1":newD[0],"k2":newD[1],"p1":newD[2],"p2":newD[3],"k3":newD[4]}
    value=manifest(); held=[(p,value["poses"][p].get("heldout_frame")) for p in registered_pose_ids(value) if value["poses"][p].get("heldout_frame")]
    oldheld=heldout_metrics(oldK,oldD,held); newheld=heldout_metrics(newK,newD,held)
    rows=[]
    for key in ("fx","fy","cx","cy","k1","k2","p1","p2","k3"):
        rows.append({"parameter":key,"old":float(op[key]),"candidate":float(ni[key]),"absolute_delta":float(ni[key]-op[key]),"relative_delta":float((ni[key]-op[key])/op[key]) if op[key] else None})
    samples=np.array([[0,0],[WIDTH/2,0],[WIDTH-1,0],[0,HEIGHT/2],[WIDTH/2,HEIGHT/2],[WIDTH-1,HEIGHT/2],[0,HEIGHT-1],[WIDTH/2,HEIGHT-1],[WIDTH-1,HEIGHT-1]],np.float32).reshape(-1,1,2)
    oldr=cv2.undistortPoints(samples,oldK,oldD).reshape(-1,2); newr=cv2.undistortPoints(samples,newK,newD).reshape(-1,2)
    rayrows=[]
    for pt,a,b in zip(samples.reshape(-1,2),oldr,newr): rayrows.append({"x":float(pt[0]),"y":float(pt[1]),"old_ray_x":float(a[0]),"old_ray_y":float(a[1]),"new_ray_x":float(b[0]),"new_ray_y":float(b[1]),"ray_direction_change":float(np.linalg.norm(b-a))})
    out=WORKSPACE/"comparison"; out.mkdir(exist_ok=True)
    with (out/"old_vs_candidate_stage7a.csv").open("w",encoding="utf-8-sig",newline="") as s:
        w=csv.DictWriter(s,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
    with (out/"sample_ray_comparison.csv").open("w",encoding="utf-8-sig",newline="") as s:
        w=csv.DictWriter(s,fieldnames=rayrows[0].keys());w.writeheader();w.writerows(rayrows)
    md=["# Old Stage7A vs Stage2A candidate","",f"Generated: {now()}","",f"Old: `{OLD_STAGE7A}`",f"Old SHA256: `{sha256_file(OLD_STAGE7A)}`",f"Candidate: `{CANDIDATE_PATH}`","","|Parameter|Old|Candidate|Absolute delta|Relative delta|","|---|---:|---:|---:|---:|"]
    md += [f"|{r['parameter']}|{r['old']:.10g}|{r['candidate']:.10g}|{r['absolute_delta']:.10g}|{r['relative_delta']:.4%}|" for r in rows]
    md += ["",f"Principal-point pixel shift: **{math.hypot(ni['cx']-op['cx'],ni['cy']-op['cy']):.4f} px**.","",f"Old held-out RMS / p95: **{oldheld['rms_px']:.4f} / {oldheld['p95_px']:.4f} px**.",f"Candidate held-out RMS / p95: **{newheld['rms_px']:.4f} / {newheld['p95_px']:.4f} px**.","",f"Old formal train RMS / held-out RMS: {old['quality']['fit_rms_px']} / {old['quality']['heldout_rms_px']} px (different historical dataset).","",f"Resolution: old {old['camera']['resolution']['width']}x{old['camera']['resolution']['height']}; candidate {WIDTH}x{HEIGHT}.","Board: old provenance binds the same 7x5, 25/18 mm manifest; candidate records exact active geometry.","", "Candidate remains NOT FOR PROMOTION."]
    write_text(out/"old_vs_candidate_stage7a.md","\n".join(md)+"\n")
    print(f"comparison written: {out}")
    return 0


def cmd_run_all(args: argparse.Namespace) -> int:
    cmd_status(args); cmd_rank(args); cmd_calibrate(args); rc=cmd_validate(args)
    if rc: return rc
    return cmd_compare(args)


def cmd_synthetic(args: argparse.Namespace) -> int:
    import cv2
    import numpy as np
    ensure_layout(); rng=np.random.default_rng(20260817); _,board=cv_board(); obj=board.getChessboardCorners().astype(np.float32); ids=np.arange(len(obj),dtype=np.int32).reshape(-1,1)
    truthK=np.array([[620.,0,319.5],[0,615.,179.5],[0,0,1]],np.float64); truthD=np.array([-0.12,0.045,0.0015,-0.001,-0.012],np.float64)
    corners=[]; idsets=[]; object_points=[]; image_points=[]
    for idx in range(72):
        rv=np.deg2rad(rng.uniform([-34,-38,-14],[34,38,14])).reshape(3,1); tv=np.array([[rng.uniform(-.20,.20)],[rng.uniform(-.12,.12)],[rng.uniform(.32,.90)]])
        pts,_=cv2.projectPoints(obj,rv,tv,truthK,truthD); pts=pts+rng.normal(0,.01,pts.shape)
        mask=(pts[:,0,0]>4)&(pts[:,0,0]<WIDTH-5)&(pts[:,0,1]>4)&(pts[:,0,1]<HEIGHT-5)
        if int(mask.sum()) < 8: continue
        detected=pts[mask].astype(np.float32); detected_ids=ids[mask]
        corners.append(detected); idsets.append(detected_ids)
        matched_obj,matched_img=board.matchImagePoints(detected,detected_ids)
        object_points.append(matched_obj.astype(np.float32)); image_points.append(matched_img.astype(np.float32))
    criteria=(cv2.TERM_CRITERIA_EPS+cv2.TERM_CRITERIA_COUNT,100,1e-9)
    result=cv2.calibrateCameraExtended(object_points,image_points,(WIDTH,HEIGHT),None,None,flags=0,criteria=criteria)
    rms,K,D=result[:3]; D=D.reshape(-1); param_err={"fx":float(K[0,0]-truthK[0,0]),"fy":float(K[1,1]-truthK[1,1]),"cx":float(K[0,2]-truthK[0,2]),"cy":float(K[1,2]-truthK[1,2]),"k1":float(D[0]-truthD[0]),"k2":float(D[1]-truthD[1]),"p1":float(D[2]-truthD[2]),"p2":float(D[3]-truthD[3]),"k3":float(D[4]-truthD[4])}
    generated=board.generateImage((1400,1000),marginSize=40,borderBits=1); detector=cv2.aruco.CharucoDetector(board); cc,ci,mc,mi=detector.detectBoard(generated)
    board_ok=ci is not None and len(ci)>=20
    synthetic_candidate={"schema_version":"stage2a_camera_intrinsics_candidate.v1","candidate_status":"CANDIDATE_NOT_PROMOTED","created_at":now(),"camera":{"resolution":{"width":WIDTH,"height":HEIGHT},"source_raster_provenance":{},"intrinsics":{"model":"opencv_brown_5","fx":float(K[0,0]),"fy":float(K[1,1]),"cx":float(K[0,2]),"cy":float(K[1,2]),"distortion":{"k1":float(D[0]),"k2":float(D[1]),"p1":float(D[2]),"p2":float(D[3]),"k3":float(D[4])}}},"board":BOARD,"source_pose_ids":[],"source_scan_ids":[],"selected_frames":[],"heldout_frames":[],"selection_strategy":{},"solver":{},"metrics":{},"outlier_diagnostics":{},"strategy_stability":{},"per_fit_frame":[],"heldout":{},"provenance":{"synthetic":True}}
    synthetic_candidate["candidate_sha256"]=canonical_sha(synthetic_candidate)
    schema_errors=validate_candidate(synthetic_candidate)
    passed=bool(rms<0.05 and abs(param_err["fx"])<1 and abs(param_err["fy"])<1 and abs(param_err["cx"])<1 and abs(param_err["cy"])<1 and abs(param_err["k1"])<.01 and abs(param_err["k2"])<.03 and abs(param_err["k3"])<.05 and board_ok and not schema_errors)
    result_doc={"schema_version":"stage2a_synthetic_recovery.v1","generated_at":now(),"opencv_version":cv2.__version__,"api":"cv2.aruco.CharucoDetector + CharucoBoard.matchImagePoints + cv2.calibrateCameraExtended","views":len(corners),"noise_sigma_px":.01,"rms_px":float(rms),"truth_K":truthK.tolist(),"recovered_K":K.tolist(),"truth_distortion":truthD.tolist(),"recovered_distortion":D[:5].tolist(),"parameter_errors":param_err,"board_generation_detection":{"detected_charuco_corners":0 if ci is None else len(ci),"pass":board_ok},"synthetic_candidate_schema_validation":{"pass":not schema_errors,"errors":schema_errors},"verdict":"PASS" if passed else "FAIL"}
    write_json(WORKSPACE/"validation"/"synthetic_recovery.json",result_doc); cv2.imwrite(str(WORKSPACE/"validation"/"synthetic_charuco_board.png"),generated)
    print(json.dumps(result_doc,indent=2)); return 0 if passed else 2


def cmd_stability_diagnostics(args: argparse.Namespace) -> int:
    """Run read-only intrinsic stability fits and mapping/ray comparisons."""
    import stage2a_stability_diagnostics as stability
    stability.run_diagnostics(
        tool=sys.modules[__name__],
        output_dir=WORKSPACE / "stability_diagnostics",
        subsample_runs=args.subsample_runs,
        subsample_fraction=args.subsample_fraction,
        seed=args.seed,
        grid_step=args.grid_step,
    )
    return 0


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description="Stage 2A Camera intrinsic + distortion candidate toolchain (formal sources read-only)")
    p.add_argument("--version",action="version",version="stage2a-camera-intrinsic 1.1")
    sub=p.add_subparsers(dest="command",required=True)
    q=sub.add_parser("inspect",help="inspect the formal scan contract/root and optional scan, without writes outside workspace"); q.add_argument("--scan-id"); q.add_argument("--latest",action="store_true"); q.add_argument("--detect-charuco",action="store_true",help="decode/detect all 11 historical frames read-only and write diagnostics in workspace"); q.set_defaults(func=cmd_inspect)
    q=sub.add_parser("register",help="bind one formal 11-frame scan to a safe unique pose_id and hardlink/copy all raw artifacts"); q.add_argument("--pose",required=True); g=q.add_mutually_exclusive_group(required=True);g.add_argument("--scan-id");g.add_argument("--latest",action="store_true");q.add_argument("--replace",action="store_true",help="explicitly replace an existing pose_id binding; source remains untouched");q.add_argument("--dry-run",action="store_true",help="validate/hash only; do not import or change the pose manifest");q.set_defaults(func=cmd_register)
    q=sub.add_parser("status",help="show dynamic registered/selected/held-out/extra pose counts and states");q.set_defaults(func=cmd_status)
    q=sub.add_parser("rank",help="detect all 11 frames, rank them, and create contact sheets/coverage outputs");q.set_defaults(func=cmd_rank)
    q=sub.add_parser("calibrate",help="fit best-1/pose, top-2 sensitivity, outlier diagnostics, and held-out validation");q.add_argument("--allow-weak",action="store_true",help="permit some weak poses only when at least 12 fit and 8 held-out frames remain");q.set_defaults(func=cmd_calibrate)
    q=sub.add_parser("validate",help="validate candidate schema, exact board/raster contract, and content hash");q.set_defaults(func=cmd_validate)
    q=sub.add_parser("compare",help="compare old formal Stage7A and candidate parameters, held-out residuals, and rays");q.set_defaults(func=cmd_compare)
    q=sub.add_parser("run-all",help="rank, calibrate, validate, and compare all currently registered poses");q.add_argument("--allow-weak",action="store_true");q.set_defaults(func=cmd_run_all)
    q=sub.add_parser("synthetic-test",help="generate synthetic ChArUco observations and verify calibration recovery");q.set_defaults(func=cmd_synthetic)
    q=sub.add_parser("stability-diagnostics",help="read-only dynamic-N LOPO, mapping/ray, historical subsets, and deterministic subset diagnostics")
    q.add_argument("--subsample-runs",type=int,default=100,help="number of deterministic subset fits (default: 100)")
    q.add_argument("--subsample-fraction",type=float,default=0.8,help="retained pose fraction; subset size is ceil(N*fraction) (default: 0.8)")
    q.add_argument("--seed",type=int,default=20260817,help="deterministic subset seed (default: 20260817)")
    q.add_argument("--grid-step",type=int,default=16,help="mapping grid step in pixels, maximum 16 (default: 16)")
    q.set_defaults(func=cmd_stability_diagnostics)
    return p


def main(argv: list[str] | None=None) -> int:
    os.environ.setdefault("TMP",str(WORKSPACE/"tmp"));os.environ.setdefault("TEMP",str(WORKSPACE/"tmp"));os.environ.setdefault("MPLCONFIGDIR",str(WORKSPACE/"tmp"/"mpl"));os.environ.setdefault("PYTHONPYCACHEPREFIX",str(WORKSPACE/"pycache"))
    try:
        args = parser().parse_args(argv)
        return int(args.func(args))
    except Stage2AError as exc: print(f"ERROR: {exc}",file=sys.stderr); return 2
    except KeyboardInterrupt: print("ERROR: interrupted",file=sys.stderr); return 130


if __name__=="__main__": raise SystemExit(main())
