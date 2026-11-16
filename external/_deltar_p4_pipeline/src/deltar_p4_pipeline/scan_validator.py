from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import validate_tof
from .errors import PipelineError
from .scan_contracts import (
    DEVICE_KEYS,
    FRAME_ID_RE,
    FRAME_KEYS,
    FRAME_SCHEMA,
    IMAGE_KEYS,
    PROFILE_IDS,
    SCAN_ID_RE,
    SCAN_KEYS,
    SCAN_SCHEMA,
    TOF_REF_KEYS,
    ScanContractError,
    load_strict_json,
    require,
    require_int,
    require_exact_keys,
    require_rfc3339_datetime,
    require_string,
    resolve_inside,
    safe_leaf_filename,
)
from .scan_profiles import ProfileBundle


@dataclass(frozen=True)
class ValidatedFrame:
    directory: Path
    frame: dict[str, Any]
    tof: dict[str, Any]
    image_path: Path


@dataclass(frozen=True)
class ValidatedScan:
    directory: Path
    scan: dict[str, Any]
    frames: tuple[ValidatedFrame, ...]
    rejected_frames: tuple[dict[str, Any], ...]
    total_bytes: int


def _validate_profiles(profiles: Any, bundle: ProfileBundle | None) -> dict[str, str]:
    if type(profiles) is not dict:
        raise ScanContractError("E_SCAN_SCHEMA", "profiles must be an object", "scan.profiles")
    require_exact_keys(profiles, set(PROFILE_IDS), code="E_SCAN_SCHEMA", prefix="scan.profiles")
    checked = {key: require_string(require(profiles, key, code="E_SCAN_SCHEMA", prefix="scan.profiles"), f"scan.profiles.{key}", code="E_SCAN_SCHEMA", max_length=64) for key in PROFILE_IDS}
    for key, expected in PROFILE_IDS.items():
        if checked[key] != expected:
            raise ScanContractError("E_SCAN_SCHEMA", "profile identifier is not the frozen Stage 6 value", f"scan.profiles.{key}", {"expected": expected, "actual": checked[key]})
    if bundle is not None:
        for key, expected in bundle.ids.items():
            if checked[key] != expected:
                raise ScanContractError("E_PROFILE_BUNDLE", "scan references a different profile", f"scan.profiles.{key}", {"expected": expected, "actual": checked[key]})
    return checked


def validate_scan_document(scan: dict[str, Any], bundle: ProfileBundle | None = None) -> dict[str, Any]:
    require_exact_keys(scan, SCAN_KEYS, code="E_SCAN_SCHEMA", prefix="scan")
    if scan["schema_version"] != SCAN_SCHEMA:
        raise ScanContractError("E_SCAN_SCHEMA", f"expected {SCAN_SCHEMA}", "scan.schema_version")
    scan_id = require_string(scan["scan_id"], "scan.scan_id", code="E_SCAN_SCHEMA", max_length=128)
    if SCAN_ID_RE.fullmatch(scan_id) is None:
        raise ScanContractError("E_SCAN_SCHEMA", "scan_id must contain only ASCII letters, digits, underscore, or hyphen", "scan.scan_id")
    require_rfc3339_datetime(scan["created_at"], "scan.created_at", code="E_SCAN_SCHEMA")
    frame_count = require_int(scan["frame_count"], "scan.frame_count", code="E_SCAN_FRAME_COUNT", minimum=1, maximum=64)
    mode = scan["capture_mode"]
    if mode not in {"single_frame", "multi_frame"} or (mode == "single_frame") != (frame_count == 1):
        raise ScanContractError("E_SCAN_SCHEMA", "capture_mode and frame_count disagree", "scan.capture_mode")
    device = scan["device"]
    if type(device) is not dict:
        raise ScanContractError("E_SCAN_SCHEMA", "device must be an object", "scan.device")
    require_exact_keys(device, DEVICE_KEYS, code="E_SCAN_SCHEMA", prefix="scan.device")
    for key in DEVICE_KEYS:
        require_string(device[key], f"scan.device.{key}", code="E_SCAN_SCHEMA", max_length=64)
    scan["profiles"] = _validate_profiles(scan["profiles"], bundle)
    notes = scan["notes"]
    if notes is not None and (type(notes) is not str or len(notes) > 512):
        raise ScanContractError("E_SCAN_SCHEMA", "notes must be null or a string up to 512 characters", "scan.notes")
    return scan


