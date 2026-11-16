from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .errors import ErrorCode, PipelineError
from .rgb565 import HEIGHT, PIXEL_FORMAT, STRIDE_BYTES, WIDTH
from .scan_contracts import TOF_KEYS

TOF_SCHEMA = "rehab_tof_raw.v1"
PROFILE_SCHEMA = "deltar_p4_profile.v1"
CANVAS_STRATEGIES = {
    "letterbox_640x480",
    "resize_640x480",
    "scale_to_fill_center_crop_640x480",
    "direct_640x360",
    "direct_640x384_pad32",
}
ZONE_STRATEGIES = {"identity", "flip_horizontal", "flip_vertical", "flip_both", "rotate_90", "rotate_180", "rotate_270", "explicit_index_map_64"}
MASK_STRATEGIES = {"explicit_bool64", "all_valid", "all_invalid", "status_count_candidate_v1"}
FR_STRATEGIES = {"explicit_model_canvas", "regular_grid_source_then_transform"}


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PipelineError(ErrorCode.FIELD_MISSING, "required JSON file is missing", str(path)) from exc
    except (UnicodeError, OSError) as exc:
        raise PipelineError(ErrorCode.JSON_PARSE, f"failed to read UTF-8 JSON: {exc}", str(path)) from exc
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise PipelineError(ErrorCode.JSON_PARSE, f"invalid strict JSON: {exc}", str(path)) from exc
    if type(value) is not dict:
        raise PipelineError(ErrorCode.TYPE, "JSON root must be an object", str(path))
    return value


def _required(obj: dict[str, Any], key: str, prefix: str) -> Any:
    if key not in obj:
        raise PipelineError(ErrorCode.FIELD_MISSING, f"missing required field {prefix}.{key}", f"{prefix}.{key}")
    return obj[key]


