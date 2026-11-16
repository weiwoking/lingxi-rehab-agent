from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ORIENTATION_SCHEMA = "camera_orientation_manifest.v1"
BOARD_SCHEMA = "charuco_board_manifest.v1"
INTRINSICS_REPORT_SCHEMA = "camera_intrinsics_report.v1"
CALIBRATION_V2_SCHEMA = "deltar_geometry_calibration.v2"
EXPECTED_WIDTH = 640
EXPECTED_HEIGHT = 360


@dataclass(frozen=True)
class CameraCalibrationError(Exception):
    code: str
    message: str
    field: str | None = None
    details: Any = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" [{self.field}]" if self.field else "")

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field is not None:
            value["field"] = self.field
        if self.details is not None:
            value["details"] = self.details
        return value


def fail(code: str, message: str, field: str | None = None, details: Any = None) -> None:
    raise CameraCalibrationError(code, message, field, details)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        fail("E_FILE_READ", f"cannot hash file: {exc}", str(path))
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_strict_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(f"non-finite constant {token}")),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        fail("E_JSON_SCHEMA", f"cannot read strict UTF-8 JSON: {exc}", str(path))
    if type(value) is not dict:
        fail("E_JSON_SCHEMA", "JSON root must be an object", str(path))
    assert_finite(value)
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def assert_finite(value: Any, field: str = "$") -> None:
    if type(value) is float and not math.isfinite(value):
        fail("E_NONFINITE", "number must be finite", field)
    if isinstance(value, dict):
        for key, item in value.items():
            assert_finite(item, f"{field}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_finite(item, f"{field}[{index}]")


def exact_object(value: Any, keys: set[str], field: str) -> dict[str, Any]:
    if type(value) is not dict:
        fail("E_JSON_SCHEMA", "expected object", field)
    missing = sorted(keys - set(value))
    extra = sorted(set(value) - keys)
    if missing:
        fail("E_JSON_SCHEMA", "required fields are missing", field, missing)
    if extra:
        fail("E_JSON_SCHEMA", "additional fields are not allowed", field, extra)
    return value


def _positive_number(value: Any, field: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        fail("E_JSON_SCHEMA", "expected finite number", field)
    if value < 0 if allow_zero else value <= 0:
        fail("E_JSON_SCHEMA", "number is outside the allowed range", field)
    return float(value)


def validate_orientation_manifest(value: dict[str, Any], *, require_confirmed: bool = False) -> dict[str, Any]:
    value = exact_object(value, {
        "schema_version", "width", "height", "physical_orientation", "software_rotation_degrees",
        "scene_upright_confirmed", "confirmed_at", "source_image_sha256", "notes", "exif_orientation",
    }, "orientation")
    if value["schema_version"] != ORIENTATION_SCHEMA:
        fail("E_ORIENTATION_SCHEMA", f"schema_version must be {ORIENTATION_SCHEMA}", "schema_version")
    if (value["width"], value["height"]) != (EXPECTED_WIDTH, EXPECTED_HEIGHT):
        fail("E_ORIENTATION_RESOLUTION", "final calibration image must be 640x360", "width/height")
    if value["physical_orientation"] != "landscape":
        fail("E_ORIENTATION_PHYSICAL", "physical orientation must be landscape", "physical_orientation")
    if value["software_rotation_degrees"] != 0:
        fail("E_ORIENTATION_ROTATION", "software rotation must be zero", "software_rotation_degrees")
    if type(value["scene_upright_confirmed"]) is not bool:
        fail("E_ORIENTATION_SCHEMA", "scene_upright_confirmed must be boolean", "scene_upright_confirmed")
    if require_confirmed and not value["scene_upright_confirmed"]:
        fail("E_ORIENTATION_UPRIGHT", "manual upright confirmation is required", "scene_upright_confirmed")
    if value["scene_upright_confirmed"] and not isinstance(value["confirmed_at"], str):
        fail("E_ORIENTATION_SCHEMA", "confirmed_at is required after confirmation", "confirmed_at")
    if not isinstance(value["source_image_sha256"], str) or len(value["source_image_sha256"]) != 64:
        fail("E_ORIENTATION_SCHEMA", "source image SHA-256 is invalid", "source_image_sha256")
    return value


def validate_board_manifest(value: dict[str, Any], *, require_print_measurement: bool = False) -> dict[str, Any]:
    value = exact_object(value, {
        "schema_version", "board_id", "family", "dictionary", "squares_x", "squares_y",
        "square_length_mm", "marker_length_mm", "page_size", "print_scale", "render_dpi",
        "board_image", "board_sha256", "generated_at", "print_measurement",
    }, "board")
    if value["schema_version"] != BOARD_SCHEMA or value["family"] != "opencv_charuco":
        fail("E_BOARD_SCHEMA", "unsupported board schema/family", "schema_version/family")
    if type(value["squares_x"]) is not int or value["squares_x"] < 3 or type(value["squares_y"]) is not int or value["squares_y"] < 3:
        fail("E_BOARD_DIMENSIONS", "squares_x/y must be integers >= 3", "squares_x/squares_y")
    square = _positive_number(value["square_length_mm"], "square_length_mm")
    marker = _positive_number(value["marker_length_mm"], "marker_length_mm")
    if marker >= square:
        fail("E_BOARD_DIMENSIONS", "marker length must be smaller than square length", "marker_length_mm")
    if value["page_size"] != "A4" or value["print_scale"] != 1.0:
        fail("E_BOARD_PRINT", "Stage 7A board must use A4 at 100% scale", "page_size/print_scale")
    if not isinstance(value["board_sha256"], str) or len(value["board_sha256"]) != 64:
        fail("E_BOARD_SCHEMA", "board SHA-256 is invalid", "board_sha256")
    measurement = exact_object(value["print_measurement"], {
        "confirmed", "measured_square_length_mm", "measured_board_width_mm", "measured_board_height_mm",
        "measured_at", "tolerance_percent",
    }, "print_measurement")
    if type(measurement["confirmed"]) is not bool:
        fail("E_BOARD_PRINT", "print measurement confirmation must be boolean", "print_measurement.confirmed")
    if require_print_measurement and not measurement["confirmed"]:
        fail("E_BOARD_PRINT_UNCONFIRMED", "measure the printed square and board before calibration", "print_measurement.confirmed")
    if measurement["confirmed"]:
        measured = _positive_number(measurement["measured_square_length_mm"], "print_measurement.measured_square_length_mm")
        tolerance = _positive_number(measurement["tolerance_percent"], "print_measurement.tolerance_percent", allow_zero=True)
        error = abs(measured - square) / square * 100.0
        if error > tolerance:
            fail("E_BOARD_PRINT_SCALE", "measured square length exceeds declared tolerance", "print_measurement.measured_square_length_mm", {"error_percent": error})
        _positive_number(measurement["measured_board_width_mm"], "print_measurement.measured_board_width_mm")
        _positive_number(measurement["measured_board_height_mm"], "print_measurement.measured_board_height_mm")
    return value


def validate_calibration_v2(value: dict[str, Any]) -> dict[str, Any]:
    value = exact_object(value, {
        "schema_version", "profile_id", "measurement_status", "absolute_geometry_valid", "camera",
        "camera_tof_extrinsics", "world_pose", "quality", "provenance",
    }, "$")
    if value["schema_version"] != CALIBRATION_V2_SCHEMA:
        fail("E_PROFILE_V2_SCHEMA", f"schema_version must be {CALIBRATION_V2_SCHEMA}", "schema_version")
    if value["measurement_status"] not in {"measured", "unverified"}:
        fail("E_PROFILE_V2_SCHEMA", "measurement_status must be measured or unverified", "measurement_status")
    if value["absolute_geometry_valid"] is not False:
        fail("E_PROFILE_V2_SEMANTIC", "camera intrinsics alone cannot enable absolute geometry", "absolute_geometry_valid")
    camera = exact_object(value["camera"], {"resolution", "orientation", "intrinsics"}, "camera")
    resolution = exact_object(camera["resolution"], {"width", "height"}, "camera.resolution")
    if (resolution["width"], resolution["height"]) != (EXPECTED_WIDTH, EXPECTED_HEIGHT):
        fail("E_PROFILE_V2_SEMANTIC", "camera resolution must be 640x360", "camera.resolution")
    orientation = exact_object(camera["orientation"], {"physical", "software_rotation_degrees", "scene_upright_confirmed"}, "camera.orientation")
    if orientation != {"physical": "landscape", "software_rotation_degrees": 0, "scene_upright_confirmed": True}:
        fail("E_PROFILE_V2_SEMANTIC", "measured camera orientation contract is not satisfied", "camera.orientation")
    intrinsics = exact_object(camera["intrinsics"], {"model", "fx", "fy", "cx", "cy", "distortion"}, "camera.intrinsics")
    if intrinsics["model"] != "opencv_brown":
        fail("E_PROFILE_V2_SEMANTIC", "intrinsics model must be opencv_brown", "camera.intrinsics.model")
    for key in ("fx", "fy", "cx", "cy"):
        _positive_number(intrinsics[key], f"camera.intrinsics.{key}", allow_zero=key in {"cx", "cy"})
    if not (0 <= intrinsics["cx"] < EXPECTED_WIDTH and 0 <= intrinsics["cy"] < EXPECTED_HEIGHT):
        fail("E_PROFILE_V2_SEMANTIC", "principal point is outside the image", "camera.intrinsics")
    distortion = exact_object(intrinsics["distortion"], {"k1", "k2", "p1", "p2", "k3"}, "camera.intrinsics.distortion")
    for key, item in distortion.items():
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            fail("E_PROFILE_V2_SCHEMA", "distortion coefficients must be finite", f"camera.intrinsics.distortion.{key}")
    for field in ("camera_tof_extrinsics", "world_pose"):
        if exact_object(value[field], {"status"}, field)["status"] != "unresolved":
            fail("E_PROFILE_V2_SEMANTIC", f"{field} must remain unresolved in Stage 7A", f"{field}.status")
    quality = exact_object(value["quality"], {
        "fit_rms_px", "heldout_rms_px", "heldout_p95_px", "accepted_images", "fit_images",
        "heldout_images", "quality_status",
    }, "quality")
    for key in ("fit_rms_px", "heldout_rms_px", "heldout_p95_px"):
        _positive_number(quality[key], f"quality.{key}", allow_zero=True)
    for key in ("accepted_images", "fit_images", "heldout_images"):
        if type(quality[key]) is not int or quality[key] < 1:
            fail("E_PROFILE_V2_SCHEMA", "image counts must be positive integers", f"quality.{key}")
    if quality["quality_status"] not in {"PASS", "PASS_DEMO_ONLY"}:
        fail("E_PROFILE_V2_SEMANTIC", "a measured profile requires accepted held-out quality", "quality.quality_status")
    provenance = exact_object(value["provenance"], {
        "board_manifest_sha256", "orientation_manifest_sha256", "accepted_images_manifest_sha256",
        "accepted_image_sha256", "python_version", "opencv_version", "cli_parameters", "seed",
        "fit_images", "heldout_images", "generated_at", "tool_version", "source_path",
    }, "provenance")
    if type(provenance["accepted_image_sha256"]) is not dict or not provenance["accepted_image_sha256"]:
        fail("E_PROFILE_V2_SCHEMA", "accepted image hashes are required", "provenance.accepted_image_sha256")
    assert_finite(value)
    return value


def v2_camera_to_v1_camera_model(value: dict[str, Any]) -> dict[str, Any]:
    """Explicit adapter for legacy consumers; it does not claim v1 extrinsics or absolute geometry."""
    value = validate_calibration_v2(value)
    intrinsics = value["camera"]["intrinsics"]
    distortion = intrinsics["distortion"]
    return {
        "model": "opencv_brown",
        "fx": intrinsics["fx"], "fy": intrinsics["fy"], "cx": intrinsics["cx"], "cy": intrinsics["cy"],
        "distortion": [distortion[key] for key in ("k1", "k2", "p1", "p2", "k3")],
    }