def validate_frame_document(frame: dict[str, Any]) -> dict[str, Any]:
    require_exact_keys(frame, FRAME_KEYS, code="E_FRAME_SCHEMA", prefix="frame")
    if frame["schema_version"] != FRAME_SCHEMA:
        raise ScanContractError("E_FRAME_SCHEMA", f"expected {FRAME_SCHEMA}", "frame.schema_version")
    frame_id = require_string(frame["frame_id"], "frame.frame_id", code="E_FRAME_SCHEMA", max_length=12)
    if FRAME_ID_RE.fullmatch(frame_id) is None:
        raise ScanContractError("E_FRAME_SCHEMA", "frame_id must match frame_[0-9]{6}", "frame.frame_id")
    require_int(frame["sequence"], "frame.sequence", code="E_FRAME_SCHEMA", maximum=63)
    require_int(frame["camera_timestamp_us"], "frame.camera_timestamp_us", code="E_FRAME_SCHEMA")
    require_int(frame["tof_timestamp_us"], "frame.tof_timestamp_us", code="E_FRAME_SCHEMA")
    require_int(frame["pair_delta_us"], "frame.pair_delta_us", code="E_FRAME_SCHEMA")
    image = frame["image"]
    if type(image) is not dict:
        raise ScanContractError("E_FRAME_SCHEMA", "image must be an object", "frame.image")
    require_exact_keys(image, IMAGE_KEYS, code="E_FRAME_SCHEMA", prefix="frame.image")
    safe_leaf_filename(image["filename"], "frame.image.filename")
    expected_image = {"filename": "image.rgb565", "width": 640, "height": 360, "pixel_format": "RGB565_LE", "stride_bytes": 1280, "payload_bytes": 460800}
    for key, expected in expected_image.items():
        if image[key] != expected:
            raise ScanContractError("E_FRAME_IMAGE_SIZE", f"expected {expected!r}", f"frame.image.{key}")
    tof_ref = frame["tof"]
    if type(tof_ref) is not dict:
        raise ScanContractError("E_FRAME_TOF_SCHEMA", "tof must be an object", "frame.tof")
    require_exact_keys(tof_ref, TOF_REF_KEYS, code="E_FRAME_TOF_SCHEMA", prefix="frame.tof")
    safe_leaf_filename(tof_ref["filename"], "frame.tof.filename")
    if tof_ref["filename"] != "tof.json" or tof_ref["resolution"] != [8, 8]:
        raise ScanContractError("E_FRAME_TOF_SCHEMA", "expected tof.json with resolution [8,8]", "frame.tof")
    return frame