def _int(value: Any, field: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise PipelineError(ErrorCode.TYPE, "expected an integer without implicit coercion", field)
    if value < minimum or (maximum is not None and value > maximum):
        code = ErrorCode.STATUS_RANGE if field.startswith("target_status") else ErrorCode.COUNT_RANGE
        raise PipelineError(code, f"integer outside allowed range {minimum}..{maximum}", field)
    return value


def _number(value: Any, field: str, *, minimum: float = 0.0, sigma: bool = False) -> float:
    if type(value) not in (int, float):
        raise PipelineError(ErrorCode.TYPE, "expected a JSON number without implicit coercion", field)
    converted = float(value)
    if not math.isfinite(converted):
        raise PipelineError(ErrorCode.NONFINITE, "number must be finite", field)
    if converted < minimum:
        code = ErrorCode.NEGATIVE_SIGMA if sigma else ErrorCode.TYPE
        raise PipelineError(code, f"number must be >= {minimum}", field)
    return converted


def _array64(obj: dict[str, Any], key: str) -> list[Any]:
    value = _required(obj, key, "tof")
    if type(value) is not list:
        raise PipelineError(ErrorCode.TYPE, "expected a flat array", key)
    if len(value) != 64:
        raise PipelineError(ErrorCode.ARRAY_LENGTH, f"expected exactly 64 values, got {len(value)}", key)
    if any(type(item) is list or type(item) is dict for item in value):
        raise PipelineError(ErrorCode.TYPE, "nested arrays/objects are not accepted", key)
    return value


def validate_tof(obj: dict[str, Any], *, max_sequence: int | None = None) -> dict[str, Any]:
    actual = set(obj)
    missing = sorted(TOF_KEYS - actual)
    unknown = sorted(actual - TOF_KEYS)
    if missing:
        raise PipelineError(ErrorCode.FIELD_MISSING, "missing required ToF fields", "tof", missing)
    if unknown:
        raise PipelineError(ErrorCode.TYPE, "unknown ToF fields are not allowed", "tof", unknown)
    if _required(obj, "schema_version", "tof") != TOF_SCHEMA:
        raise PipelineError(ErrorCode.SCHEMA_VERSION, f"expected {TOF_SCHEMA}", "tof.schema_version")
    sequence = _int(_required(obj, "sequence", "tof"), "sequence", maximum=max_sequence)
    timestamp = _int(_required(obj, "timestamp_us", "tof"), "timestamp_us")
    distance = [_int(v, f"distance_mm[{i}]", maximum=65535) for i, v in enumerate(_array64(obj, "distance_mm"))]
    sigma = []
    for i, value in enumerate(_array64(obj, "range_sigma_mm")):
        if type(value) is int and value < 0:
            raise PipelineError(ErrorCode.NEGATIVE_SIGMA, "number must be >= 0", f"range_sigma_mm[{i}]")
        sigma.append(_int(value, f"range_sigma_mm[{i}]", maximum=65535))
    statuses = [_int(v, f"target_status[{i}]", maximum=255) for i, v in enumerate(_array64(obj, "target_status"))]
    counts = [_int(v, f"nb_target_detected[{i}]", maximum=255) for i, v in enumerate(_array64(obj, "nb_target_detected"))]
    return {
        "schema_version": TOF_SCHEMA,
        "sequence": sequence,
        "timestamp_us": timestamp,
        "distance_mm": distance,
        "range_sigma_mm": sigma,
        "target_status": statuses,
        "nb_target_detected": counts,
    }


def _section(profile: dict[str, Any], key: str) -> dict[str, Any]:
    value = _required(profile, key, "profile")
    if type(value) is not dict:
        raise PipelineError(ErrorCode.TYPE, "profile section must be an object", f"profile.{key}")
    return value


def _validate_fr(values: Any) -> list[list[float]]:
    if type(values) is not list or len(values) != 64:
        raise PipelineError(ErrorCode.FR_SHAPE, "fr values must have exactly 64 rows", "fr.values")
    output: list[list[float]] = []
    for row_index, row in enumerate(values):
        if type(row) is not list or len(row) != 4:
            raise PipelineError(ErrorCode.FR_SHAPE, "each fr row must be [sy,sx,ey,ex]", f"fr.values[{row_index}]")
        converted = [_number(item, f"fr.values[{row_index}][{i}]", minimum=-1e9) for i, item in enumerate(row)]
        sy, sx, ey, ex = converted
        if ey <= sy or ex <= sx:
            raise PipelineError(ErrorCode.FR_INVALID, "fr rectangles are half-open and must have positive extent", f"fr.values[{row_index}]")
        output.append(converted)
    return output


def validate_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if _required(profile, "schema_version", "profile") != PROFILE_SCHEMA:
        raise PipelineError(ErrorCode.SCHEMA_VERSION, f"expected {PROFILE_SCHEMA}", "profile.schema_version")
    profile_id = _required(profile, "profile_id", "profile")
    sample_kind = _required(profile, "sample_kind", "profile")
    if type(profile_id) is not str or not profile_id or type(sample_kind) is not str or not sample_kind:
        raise PipelineError(ErrorCode.TYPE, "profile_id and sample_kind must be non-empty strings", "profile")
    image = _section(profile, "image")
    expected_image = {"width": WIDTH, "height": HEIGHT, "stride": STRIDE_BYTES, "pixel_format": PIXEL_FORMAT}
    for key, expected in expected_image.items():
        if _required(image, key, "profile.image") != expected:
            raise PipelineError(ErrorCode.IMAGE_FORMAT, f"profile image {key} must be {expected!r}", f"image.{key}")
    canvas = _section(profile, "canvas")
    canvas_strategy = _required(canvas, "strategy", "profile.canvas")
    if canvas_strategy not in CANVAS_STRATEGIES:
        raise PipelineError(ErrorCode.PROFILE_UNSUPPORTED, f"unsupported canvas strategy {canvas_strategy!r}", "canvas.strategy")
    fill = canvas.get("fill_rgb", [0, 0, 0])
    if type(fill) is not list or len(fill) != 3 or any(type(v) is not int or v < 0 or v > 255 for v in fill):
        raise PipelineError(ErrorCode.TYPE, "fill_rgb must be three integers in 0..255", "canvas.fill_rgb")
    zone = _section(profile, "zone_mapping")
    zone_strategy = _required(zone, "strategy", "profile.zone_mapping")
    if zone_strategy not in ZONE_STRATEGIES:
        raise PipelineError(ErrorCode.PROFILE_UNSUPPORTED, "unsupported zone mapping", "zone_mapping.strategy")
    if zone_strategy == "explicit_index_map_64":
        mapping = zone.get("explicit_index_map_64")
        if type(mapping) is not list or len(mapping) != 64 or any(type(v) is not int for v in mapping) or sorted(mapping) != list(range(64)):
            raise PipelineError(ErrorCode.PROFILE_UNSUPPORTED, "explicit zone map must be a permutation of 0..63", "zone_mapping.explicit_index_map_64")
    histogram = _section(profile, "histogram")
    if histogram.get("mu_strategy") != "distance_mm_to_m" or histogram.get("sigma_strategy") != "range_sigma_mm_to_m":
        raise PipelineError(ErrorCode.PROFILE_UNSUPPORTED, "only explicit mm-to-m histogram mappings are supported", "histogram")
    mask = _section(profile, "mask")
    mask_strategy = _required(mask, "strategy", "profile.mask")
    if mask_strategy not in MASK_STRATEGIES:
        raise PipelineError(ErrorCode.PROFILE_UNSUPPORTED, "unsupported mask strategy", "mask.strategy")
    if mask_strategy == "explicit_bool64":
        values = mask.get("values")
        if type(values) is not list or len(values) != 64:
            raise PipelineError(ErrorCode.MASK_LENGTH, "explicit mask must contain 64 booleans", "mask.values")
        if any(type(value) is not bool for value in values):
            raise PipelineError(ErrorCode.TYPE, "explicit mask accepts booleans only", "mask.values")
    fr = _section(profile, "fr")
    fr_strategy = _required(fr, "strategy", "profile.fr")
    if fr_strategy not in FR_STRATEGIES:
        raise PipelineError(ErrorCode.PROFILE_UNSUPPORTED, "unsupported fr strategy", "fr.strategy")
    if fr_strategy == "explicit_model_canvas":
        fr["values"] = _validate_fr(fr.get("values"))
    seed = _int(_required(profile, "seed", "profile"), "profile.seed")
    intrinsics = profile.get("test_intrinsics", {"fx": 500.0, "fy": 500.0, "cx": 320.0, "cy": 180.0})
    if type(intrinsics) is not dict:
        raise PipelineError(ErrorCode.TYPE, "test_intrinsics must be an object", "test_intrinsics")
    checked_intrinsics = {key: _number(_required(intrinsics, key, "test_intrinsics"), f"test_intrinsics.{key}", minimum=-1e9) for key in ("fx", "fy", "cx", "cy")}
    return {
        **profile,
        "profile_id": profile_id,
        "sample_kind": sample_kind,
        "image": expected_image,
        "canvas": {**canvas, "strategy": canvas_strategy, "fill_rgb": fill},
        "seed": seed,
        "test_intrinsics": checked_intrinsics,
    }


def load_sample_contract(sample_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return validate_tof(load_json(sample_dir / "tof.json")), validate_profile(load_json(sample_dir / "profile.json"))
