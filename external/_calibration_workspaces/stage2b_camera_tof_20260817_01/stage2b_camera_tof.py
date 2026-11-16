#!/usr/bin/env python3
"""Stage2B Camera<->ToF dynamic calibration candidate tool.

All writes are confined to this standalone workspace. Formal scans, Stage2A,
Stage7 and runtime assets are read-only inputs. There is deliberately no
promotion command.
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

import cv2
import numpy as np

WORKSPACE = Path(__file__).resolve().parent
SCAN_ROOT = Path(r"C:\ec\_deltar_scan_service_data\validated")
CAMERA_CANDIDATE = Path(r"C:\ec\_calibration_workspaces\stage2a_camera_intrinsic_20260817_01\candidate\camera_intrinsics.stage2a_candidate.v1.json")
CAMERA_CANDIDATE_SHA256 = "99e5fe45e56b9f9a5a05f26179a9ee124610c3d4524b4e257b88c7b149979f6b"
OLD_STAGE7B = Path(r"C:\calibration\stage7b_result_unitfix_20260724_012041\camera_tof_extrinsics.v1.json")
WIDTH, HEIGHT, PAYLOAD_BYTES = 640, 360, 460800
POSE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
STRICT_STATUSES = {5, 6, 9}
D4 = (
    "identity", "rotate_90_cw", "rotate_180", "rotate_270_cw",
    "flip_horizontal", "flip_vertical", "transpose_main_diagonal", "transpose_anti_diagonal",
)
PARAMETER_NAMES_UNITS = (
    ("rotation_rodrigues_x", "rad"), ("rotation_rodrigues_y", "rad"),
    ("rotation_rodrigues_z", "rad"), ("translation_x", "mm"),
    ("translation_y", "mm"), ("translation_z", "mm"),
    ("log_fx_tof", "ln(zone_cell)"), ("log_fy_tof", "ln(zone_cell)"),
    ("cx_tof", "zone_coordinate"), ("cy_tof", "zone_coordinate"),
)
BOARD_SPEC = {
    "squares_x": 7, "squares_y": 5, "dictionary": "DICT_5X5_100",
    "square_length_mm": 25.0, "marker_length_mm": 18.0,
    "active_width_mm": 175.0, "active_height_mm": 125.0,
}
MANIFEST_PATH = WORKSPACE / "workspace_manifest.json"
OBS_PATH = WORKSPACE / "observations" / "observations.stage2b.json"
SOLUTION_PATH = WORKSPACE / "solve" / "stage2b_solution.json"
CANDIDATE_PATH = WORKSPACE / "candidate" / "camera_tof.stage2b_candidate.v1.json"
FR_PATH = WORKSPACE / "candidate" / "deltar_fr.stage2b_candidate.v1.json"


class Stage2BError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise Stage2BError(f"cannot read strict JSON {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(raw, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(value, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_layout() -> None:
    for name in ("imports", "manifests", "observations", "coverage", "solve", "validation", "stability", "candidate", "plots", "logs", "tmp"):
        (WORKSPACE / name).mkdir(parents=True, exist_ok=True)
    if not MANIFEST_PATH.exists():
        if not CAMERA_CANDIDATE.is_file() or sha256_file(CAMERA_CANDIDATE) != CAMERA_CANDIDATE_SHA256:
            raise Stage2BError("Stage2A camera candidate is missing or its frozen SHA-256 does not match")
        write_json(MANIFEST_PATH, {
            "schema_version": "stage2b_workspace_manifest.v1", "created_at": now(),
            "workspace": str(WORKSPACE), "scan_root": str(SCAN_ROOT),
            "camera_intrinsics_ref": str(CAMERA_CANDIDATE),
            "camera_intrinsics_sha256": CAMERA_CANDIDATE_SHA256,
            "camera_raster": {"width": WIDTH, "height": HEIGHT, "pixel_format": "RGB565_LE", "stride_bytes": 1280, "payload_bytes": PAYLOAD_BYTES},
            "board": BOARD_SPEC, "poses": {},
        })


def manifest() -> dict[str, Any]:
    ensure_layout()
    value = load_json(MANIFEST_PATH)
    if value.get("schema_version") != "stage2b_workspace_manifest.v1" or not isinstance(value.get("poses"), dict):
        raise Stage2BError("unsupported or malformed Stage2B workspace manifest")
    return value


def save_manifest(value: dict[str, Any]) -> None:
    value["updated_at"] = now()
    write_json(MANIFEST_PATH, value)


def pose_id(value: str) -> str:
    if not POSE_RE.fullmatch(value):
        raise Stage2BError("invalid pose_id; expected [A-Za-z0-9][A-Za-z0-9_-]*")
    return value


def d4_map(name: str) -> np.ndarray:
    grid = np.arange(64, dtype=np.int64).reshape(8, 8)
    operations = {
        "identity": grid, "rotate_90_cw": np.rot90(grid, -1), "rotate_180": np.rot90(grid, 2),
        "rotate_270_cw": np.rot90(grid, 1), "flip_horizontal": np.fliplr(grid),
        "flip_vertical": np.flipud(grid), "transpose_main_diagonal": grid.T,
        "transpose_anti_diagonal": np.rot90(grid.T, 2),
    }
    if name not in operations:
        raise Stage2BError(f"unsupported D4 transform: {name}")
    return operations[name].reshape(-1).copy()


def resolve_scan(scan_id: str | None, latest: bool) -> Path:
    if bool(scan_id) == bool(latest):
        raise Stage2BError("choose exactly one of --scan-id or --latest")
    if latest:
        scans = sorted(p for p in SCAN_ROOT.glob("scan_*") if p.is_dir() and (p / "scan.json").is_file())
        if not scans:
            raise Stage2BError(f"no formal scans under {SCAN_ROOT}")
        return scans[-1]
    if scan_id is None or not re.fullmatch(r"scan_[A-Za-z0-9_-]+", scan_id):
        raise Stage2BError("invalid scan_id")
    result = SCAN_ROOT / scan_id
    if not result.is_dir():
        raise Stage2BError(f"formal scan does not exist: {result}")
    return result


def validate_scan(path: Path) -> list[dict[str, Any]]:
    scan = load_json(path / "scan.json")
    errors: list[str] = []
    if scan.get("schema_version") != "deltar_scan_input.v1": errors.append("scan schema")
    if scan.get("scan_id") != path.name: errors.append("scan_id")
    if scan.get("frame_count") != 11: errors.append(f"frame_count={scan.get('frame_count')}, expected 11")
    rows: list[dict[str, Any]] = []
    for sequence in range(11):
        frame_id = f"frame_{sequence:06d}"
        folder = path / "frames" / frame_id
        fp, ip, tp = folder / "frame.json", folder / "image.rgb565", folder / "tof.json"
        if not all(p.is_file() for p in (fp, ip, tp)):
            errors.append(f"{frame_id}: required files"); continue
        frame, tof = load_json(fp), load_json(tp)
        image = frame.get("image", {})
        if frame.get("frame_id") != frame_id or frame.get("sequence") != sequence: errors.append(f"{frame_id}: sequence")
        if image != {"filename":"image.rgb565","width":640,"height":360,"pixel_format":"RGB565_LE","stride_bytes":1280,"payload_bytes":460800}: errors.append(f"{frame_id}: raster contract")
        if ip.stat().st_size != PAYLOAD_BYTES: errors.append(f"{frame_id}: payload bytes")
        cam, tof_ts, delta = frame.get("camera_timestamp_us"), frame.get("tof_timestamp_us"), frame.get("pair_delta_us")
        if not all(type(v) is int for v in (cam, tof_ts, delta)) or delta != abs(cam - tof_ts): errors.append(f"{frame_id}: timestamp/pair_delta")
        if tof.get("schema_version") != "rehab_tof_raw.v1" or tof.get("sequence") != sequence or tof.get("timestamp_us") != tof_ts: errors.append(f"{frame_id}: ToF header")
        for key in ("distance_mm", "range_sigma_mm", "target_status", "nb_target_detected"):
            if not isinstance(tof.get(key), list) or len(tof[key]) != 64: errors.append(f"{frame_id}: ToF {key}")
        rows.append({"frame_id":frame_id, "sequence":sequence, "folder":folder, "frame":frame, "tof":tof})
    if errors:
        raise Stage2BError("invalid formal 11-frame scan: " + "; ".join(errors))
    return rows


def source_files(path: Path) -> list[Path]:
    files = [path / "scan.json"]
    for sequence in range(11):
        folder = path / "frames" / f"frame_{sequence:06d}"
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
    value = manifest(); pid = pose_id(args.pose)
    if pid in value["poses"]:
        raise Stage2BError(f"pose_id already registered: {pid}; no silent replacement is allowed")
    source = resolve_scan(args.scan_id, args.latest)
    validate_scan(source)
    before = {str(p.relative_to(source)): sha256_file(p) for p in source_files(source)}
    if args.dry_run:
        print(f"DRY-RUN valid pose={pid} scan_id={source.name} frames=11 files={len(before)} source_unchanged=true")
        return 0
    destination = WORKSPACE / "imports" / pid / source.name
    if destination.exists():
        raise Stage2BError(f"import destination already exists: {destination}")
    modes: set[str] = set()
    for file in source_files(source):
        modes.add(link_or_copy(file, destination / file.relative_to(source)))
    after = {str(p.relative_to(source)): sha256_file(p) for p in source_files(source)}
    if before != after:
        raise Stage2BError("source hash changed during registration")
    imported = {str((destination / rel).relative_to(WORKSPACE)): sha256_file(destination / rel) for rel in before}
    if imported != {str((destination / rel).relative_to(WORKSPACE)): digest for rel, digest in before.items()}:
        raise Stage2BError("import hash mismatch")
    value["poses"][pid] = {
        "scan_id": source.name, "source_path": str(source.resolve()), "import_path": str(destination.resolve()),
        "registered_at": now(), "frame_count": 11, "pair_count": 11,
        "link_mode": sorted(modes), "source_hashes": before,
        "physical_position_note": args.physical_position_note, "capture_note": args.capture_note,
        "analysis_state": "NOT_INSPECTED",
    }
    save_manifest(value)
    write_json(WORKSPACE / "manifests" / f"{pid}.json", {"pose_id":pid, **value["poses"][pid]})
    print(f"registered pose={pid} scan_id={source.name} frames=11 mode={','.join(sorted(modes))} source_unchanged=true")
    return 0


def rgb565(path: Path) -> np.ndarray:
    raw = np.fromfile(path, dtype="<u2")
    if raw.size != WIDTH * HEIGHT:
        raise Stage2BError(f"RGB565 payload size mismatch: {path}")
    red = ((raw >> 11) & 31).astype(np.uint8); green = ((raw >> 5) & 63).astype(np.uint8); blue = (raw & 31).astype(np.uint8)
    red = (red << 3) | (red >> 2); green = (green << 2) | (green >> 4); blue = (blue << 3) | (blue >> 2)
    return np.stack((red, green, blue), axis=-1).reshape(HEIGHT, WIDTH, 3)


def camera_parameters() -> tuple[np.ndarray, np.ndarray]:
    candidate = load_json(CAMERA_CANDIDATE)
    intr = candidate["camera"]["intrinsics"]
    k = np.array([[intr["fx"],0,intr["cx"]],[0,intr["fy"],intr["cy"]],[0,0,1]], dtype=np.float64)
    dist = intr["distortion"]
    d = np.array([dist[x] for x in ("k1","k2","p1","p2","k3")], dtype=np.float64)
    return k, d


def charuco_board() -> cv2.aruco.CharucoBoard:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
    return cv2.aruco.CharucoBoard((7,5), 25.0, 18.0, dictionary)


def detect_plane(image_rgb: np.ndarray, frame_label: str) -> dict[str, Any]:
    board = charuco_board(); dictionary = board.getDictionary()
    params = cv2.aruco.DetectorParameters(); params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.CharucoDetector(board, cv2.aruco.CharucoParameters(), params)
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    charuco_corners, charuco_ids, marker_corners, marker_ids = detector.detectBoard(gray)
    corner_count = 0 if charuco_ids is None else int(len(charuco_ids)); marker_count = 0 if marker_ids is None else int(len(marker_ids))
    result: dict[str, Any] = {"frame_label":frame_label, "detected":False, "corner_count":corner_count, "marker_count":marker_count}
    if charuco_ids is None or corner_count < 6:
        result["reason"] = "fewer_than_6_charuco_corners"; return result
    obj, img = board.matchImagePoints(charuco_corners, charuco_ids)
    k, d = camera_parameters()
    ok, rvec, tvec = cv2.solvePnP(obj, img, k, d, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        result["reason"] = "solvePnP_failed"; return result
    projected, _ = cv2.projectPoints(obj, rvec, tvec, k, d)
    errors = np.linalg.norm(projected.reshape(-1,2) - img.reshape(-1,2), axis=1)
    rotation, _ = cv2.Rodrigues(rvec); normal = rotation[:,2]; point = tvec.reshape(3)
    if float(normal @ point) > 0: normal = -normal
    offset = -float(normal @ point)
    hull = cv2.convexHull(img.astype(np.float32)); coverage = float(cv2.contourArea(hull) / (WIDTH * HEIGHT))
    center = np.mean(img.reshape(-1,2), axis=0)
    result.update({
        "detected":True, "pnp_rms_px":float(np.sqrt(np.mean(errors**2))), "pnp_p95_px":float(np.percentile(errors,95)),
        "board_coverage_fraction":coverage, "board_center_px":center.tolist(),
        "rvec":rvec.reshape(3).tolist(), "tvec_mm":point.tolist(),
        "plane_normal_camera":normal.tolist(), "plane_offset_camera_mm":offset,
        "plane_distance_camera_mm":abs(offset),
    })
    return result


def frame_tof_rows(tof: dict[str, Any]) -> list[dict[str, Any]]:
    rows=[]
    for index in range(64):
        distance=float(tof["distance_mm"][index]); status=int(tof["target_status"][index]); count=int(tof["nb_target_detected"][index])
        broad = count != 0 and status != 255 and distance > 0
        strict = count != 0 and status in STRICT_STATUSES and distance > 0
        rows.append({
            "raw_index":index, "raw_row":index//8, "raw_col":index%8,
            "distance_mm":distance, "range_sigma_mm":float(tof["range_sigma_mm"][index]),
            "target_status":status, "target_count":count,
            "broad_valid":broad, "strict_valid":strict,
            "plane_consistency_residual_mm":None, "likely_reference_plane":None, "likely_outside_or_background":None,
        })
    return rows


def inspect_all(*, force: bool = False) -> dict[str, Any]:
    value = manifest(); poses: list[dict[str, Any]] = []
    for pid, item in value["poses"].items():
        scan_path = Path(item["import_path"]); frames=[]
        normals=[]; offsets=[]
        for row in validate_scan(scan_path):
            image = rgb565(row["folder"] / "image.rgb565")
            plane = detect_plane(image, f"{pid}/{row['frame_id']}")
            if plane["detected"]:
                normals.append(np.asarray(plane["plane_normal_camera"],float)); offsets.append(float(plane["plane_offset_camera_mm"]))
            f = row["frame"]
            frames.append({
                "frame_id":row["frame_id"], "camera_timestamp_us":f["camera_timestamp_us"], "tof_timestamp_us":f["tof_timestamp_us"],
                "pair_delta_us":f["pair_delta_us"], "camera_plane":plane, "tof_zones":frame_tof_rows(row["tof"]),
            })
        stability={"usable_plane_frames":len(normals),"normal_max_deviation_deg":None,"offset_range_mm":None,"stable":False}
        if normals:
            ref=np.mean(normals,axis=0); ref/=np.linalg.norm(ref)
            angles=np.degrees(np.arccos(np.clip(np.abs(np.asarray(normals)@ref),-1,1)))
            stability.update({"normal_max_deviation_deg":float(np.max(angles)),"normal_rms_deviation_deg":float(np.sqrt(np.mean(angles**2))),"offset_range_mm":float(np.ptp(offsets)),"offset_std_mm":float(np.std(offsets)),"stable":bool(np.max(angles)<=1.0 and np.ptp(offsets)<=15.0)})
        poses.append({"pose_id":pid,"scan_id":item["scan_id"],"frames":frames,"within_scan_plane_stability":stability})
        item["analysis_state"] = "INSPECTED" if normals else "NO_CAMERA_PLANE"
        item["usable_plane_frames"] = len(normals); item["within_scan_plane_stable"] = stability["stable"]
    output={"schema_version":"stage2b_observations.v1","generated_at":now(),"pose_count":len(poses),"poses":poses}
    write_json(OBS_PATH,output); save_manifest(value)
    return output


def get_observations(refresh: bool = False) -> dict[str, Any]:
    if refresh or not OBS_PATH.is_file(): return inspect_all(force=refresh)
    return load_json(OBS_PATH)


def plane_summary(frame: dict[str, Any]) -> tuple[np.ndarray,float] | None:
    plane=frame["camera_plane"]
    if not plane.get("detected"): return None
    return np.asarray(plane["plane_normal_camera"],float),float(plane["plane_offset_camera_mm"])


def observation_rows(obs: dict[str, Any], policy: str = "broad", pose_filter: set[str] | None = None) -> list[dict[str, Any]]:
    rows=[]
    for pose in obs["poses"]:
        if pose_filter is not None and pose["pose_id"] not in pose_filter: continue
        detected=[f for f in pose["frames"] if f["camera_plane"].get("detected")]
        if not detected: continue
        per_frame_weight=1.0/math.sqrt(len(detected))
        for frame in detected:
            normal,offset=plane_summary(frame)  # type: ignore[misc]
            for zone in frame["tof_zones"]:
                if zone[f"{policy}_valid"]:
                    rows.append({"pose_id":pose["pose_id"],"scan_id":pose["scan_id"],"frame_id":frame["frame_id"],"raw_index":zone["raw_index"],"distance_mm":zone["distance_mm"],"sigma_mm":zone["range_sigma_mm"],"normal":normal,"offset":offset,"base_weight":per_frame_weight})
    return rows


def tof_rays(params: np.ndarray) -> np.ndarray:
    fx,fy=math.exp(float(params[6])),math.exp(float(params[7])); cx,cy=float(params[8]),float(params[9])
    c,r=np.meshgrid(np.arange(8,dtype=float)+0.5,np.arange(8,dtype=float)+0.5)
    rays=np.stack(((c-cx)/fx,(r-cy)/fy,np.ones_like(c)),axis=-1).reshape(64,3)
    rays/=np.linalg.norm(rays,axis=1,keepdims=True)
    return rays


def default_params() -> np.ndarray:
    focal=8.0/(2.0*math.tan(math.radians(45.0)/2.0))
    return np.array([0,0,0,-20,0,0,math.log(focal),math.log(focal),4,4],dtype=float)


def residual_vector(params: np.ndarray, rows: list[dict[str, Any]], orientation: str, semantics: str, model: str, include_regularization: bool = True) -> np.ndarray:
    rotation,_=cv2.Rodrigues(np.asarray(params[:3],float)); translation=np.asarray(params[3:6],float)
    p=default_params() if model=="A_nominal" else params
    rays=tof_rays(p); mapping=d4_map(orientation); inverse=np.empty(64,dtype=np.int64); inverse[mapping]=np.arange(64)
    raw_indices=np.fromiter((int(row["raw_index"]) for row in rows),dtype=np.int64,count=len(rows))
    distances=np.fromiter((float(row["distance_mm"]) for row in rows),dtype=np.float64,count=len(rows))
    normals=np.asarray([row["normal"] for row in rows],dtype=np.float64)
    offsets=np.fromiter((float(row["offset"]) for row in rows),dtype=np.float64,count=len(rows))
    base_weights=np.fromiter((float(row["base_weight"]) for row in rows),dtype=np.float64,count=len(rows))
    selected=rays[inverse[raw_indices]]
    scale=distances if semantics=="radial_range" else distances/selected[:,2]
    points=selected*scale[:,None]
    transformed=points@rotation.T+translation
    residual=(np.sum(normals*transformed,axis=1)+offsets)*base_weights
    residual=residual.tolist()
    if include_regularization:
        # Weak physical gauge prior: cameras are expected approximately co-oriented;
        # reported separately and never treated as measured truth.
        residual.extend((np.asarray(params[:3])*math.sqrt(0.08)*20.0).tolist())
        residual.extend((np.asarray(params[3:6])-np.array([-20,0,0])) .__mul__(math.sqrt(0.0004)).tolist())
        if model!="A_nominal":
            base=default_params(); scales=np.array([0.03,0.03,0.08,0.08])
            residual.extend(((params[6:10]-base[6:10])*scales).tolist())
    return np.asarray(residual,float)


def robust_lm(rows: list[dict[str, Any]], orientation: str, semantics: str, model: str, initial: np.ndarray | None = None, max_iterations: int = 35) -> dict[str, Any]:
    if len(rows)<18: raise Stage2BError("too few usable zone observations for solve")
    p=default_params() if initial is None else np.asarray(initial,float).copy()
    active=6 if model=="A_nominal" else 10; damping=1e-3; converged=False
    for iteration in range(max_iterations):
        raw=residual_vector(p,rows,orientation,semantics,model); data=raw[:len(rows)]
        med=float(np.median(data)); scale=max(1.4826*float(np.median(np.abs(data-med))),1.0); cutoff=1.345*scale
        w=np.ones_like(raw); large=np.abs(data)>cutoff; w[:len(rows)][large]=cutoff/np.abs(data[large])
        steps=np.array([2e-5,2e-5,2e-5,0.03,0.03,0.03,2e-4,2e-4,2e-3,2e-3])[:active]
        jac=np.empty((len(raw),active),float)
        for j,step in enumerate(steps):
            trial=p.copy(); trial[j]+=step
            jac[:,j]=(residual_vector(trial,rows,orientation,semantics,model)-raw)/step
        sw=np.sqrt(w); jw=jac*sw[:,None]; rw=raw*sw
        h=jw.T@jw+damping*np.diag(np.maximum(np.diag(jw.T@jw),1.0)); g=jw.T@rw
        try: delta=-np.linalg.solve(h,g)
        except np.linalg.LinAlgError: delta=-np.linalg.lstsq(h,g,rcond=None)[0]
        accepted=False; old=float(np.sum(np.where(np.abs(raw)<=cutoff,0.5*raw**2,cutoff*(np.abs(raw)-0.5*cutoff))))
        for _ in range(10):
            trial=p.copy(); trial[:active]+=delta
            trial[6:8]=np.clip(trial[6:8],math.log(3.0),math.log(30.0)); trial[8:10]=np.clip(trial[8:10],1.0,7.0)
            new=residual_vector(trial,rows,orientation,semantics,model); cost=float(np.sum(np.where(np.abs(new)<=cutoff,0.5*new**2,cutoff*(np.abs(new)-0.5*cutoff))))
            if cost<=old: p=trial; damping=max(damping/3,1e-9); accepted=True; break
            delta*=0.5; damping*=5
        if not accepted: break
        if np.linalg.norm(delta)<1e-6: converged=True; break
    data=residual_vector(p,rows,orientation,semantics,model,include_regularization=False)
    # Unweighted physical residual for metrics.
    physical=[]
    for row,value in zip(rows,data): physical.append(value/max(float(row["base_weight"]),1e-12))
    physical=np.asarray(physical)
    singular=np.linalg.svd(jac[:len(rows)],compute_uv=False); rank=int(np.sum(singular>singular[0]*1e-10)); condition=float(singular[0]/max(singular[-1],1e-15))
    return {"params":p.tolist(),"converged":converged or iteration>1,"iterations":iteration+1,"rank":rank,"condition_number":condition,"residuals":physical,"metrics":metrics(physical)}


def metrics(values: Iterable[float]) -> dict[str, Any]:
    a=np.abs(np.asarray(list(values),float))
    if not len(a): return {"count":0,"rms_mm":None,"median_mm":None,"p95_mm":None,"max_mm":None}
    return {"count":int(len(a)),"rms_mm":float(np.sqrt(np.mean(a*a))),"median_mm":float(np.median(a)),"p95_mm":float(np.percentile(a,95)),"max_mm":float(np.max(a))}


def unit_metrics(values: Iterable[float], unit: str) -> dict[str, Any]:
    """Return truthful display units and keep old aliases explicitly deprecated."""
    base=metrics(values); result={"count":base["count"]}
    for key in ("rms","median","p95","max"): result[f"{key}_{unit}"]=base[f"{key}_mm"]
    result["deprecated_mm_aliases"]={key:base[key] for key in ("rms_mm","median_mm","p95_mm","max_mm")}
    return result


def split_poses(obs: dict[str, Any]) -> tuple[set[str],set[str]]:
    ids=[p["pose_id"] for p in obs["poses"] if any(f["camera_plane"].get("detected") for f in p["frames"])]
    held={pid for pid in ids if int(hashlib.sha256(pid.encode()).hexdigest()[:8],16)%5==0}
    if len(ids)>=4 and not held: held={ids[-1]}
    if len(held)>=len(ids): held={ids[-1]}
    return set(ids)-held,held


def evaluate_params(params: list[float], rows: list[dict[str, Any]], orientation: str, semantics: str, model: str) -> tuple[np.ndarray,dict[str,Any]]:
    weighted=residual_vector(np.asarray(params,float),rows,orientation,semantics,model,include_regularization=False)
    physical=np.asarray([v/max(float(row["base_weight"]),1e-12) for v,row in zip(weighted,rows)])
    return physical,metrics(physical)


def residual_with_rays(params: list[float], rows: list[dict[str,Any]], orientation: str, semantics: str, rays: np.ndarray) -> np.ndarray:
    rotation,translation=params_to_transform(params); mapping=d4_map(orientation); inverse=np.empty(64,dtype=np.int64); inverse[mapping]=np.arange(64)
    raw=np.fromiter((int(x["raw_index"]) for x in rows),dtype=np.int64,count=len(rows)); distance=np.fromiter((float(x["distance_mm"]) for x in rows),dtype=float,count=len(rows))
    normals=np.asarray([x["normal"] for x in rows],float); offsets=np.fromiter((float(x["offset"]) for x in rows),dtype=float,count=len(rows))
    selected=np.asarray(rays,float)[inverse[raw]]; scale=distance if semantics=="radial_range" else distance/selected[:,2]
    return np.sum(normals*(selected*scale[:,None]@rotation.T+translation),axis=1)+offsets


def fit_model_c_refinement(params: list[float], rows: list[dict[str,Any]], orientation: str, semantics: str) -> dict[str,Any]:
    """Fit small regularized per-zone tangent-ray corrections around Model B."""
    base=tof_rays(np.asarray(params,float)); initial=residual_with_rays(params,rows,orientation,semantics,base); mapping=d4_map(orientation); inverse=np.empty(64,dtype=np.int64); inverse[mapping]=np.arange(64)
    canonical=inverse[np.fromiter((int(x["raw_index"]) for x in rows),dtype=np.int64,count=len(rows))]
    jac=np.zeros((len(rows),128),float); step=1e-4
    for zone in range(64):
        selected=np.where(canonical==zone)[0]
        if not len(selected): continue
        for axis in range(2):
            modified=base.copy(); modified[zone,axis]+=step; modified[zone]/=np.linalg.norm(modified[zone])
            changed=residual_with_rays(params,[rows[i] for i in selected],orientation,semantics,modified)
            jac[selected,zone*2+axis]=(changed-initial[selected])/step
    # Approximate scan-equal weights are already encoded by balanced repeated observations;
    # a strong angular prior and 4-neighbour smoothness prevent 64 free rays overfitting.
    prior=2.5e6; smooth=7.5e5; h=jac.T@jac+prior*np.eye(128); g=jac.T@initial
    for row in range(8):
        for col in range(8):
            zone=row*8+col
            for other in ((row+1)*8+col if row<7 else None,row*8+col+1 if col<7 else None):
                if other is None: continue
                for axis in range(2):
                    a=zone*2+axis; b=other*2+axis; h[a,a]+=smooth; h[b,b]+=smooth; h[a,b]-=smooth; h[b,a]-=smooth
    delta=-np.linalg.solve(h,g).reshape(64,2); refined=base.copy(); refined[:,:2]+=delta; refined/=np.linalg.norm(refined,axis=1,keepdims=True)
    angular=np.degrees(np.arccos(np.clip(np.sum(base*refined,axis=1),-1,1)))
    return {"corrections_xy":delta.tolist(),"zone_center_rays":refined.tolist(),"train":metrics(residual_with_rays(params,rows,orientation,semantics,refined)),"angular_correction_deg":metrics(angular),"regularization":{"ridge":prior,"four_neighbour_smoothness":smooth}}


def apply_plane_residual_diagnostics(obs: dict[str,Any], selected: dict[str,Any]) -> dict[str,Any]:
    params=selected["params"]; rays=tof_rays(np.asarray(params,float)) if selected["model"]!="A_nominal" else tof_rays(default_params()); orientation=selected["orientation"]; semantics=selected["range_semantics"]
    all_values=[]
    for pose in obs["poses"]:
        for frame in pose["frames"]:
            plane=plane_summary(frame)
            if plane is None: continue
            rows=[]; zones=[]
            for zone in frame["tof_zones"]:
                if zone["broad_valid"]:
                    rows.append({"raw_index":zone["raw_index"],"distance_mm":zone["distance_mm"],"normal":plane[0],"offset":plane[1]}); zones.append(zone)
            residuals=residual_with_rays(params,rows,orientation,semantics,rays) if rows else np.asarray([])
            for zone,residual in zip(zones,residuals): zone["plane_consistency_residual_mm"]=float(residual); all_values.append(abs(float(residual)))
    threshold=max(10.0,float(np.median(all_values)+4.5*1.4826*np.median(np.abs(np.asarray(all_values)-np.median(all_values))))) if all_values else 10.0
    for pose in obs["poses"]:
        for frame in pose["frames"]:
            for zone in frame["tof_zones"]:
                residual=zone["plane_consistency_residual_mm"]
                if residual is not None:
                    zone["likely_reference_plane"]=bool(abs(residual)<=threshold); zone["likely_outside_or_background"]=bool(abs(residual)>threshold)
    obs["plane_consistency_rule"]={"threshold_mm":threshold,"method":"median + 4.5 * 1.4826 MAD, floor 10 mm","zone_frame_robust_not_whole_scan_rejection":True}
    write_json(OBS_PATH,obs); return obs


def gauge_prior_penalty_mm(params: list[float]) -> float:
    """Weak ranking-only co-orientation prior; never part of data residuals."""
    return 0.02 * math.degrees(float(np.linalg.norm(np.asarray(params[:3],float))))


def candidate_grid(obs: dict[str, Any], policy: str="broad", quick: bool=False) -> list[dict[str, Any]]:
    train_ids,held_ids=split_poses(obs); train=observation_rows(obs,policy,train_ids); held=observation_rows(obs,policy,held_ids)
    if not held: held=train
    results=[]
    for orientation in D4:
        for semantics in ("radial_range","axial_z"):
            for model in ("A_nominal","B_low_dimensional"):
                fit=robust_lm(train,orientation,semantics,model,max_iterations=18 if quick else 35)
                held_res,held_metrics=evaluate_params(fit["params"],held,orientation,semantics,model)
                data_score=(held_metrics["rms_mm"] or 1e9)+0.15*(fit["metrics"]["rms_mm"] or 1e9)
                prior_penalty=gauge_prior_penalty_mm(fit["params"])
                score=data_score+prior_penalty
                results.append({"orientation":orientation,"range_semantics":semantics,"model":model,"score":score,"data_only_score":data_score,"gauge_prior_penalty_mm":prior_penalty,"train":fit["metrics"],"heldout":held_metrics,"params":fit["params"],"rank":fit["rank"],"condition_number":fit["condition_number"]})
    return sorted(results,key=lambda x:x["score"])


def group_metrics(rows: list[dict[str,Any]], residuals: np.ndarray, key_fn) -> dict[str,Any]:
    groups: dict[str,list[float]]={}
    for row,residual in zip(rows,residuals): groups.setdefault(str(key_fn(row)),[]).append(float(residual))
    return {key:metrics(values) for key,values in sorted(groups.items())}


def enrich_selected(selected: dict[str,Any], obs: dict[str,Any], policy: str="broad") -> dict[str,Any]:
    rows=observation_rows(obs,policy); residuals,_=evaluate_params(selected["params"],rows,selected["orientation"],selected["range_semantics"],selected["model"])
    normals={}
    for pose in obs["poses"]:
        planes=[plane_summary(f) for f in pose["frames"] if plane_summary(f) is not None]
        if planes:
            n=np.mean([p[0] for p in planes],axis=0); n/=np.linalg.norm(n); normals[pose["pose_id"]]=n
    def dist_band(row):
        d=abs(float(row["offset"])); return "near" if d<550 else ("mid" if d<850 else "far")
    def angle_band(row):
        n=np.asarray(row["normal"]); tilt=math.degrees(math.acos(np.clip(abs(n[2]),-1,1))); return "low_0_10" if tilt<10 else ("medium_10_25" if tilt<25 else "high_25_plus")
    selected["metrics_detail"]={"all":metrics(residuals),"per_zone":group_metrics(rows,residuals,lambda r:int(np.where(d4_map(selected["orientation"])==r["raw_index"])[0][0])),"per_distance":group_metrics(rows,residuals,dist_band),"per_angle":group_metrics(rows,residuals,angle_band),"per_scan":group_metrics(rows,residuals,lambda r:r["pose_id"])}
    selected["row_count"]=len(rows)
    return selected


def cmd_inspect(args: argparse.Namespace) -> int:
    if args.scan_id or args.latest:
        path=resolve_scan(args.scan_id,args.latest); rows=validate_scan(path)
        print(json.dumps({"scan_id":path.name,"frame_count":len(rows),"contract":"valid","read_only":True},indent=2))
        return 0
    obs=inspect_all(force=True)
    for pose in obs["poses"]:
        s=pose["within_scan_plane_stability"]
        print(f"{pose['pose_id']}: camera_plane_frames={s['usable_plane_frames']}/11 stable={str(s['stable']).lower()} normal_max_deg={s['normal_max_deviation_deg']} offset_range_mm={s['offset_range_mm']}")
    print(f"wrote {OBS_PATH}")
    return 0


def coverage_document(obs: dict[str,Any]) -> dict[str,Any]:
    zone={str(i):{"total_observations":0,"broad_count":0,"strict_count":0,"plane_consistent_observations":None,"distance_values_mm":[],"angle_values_deg":[]} for i in range(64)}
    distances=[]; yaws=[]; pitches=[]; tilts=[]; pair=[]; usable_scans=0
    for pose in obs["poses"]:
        detected=[f for f in pose["frames"] if f["camera_plane"].get("detected")]
        if detected: usable_scans+=1
        for frame in pose["frames"]:
            pair.append(frame["pair_delta_us"]); plane=frame["camera_plane"]
            if plane.get("detected"):
                n=np.asarray(plane["plane_normal_camera"],float); d=plane["plane_distance_camera_mm"]
                yaw=math.degrees(math.atan2(n[0],abs(n[2]))); pitch=math.degrees(math.atan2(n[1],abs(n[2]))); tilt=math.degrees(math.acos(np.clip(abs(n[2]),-1,1)))
                distances.append(d); yaws.append(yaw); pitches.append(pitch); tilts.append(tilt)
            for z in frame["tof_zones"]:
                item=zone[str(z["raw_index"])]; item["total_observations"]+=1; item["broad_count"]+=int(z["broad_valid"]); item["strict_count"]+=int(z["strict_valid"])
                if z["broad_valid"]: item["distance_values_mm"].append(z["distance_mm"])
                if plane.get("detected"): item["angle_values_deg"].append(tilt)
    for item in zone.values():
        dv=item.pop("distance_values_mm"); av=item.pop("angle_values_deg")
        item["distance_spread_mm"]=float(np.ptp(dv)) if dv else None; item["angle_spread_deg"]=float(np.ptp(av)) if av else None
    return {"schema_version":"stage2b_coverage.v1","generated_at":now(),"registered_scans":len(obs["poses"]),"usable_scans":usable_scans,"frame_pair_count":sum(len(p["frames"]) for p in obs["poses"]),"distance_mm":metrics(distances),"distance_histogram":{"near_lt_550":sum(x<550 for x in distances),"mid_550_850":sum(550<=x<850 for x in distances),"far_ge_850":sum(x>=850 for x in distances)},"plane_angle_deg":{"yaw":unit_metrics(yaws,"deg"),"pitch":unit_metrics(pitches,"deg"),"combined_tilt":unit_metrics(tilts,"deg")},"pair_delta_us":unit_metrics(pair,"us"),"zones_raw":zone,"orientation_observability":{"status":"PENDING_SOLVE" if not SOLUTION_PATH.is_file() else load_json(SOLUTION_PATH)["orientation_decision"]},"notes":["distance/angle bins derive from measured camera-plane geometry, never pose_id text","plane-consistent zone counts become available after solve","deprecated_mm_aliases retain prior values only for compatibility"]}


def save_heatmaps(coverage: dict[str,Any]) -> None:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fields=("broad_count","strict_count","distance_spread_mm","angle_spread_deg")
    fig,axes=plt.subplots(1,4,figsize=(15,3.6))
    for ax,field in zip(axes,fields):
        data=np.array([coverage["zones_raw"][str(i)][field] if coverage["zones_raw"][str(i)][field] is not None else np.nan for i in range(64)]).reshape(8,8)
        im=ax.imshow(data,cmap="viridis"); ax.set_title(field); ax.set_xlabel("raw col"); ax.set_ylabel("raw row"); fig.colorbar(im,ax=ax,shrink=.7)
    fig.tight_layout(); fig.savefig(WORKSPACE/"coverage"/"zone_coverage_heatmaps.png",dpi=150); plt.close(fig)


def cmd_coverage(args: argparse.Namespace) -> int:
    obs=get_observations(refresh=args.refresh); doc=coverage_document(obs); write_json(WORKSPACE/"coverage"/"coverage.json",doc); save_heatmaps(doc)
    print(json.dumps({k:doc[k] for k in ("registered_scans","usable_scans","frame_pair_count","distance_histogram","plane_angle_deg","pair_delta_us")},indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    value=manifest(); registered=len(value["poses"]); usable=sum(bool(p.get("usable_plane_frames")) for p in value["poses"].values())
    plane_frames=sum(int(p.get("usable_plane_frames",0)) for p in value["poses"].values())
    print(f"registered_scans={registered} usable_scans={usable} frames={registered*11} pairs={registered*11} camera_plane_frames={plane_frames}")
    if OBS_PATH.is_file():
        cov=coverage_document(load_json(OBS_PATH)); strict=sum(z["strict_count"] for z in cov["zones_raw"].values()); broad=sum(z["broad_count"] for z in cov["zones_raw"].values())
        print(f"broad_zone_observations={broad} strict_zone_observations={strict} distance_bins={cov['distance_histogram']}")
    if SOLUTION_PATH.is_file():
        solution=load_json(SOLUTION_PATH); print(f"orientation={solution['orientation_decision']['status']} best={solution['selected']['orientation']} confidence_gap_mm={solution['orientation_decision']['gap_to_second_mm']:.4f}")
    else: print("orientation=PENDING_SOLVE")
    print("pose_registry=dynamic no_maximum=true")
    return 0


def cmd_solve(args: argparse.Namespace) -> int:
    obs=get_observations(refresh=args.refresh); grid=candidate_grid(obs,args.policy,quick=args.quick)
    selected=enrich_selected(dict(grid[0]),obs,args.policy)
    orientation_best={}
    for row in grid:
        orientation_best.setdefault(row["orientation"],row)
    ranked=sorted(orientation_best.values(),key=lambda x:x["score"]); gap=float(ranked[1]["score"]-ranked[0]["score"])
    by_data=sorted(ranked,key=lambda x:x["data_only_score"]); data_gap=float(by_data[1]["data_only_score"]-by_data[0]["data_only_score"])
    ambiguous=data_gap<max(1.0,0.08*ranked[0]["data_only_score"])
    semantics_best={}
    for row in grid: semantics_best.setdefault(row["range_semantics"],row)
    sem_rank=sorted(semantics_best.values(),key=lambda x:x["score"]); sem_gap=float(sem_rank[1]["score"]-sem_rank[0]["score"])
    train_ids,held_ids=split_poses(obs); c_train=observation_rows(obs,args.policy,train_ids); c_held=observation_rows(obs,args.policy,held_ids) or c_train
    base_b=next((x for x in grid if x["orientation"]==selected["orientation"] and x["range_semantics"]==selected["range_semantics"] and x["model"]=="B_low_dimensional"),None)
    model_c={"status":"NOT_EVALUATED"}
    if base_b is not None:
        model_c=fit_model_c_refinement(base_b["params"],c_train,base_b["orientation"],base_b["range_semantics"])
        held_c=metrics(residual_with_rays(base_b["params"],c_held,base_b["orientation"],base_b["range_semantics"],np.asarray(model_c["zone_center_rays"])))
        gain=float((base_b["heldout"]["rms_mm"] or 0)-(held_c["rms_mm"] or 0)); generalized=gain>max(.5,.05*(base_b["heldout"]["rms_mm"] or 0)) and (model_c["angular_correction_deg"]["rms_mm"] or 99)<2.0
        model_c.update({"heldout":held_c,"heldout_gain_mm":gain,"status":"GENERALIZATION_GAIN_PROVEN" if generalized else "NOT_SELECTED","selection_rule":"heldout gain > max(0.5 mm, 5%) and RMS angular correction < 2 deg"})
    broad_rows=observation_rows(obs,"broad"); strict_rows=observation_rows(obs,"strict"); _,broad_metrics=evaluate_params(selected["params"],broad_rows,selected["orientation"],selected["range_semantics"],selected["model"]); _,strict_metrics=evaluate_params(selected["params"],strict_rows,selected["orientation"],selected["range_semantics"],selected["model"])
    solution={"schema_version":"stage2b_solution.v1","created_at":now(),"policy":args.policy,"selected":selected,"candidate_scores":[{k:v for k,v in x.items() if k!="params"} for x in grid],"orientation_decision":{"status":"AMBIGUOUS" if ambiguous else "RESOLVED_CANDIDATE","gap_to_second_mm":gap,"data_only_gap_to_second_mm":data_gap,"ranked":[{"orientation":x["orientation"],"score":x["score"],"data_only_score":x["data_only_score"],"gauge_prior_penalty_mm":x["gauge_prior_penalty_mm"]} for x in ranked],"weak_identity_rotation_prior_used":True,"ambiguity_uses_data_only_gap":True},"range_semantics_decision":{"status":"AMBIGUOUS" if sem_gap<1.0 else "RESOLVED_CANDIDATE","gap_to_second_mm":sem_gap,"ranked":[{"range_semantics":x["range_semantics"],"score":x["score"]} for x in sem_rank]},"model_C":model_c,"broad_vs_strict":{"broad_observations":len(broad_rows),"strict_observations":len(strict_rows),"broad_metrics":broad_metrics,"strict_metrics":strict_metrics,"diagnostic_only":True,"formal_policy_modified":False}}
    apply_plane_residual_diagnostics(obs,selected)
    write_json(SOLUTION_PATH,solution)
    print(json.dumps({"selected":{k:selected[k] for k in ("orientation","range_semantics","model","train","heldout","score")},"orientation_decision":solution["orientation_decision"],"range_semantics_decision":solution["range_semantics_decision"]},indent=2))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    if not SOLUTION_PATH.is_file(): raise Stage2BError("run solve first")
    obs=get_observations(); solution=load_json(SOLUTION_PATH); s=solution["selected"]
    ids=[p["pose_id"] for p in obs["poses"] if any(f["camera_plane"].get("detected") for f in p["frames"])]
    lopo=[]
    if len(ids)>=3:
        for held in ids:
            train=observation_rows(obs,solution["policy"],set(ids)-{held}); test=observation_rows(obs,solution["policy"],{held})
            fit=robust_lm(train,s["orientation"],s["range_semantics"],s["model"],initial=np.asarray(s["params"]),max_iterations=18)
            _,m=evaluate_params(fit["params"],test,s["orientation"],s["range_semantics"],s["model"]); lopo.append({"heldout_pose_id":held,"metrics":m})
    rows=observation_rows(obs,solution["policy"]); residuals,_=evaluate_params(s["params"],rows,s["orientation"],s["range_semantics"],s["model"])
    pair=np.asarray([next(f["pair_delta_us"] for p in obs["poses"] if p["pose_id"]==r["pose_id"] for f in p["frames"] if f["frame_id"]==r["frame_id"]) for r in rows],float)
    correlation=float(np.corrcoef(pair,np.abs(residuals))[0,1]) if len(pair)>2 and np.std(pair)>0 else None
    doc={"schema_version":"stage2b_validation.v1","created_at":now(),"scan_level_heldout":s["heldout"],"lopo":lopo,"lopo_summary":metrics([x["metrics"]["rms_mm"] for x in lopo if x["metrics"]["rms_mm"] is not None]),"per_zone":s["metrics_detail"]["per_zone"],"per_distance":s["metrics_detail"]["per_distance"],"per_angle":s["metrics_detail"]["per_angle"],"pair_delta":{"distribution":metrics(pair),"abs_residual_correlation":correlation,"fixed_gate_added":False},"leakage_control":"held-out split and LOPO operate at scan/pose level; all 11 repeated frames stay together","scan_equal_weighting":"each detected frame receives 1/sqrt(frames_in_scan), preventing an 11-frame scan from acting as 11 geometric poses"}
    write_json(WORKSPACE/"validation"/"validation.json",doc); print(json.dumps(doc,indent=2)); return 0


def cmd_stability(args: argparse.Namespace) -> int:
    if not SOLUTION_PATH.is_file(): raise Stage2BError("run solve first")
    obs=get_observations(); solution=load_json(SOLUTION_PATH); s=solution["selected"]
    ids=[p["pose_id"] for p in obs["poses"] if any(f["camera_plane"].get("detected") for f in p["frames"])]
    fraction=min(max(args.fraction,0.5),0.95); target=max(2,int(math.ceil(len(ids)*fraction))) if ids else 0
    rng=np.random.default_rng(args.seed); trials=[]
    for trial in range(args.trials):
        chosen=set(rng.choice(ids,size=min(target,len(ids)),replace=False).tolist()); rows=observation_rows(obs,solution["policy"],chosen)
        fit=robust_lm(rows,s["orientation"],s["range_semantics"],s["model"],initial=np.asarray(s["params"]),max_iterations=18)
        trials.append({"trial":trial,"pose_ids":sorted(chosen),"metrics":fit["metrics"],"params":fit["params"]})
    params=np.asarray([x["params"] for x in trials]) if trials else np.empty((0,10))
    std=params.std(axis=0).tolist() if len(params) else []
    doc={"schema_version":"stage2b_stability.v1","created_at":now(),"registered_N":len(ids),"subset_fraction":fraction,"subset_size":target,"trial_count":len(trials),"parameter_std":std,"parameter_names_units":[{"name":name,"unit":unit} for name,unit in PARAMETER_NAMES_UNITS],"parameter_std_labeled":[{"name":name,"unit":unit,"std":value} for (name,unit),value in zip(PARAMETER_NAMES_UNITS,std)],"trials":trials,"dynamic_N":True}
    write_json(WORKSPACE/"stability"/"subset_stability.json",doc); print(json.dumps({k:doc[k] for k in ("registered_N","subset_fraction","subset_size","trial_count","parameter_std")},indent=2)); return 0


def params_to_transform(params: list[float]) -> tuple[np.ndarray,np.ndarray]:
    rotation,_=cv2.Rodrigues(np.asarray(params[:3],float)); return rotation,np.asarray(params[3:6],float)


def ray_boundaries(params: list[float]) -> list[list[list[float]]]:
    p=np.asarray(params,float); fx,fy=math.exp(p[6]),math.exp(p[7]); cx,cy=p[8],p[9]; result=[]
    for row in range(8):
        cells=[]
        for col in range(8):
            rays=[]
            for u,v in ((col,row),(col+1,row),(col+1,row+1),(col,row+1)):
                ray=np.array([(u-cx)/fx,(v-cy)/fy,1.0]); ray/=np.linalg.norm(ray); rays.append(ray.tolist())
            cells.append(rays)
        result.extend(cells)
    return result


def project_zones(params: list[float], orientation: str, semantics: str, distance_mm: float) -> tuple[np.ndarray,np.ndarray]:
    p=np.asarray(params,float); rays=tof_rays(p); boundaries=ray_boundaries(params); rotation,t=params_to_transform(params); k,d=camera_parameters()
    centers=[]; rectangles=[]
    for idx,ray in enumerate(rays):
        point=ray*distance_mm if semantics=="radial_range" else ray*(distance_mm/ray[2]); camera=rotation@point+t
        uv,_=cv2.projectPoints(camera.reshape(1,3),np.zeros(3),np.zeros(3),k,d); centers.append(uv.reshape(2))
        pts=[]
        for bray in boundaries[idx]:
            br=np.asarray(bray); bp=br*distance_mm if semantics=="radial_range" else br*(distance_mm/br[2]); bc=rotation@bp+t
            buv,_=cv2.projectPoints(bc.reshape(1,3),np.zeros(3),np.zeros(3),k,d); pts.append(buv.reshape(2))
        a=np.asarray(pts); rectangles.append([float(a[:,1].min()),float(a[:,0].min()),float(a[:,1].max()),float(a[:,0].max())])
    return np.asarray(centers),np.asarray(rectangles)


def save_projection_png(params: list[float], orientation: str, semantics: str, distance: int) -> None:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    centers,rects=project_zones(params,orientation,semantics,distance); fig,ax=plt.subplots(figsize=(12.8,7.2)); ax.set_xlim(0,WIDTH); ax.set_ylim(HEIGHT,0); ax.set_aspect("equal"); ax.set_facecolor("#20242a")
    for i,(c,r) in enumerate(zip(centers,rects)):
        ax.add_patch(plt.Rectangle((r[1],r[0]),r[3]-r[1],r[2]-r[0],fill=False,edgecolor=plt.cm.turbo(i/63),linewidth=1)); ax.text(c[0],c[1],str(i),fontsize=5,color="white",ha="center",va="center")
    ax.set_title(f"Stage2B model-derived ToF zone footprints at {distance} mm\nnot independently measured optical sensitivity contours")
    fig.tight_layout(); fig.savefig(WORKSPACE/"candidate"/f"zone_projection_{distance}mm.png",dpi=150); plt.close(fig)


def cmd_export(args: argparse.Namespace) -> int:
    if not SOLUTION_PATH.is_file(): raise Stage2BError("run solve first")
    solution=load_json(SOLUTION_PATH); obs=get_observations(); s=solution["selected"]; rotation,translation=params_to_transform(s["params"]); rays=tof_rays(np.asarray(s["params"]))
    if s["model"]=="A_nominal": rays=tof_rays(default_params())
    boundaries=ray_boundaries(s["params"] if s["model"]!="A_nominal" else default_params().tolist())
    transform=np.eye(4); transform[:3,:3]=rotation; transform[:3,3]=translation
    source_ids=[p["pose_id"] for p in obs["poses"]]; scans=[p["scan_id"] for p in obs["poses"]]
    source_hashes={pid:manifest()["poses"][pid]["source_hashes"] for pid in source_ids}
    model_c=solution.get("model_C",{}); use_c=model_c.get("status")=="GENERALIZATION_GAIN_PROVEN"
    if use_c: rays=np.asarray(model_c["zone_center_rays"],float)
    candidate={"schema_version":"camera_tof.stage2b_candidate.v1","status":"CANDIDATE_NOT_PROMOTED","created_at":now(),"camera_intrinsics_ref":str(CAMERA_CANDIDATE),"camera_intrinsics_sha256":CAMERA_CANDIDATE_SHA256,"transform":{"direction":"T_camera_from_tof","units":"mm","convention":"column_vector","R":rotation.tolist(),"t":translation.tolist(),"matrix":transform.tolist()},"raw_zone_orientation":{"name":s["orientation"],"raw_index_for_canonical_index":d4_map(s["orientation"]).tolist(),"status":solution["orientation_decision"]["status"]},"orientation_candidate_scores":solution["orientation_decision"]["ranked"],"range_semantics":{"recommended":s["range_semantics"],"status":solution["range_semantics_decision"]["status"]},"range_semantics_scores":solution["range_semantics_decision"]["ranked"],"tof_ray_model":{"model_type":"C_low_dimensional_plus_regularized_per_zone" if use_c else s["model"],"low_dimensional_params":{"fx_tof":math.exp(s["params"][6]),"fy_tof":math.exp(s["params"][7]),"cx_tof":s["params"][8],"cy_tof":s["params"][9]},"zone_center_rays":rays.tolist(),"per_zone_refinements":model_c if use_c else None,"zone_boundaries":{"kind":"model-derived geometric cell boundaries; not an independently measured optical sensitivity contour","corner_rays":boundaries}},"metrics":{"train":s["train"],"heldout":s["heldout"],"detail":s["metrics_detail"],"model_A_B_C":model_c,"lopo":load_json(WORKSPACE/"validation"/"validation.json").get("lopo_summary") if (WORKSPACE/"validation"/"validation.json").is_file() else None,"subset":load_json(WORKSPACE/"stability"/"subset_stability.json") if (WORKSPACE/"stability"/"subset_stability.json").is_file() else None},"broad_vs_strict":solution["broad_vs_strict"],"pair_delta_diagnostics":load_json(WORKSPACE/"validation"/"validation.json").get("pair_delta") if (WORKSPACE/"validation"/"validation.json").is_file() else None,"source_pose_ids":source_ids,"source_scan_ids":scans,"source_hashes":source_hashes,"promotion":False,"warnings":["orientation and semantics marked AMBIGUOUS must not be promoted","historical Stage7B is reference only, not new-hardware truth","zone boundaries are model-derived"]}
    write_json(CANDIDATE_PATH,candidate)
    fr_distances={}
    for distance in (400,600,1000):
        _,rects=project_zones(s["params"],s["orientation"],s["range_semantics"],distance); fr_distances[str(distance)]=rects.tolist(); save_projection_png(s["params"],s["orientation"],s["range_semantics"],distance)
    fr={"schema_version":"deltar_fr.stage2b_candidate.v1","status":"CANDIDATE_NOT_PROMOTED","shape":[64,4],"dtype":"float32 at runtime","units":"source-camera pixels","field_order":["sy","sx","ey","ex"],"rectangle_semantics":"half-open patch bounds","zone_order":"canonical row-major after raw_zone_orientation","orientation":candidate["raw_zone_orientation"],"measurement_semantics":s["range_semantics"],"values_by_reference_distance_mm":fr_distances,"device_placement":"consumer creates torch tensor on model device; this artifact is device-neutral JSON","runtime_boundary":"current DELTAR input accepts one rectangle per zone per frame; depth-dependent parallax requires selecting/projecting footprints using the associated range rather than silently storing extrinsic in fr","promotion":False}
    write_json(FR_PATH,fr)
    print(f"exported candidate={CANDIDATE_PATH}\nexported deltar_fr={FR_PATH}\npromotion=false")
    return 0


def cmd_suggest(args: argparse.Namespace) -> int:
    obs=get_observations(); cov=coverage_document(obs); suggestions=[]; reasons=[]
    angles=cov["plane_angle_deg"]
    yaw_span=(angles["yaw"]["max_deg"] or 0) if angles["yaw"]["count"] else 0
    under=[int(i) for i,z in cov["zones_raw"].items() if z["broad_count"]<max(3,cov["registered_scans"])]
    if under:
        left=sum(i%8<4 for i in under); direction="right" if left>len(under)/2 else "left"
        suggestions=["keep distance approximately unchanged","keep height approximately unchanged",f"move horizontally to the {direction}","maintain current pitch","add a moderate board-plane yaw while keeping the component still during all 11 frames"]
        reasons.append(f"{len(under)} raw zones have low broad-policy coverage")
    elif cov["distance_histogram"]["far_ge_850"]==0:
        suggestions=["keep left/right position approximately unchanged","keep height approximately unchanged","increase distance only","hold yaw and pitch approximately unchanged"]
        reasons.append("far-distance coverage is missing")
    elif angles["combined_tilt"]["p95_deg"] is None or angles["combined_tilt"]["p95_deg"]<18:
        suggestions=["keep distance approximately unchanged","keep horizontal position approximately unchanged","move vertically next in the snake route","change pitch moderately, without changing yaw"]
        reasons.append("high-tilt plane observations are underrepresented")
    else:
        suggestions=["keep distance approximately unchanged","continue the horizontal snake route one step","change only yaw enough to expose an asymmetric zone-to-plane gradient"]
        reasons.append("coverage is broad; the next capture should maximize orientation discrimination")
    if SOLUTION_PATH.is_file() and load_json(SOLUTION_PATH)["orientation_decision"]["status"]=="AMBIGUOUS": reasons.append("the leading D4 orientations remain too close")
    print("Recommended next capture:")
    for item in suggestions: print(f"- {item}")
    print("Reason:")
    for item in reasons: print(f"- {item}")
    print("This is guidance only; it does not capture or register data.")
    return 0


def cmd_advanced_diagnostics(args: argparse.Namespace) -> int:
    if not SOLUTION_PATH.is_file(): raise Stage2BError("run solve first")
    import advanced_diagnostics
    return int(advanced_diagnostics.run(args))


def build_parser() -> argparse.ArgumentParser:
    parser=argparse.ArgumentParser(description="Dynamic Stage2B Camera<->ToF calibration candidate tool (no promotion)")
    sub=parser.add_subparsers(dest="command",required=True)
    q=sub.add_parser("register",help="register one formal complete 11-frame scan by safe unique pose ID"); q.add_argument("--pose",required=True); g=q.add_mutually_exclusive_group(required=True); g.add_argument("--latest",action="store_true"); g.add_argument("--scan-id"); q.add_argument("--dry-run",action="store_true"); q.add_argument("--physical-position-note"); q.add_argument("--capture-note"); q.set_defaults(func=cmd_register)
    q=sub.add_parser("status",help="show dynamic registry and available QA"); q.set_defaults(func=cmd_status)
    q=sub.add_parser("inspect",help="inspect registered data, or one formal scan read-only"); g=q.add_mutually_exclusive_group(); g.add_argument("--latest",action="store_true"); g.add_argument("--scan-id"); q.set_defaults(func=cmd_inspect)
    q=sub.add_parser("coverage",help="derive distance/angle/zone/timing coverage from observations"); q.add_argument("--refresh",action="store_true"); q.set_defaults(func=cmd_coverage)
    q=sub.add_parser("solve",help="rank all D4 x range semantics x nominal/low-dimensional models"); q.add_argument("--policy",choices=("broad","strict"),default="broad"); q.add_argument("--refresh",action="store_true"); q.add_argument("--quick",action="store_true"); q.set_defaults(func=cmd_solve)
    q=sub.add_parser("validate",help="scan-level heldout, dynamic LOPO and residual diagnostics"); q.set_defaults(func=cmd_validate)
    q=sub.add_parser("stability-diagnostics",help="dynamic fraction-based subset stability"); q.add_argument("--fraction",type=float,default=.8); q.add_argument("--trials",type=int,default=12); q.add_argument("--seed",type=int,default=20260817); q.set_defaults(func=cmd_stability)
    q=sub.add_parser("advanced-diagnostics",help="read-only D4 gauge, range semantics, provenance and Camera mapping diagnostics"); q.add_argument("--subset-trials",type=int,default=100); q.add_argument("--seed",type=int,default=20260817); q.add_argument("--max-iterations",type=int,default=12); q.set_defaults(func=cmd_advanced_diagnostics)
    q=sub.add_parser("suggest-next",help="diagnostic-driven, snake-route-compatible capture advice only"); q.set_defaults(func=cmd_suggest)
    q=sub.add_parser("export-candidate",help="export candidate and DELTAR fr artifacts; never promote"); q.set_defaults(func=cmd_export)
    return parser


def main(argv: list[str] | None=None) -> int:
    try:
        args=build_parser().parse_args(argv); ensure_layout(); return int(args.func(args))
    except Stage2BError as exc:
        print(f"ERROR: {exc}",file=sys.stderr); return 2


if __name__=="__main__": raise SystemExit(main())