def _validate_frame(frame_dir: Path, expected_sequence: int) -> ValidatedFrame:
    frame_path = frame_dir / "frame.json"
    frame = load_strict_json(frame_path, code="E_FRAME_SCHEMA")
    validate_frame_document(frame)
    frame_id = frame["frame_id"]
    if frame_id != frame_dir.name:
        raise ScanContractError("E_FRAME_SCHEMA", "frame_id must equal its directory name", "frame.frame_id")
    sequence = frame["sequence"]
    if sequence != expected_sequence:
        raise ScanContractError("E_FRAME_TIMESTAMP", "frame sequence must be contiguous and directory ordered", "frame.sequence", {"expected": expected_sequence, "actual": sequence})
    camera_ts = frame["camera_timestamp_us"]
    tof_ts = frame["tof_timestamp_us"]
    pair_delta = frame["pair_delta_us"]
    if pair_delta != abs(camera_ts - tof_ts):
        raise ScanContractError("E_FRAME_TIMESTAMP", "pair_delta_us must equal absolute camera/ToF delta", "frame.pair_delta_us")
    image = frame["image"]
    image_name = safe_leaf_filename(image["filename"], "frame.image.filename")
    image_path = resolve_inside(frame_dir, Path(image_name), "frame.image.filename")
    if not image_path.is_file() or image_path.stat().st_size != 460800:
        raise ScanContractError("E_FRAME_IMAGE_SIZE", "RGB565 payload must be exactly 460800 bytes", "frame.image.payload_bytes")
    tof_ref = frame["tof"]
    tof_name = safe_leaf_filename(tof_ref["filename"], "frame.tof.filename")
    tof_path = resolve_inside(frame_dir, Path(tof_name), "frame.tof.filename")
    try:
        tof = validate_tof(load_strict_json(tof_path, code="E_FRAME_TOF_SCHEMA"), max_sequence=63)
    except PipelineError as exc:
        raise ScanContractError("E_FRAME_TOF_SCHEMA", exc.message, exc.field, exc.details) from exc
    if tof["sequence"] != sequence or tof["timestamp_us"] != tof_ts:
        raise ScanContractError("E_FRAME_TIMESTAMP", "ToF sequence/timestamp differs from frame.json", "tof")
    return ValidatedFrame(frame_dir, frame, tof, image_path)


def validate_scan(scan_dir: Path, bundle: ProfileBundle | None = None, *, max_frames: int | None = None,
                  max_scan_bytes: int | None = None, allow_frame_rejections: bool = False,
                  expected_scan_id: str | None = None) -> ValidatedScan:
    scan_dir = scan_dir.resolve()
    if not scan_dir.is_dir():
        raise ScanContractError("E_SCAN_SCHEMA", "scan directory is missing", "scan_dir")
    scan = load_strict_json(scan_dir / "scan.json", code="E_SCAN_SCHEMA")
    validate_scan_document(scan, bundle)
    scan_id = scan["scan_id"]
    expected_root_id = expected_scan_id if expected_scan_id is not None else scan_dir.name
    if scan_id != expected_root_id:
        raise ScanContractError("E_SCAN_SCHEMA", "scan_id must equal directory name", "scan.scan_id")
    frame_count = scan["frame_count"]
    if max_frames is not None and frame_count > max_frames:
        raise ScanContractError("E_SCAN_FRAME_COUNT", "frame_count exceeds the selected profile limit", "scan.frame_count", {"limit": max_frames, "actual": frame_count})
    frames_root = resolve_inside(scan_dir, Path("frames"), "frames")
    if not frames_root.is_dir():
        raise ScanContractError("E_SCAN_FRAME_COUNT", "frames directory is missing", "frames")
    directories = sorted(path for path in frames_root.iterdir() if path.is_dir())
    if len(directories) != frame_count:
        raise ScanContractError("E_SCAN_FRAME_COUNT", "frame_count differs from frame directories", "scan.frame_count", {"declared": frame_count, "found": len(directories)})
    valid_frames: list[ValidatedFrame] = []
    rejected_frames: list[dict[str, Any]] = []
    for index, path in enumerate(directories):
        try:
            valid_frames.append(_validate_frame(path, index))
        except ScanContractError as exc:
            if not allow_frame_rejections:
                raise
            rejected_frames.append({"frame_id": path.name, "sequence": index, "error": exc.as_dict()})
    frames = tuple(valid_frames)
    ids = [frame.frame["frame_id"] for frame in frames]
    if len(ids) != len(set(ids)):
        raise ScanContractError("E_SCAN_DUPLICATE_FRAME", "duplicate frame_id", "frames")
    total_bytes = sum(path.stat().st_size for path in scan_dir.rglob("*") if path.is_file())
    if max_scan_bytes is not None and total_bytes > max_scan_bytes:
        raise ScanContractError("E_SCAN_SCHEMA", "scan exceeds max_scan_bytes", "scan", {"limit": max_scan_bytes, "actual": total_bytes})
    return ValidatedScan(scan_dir, scan, frames, tuple(rejected_frames), total_bytes)
