from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .formal_geometry_authority import (
    FormalGeometryAuthority,
    load_formal_geometry_authority,
)
from .scan_contracts import ScanContractError, load_strict_json


HORIZONTAL_PLANES_SCHEMA = "deltar_horizontal_planes.v2"
SUMMARY_SCHEMA = "stage7d_summary.v2"
PROFILE_SCHEMA = "formal_horizontal_plane_profile.v2"

PROFILE_KEYS = {
    "schema_version",
    "fit_stride_px",
    "max_planes",
    "ransac_iterations",
    "ransac_seed",
    "plane_residual_threshold_mm",
    "max_horizontal_tilt_deg",
    "ground_abs_height_gate_mm",
    "min_support_height_mm",
    "max_support_height_mm",
    "min_ground_pixels",
    "min_support_pixels",
    "min_visible_span_px",
    "largest_component_only",
    "min_depth_m",
    "max_depth_m",
    "depth_semantics",
    "selection_status",
}


@dataclass(frozen=True)
class Stage7DContext:
    camera_model: dict[str, Any]
    gravity_up_camera: np.ndarray
    camera_height_mm: float
    profile: dict[str, Any]
    authority: FormalGeometryAuthority
    camera_summary: dict[str, Any]
    camera_world_summary: dict[str, Any]
    camera_tof_summary: dict[str, Any]


def _fail(message: str, field: str, details: Any = None) -> None:
    raise ScanContractError("E_STAGE7D_PROFILE", message, field, details)


def _finite_number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("expected a finite number", field)
    checked = float(value)
    if not math.isfinite(checked):
        _fail("expected a finite number", field)
    if minimum is not None and checked < minimum:
        _fail(f"must be >= {minimum}", field)
    if maximum is not None and checked > maximum:
        _fail(f"must be <= {maximum}", field)
    return checked


def validate_stage7d_profile(value: dict[str, Any]) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("profile must be an object", "$")
    missing = sorted(PROFILE_KEYS - set(value))
    unknown = sorted(set(value) - PROFILE_KEYS)
    if missing or unknown:
        _fail("profile keys mismatch", "$", {"missing": missing, "unknown": unknown})
    if value["schema_version"] != PROFILE_SCHEMA:
        _fail(f"schema_version must be {PROFILE_SCHEMA}", "schema_version")
    for field, lower, upper in (
        ("fit_stride_px", 1, 8),
        ("max_planes", 1, 6),
        ("ransac_iterations", 16, 2000),
        ("ransac_seed", 0, 2**31 - 1),
        ("min_ground_pixels", 1, 640 * 360),
        ("min_support_pixels", 1, 640 * 360),
        ("min_visible_span_px", 1, 640),
    ):
        if type(value[field]) is not int or not lower <= value[field] <= upper:
            _fail(f"expected integer in {lower}..{upper}", field)
    for field, lower, upper in (
        ("plane_residual_threshold_mm", 1.0, 200.0),
        ("max_horizontal_tilt_deg", 0.1, 45.0),
        ("ground_abs_height_gate_mm", 1.0, 300.0),
        ("min_support_height_mm", 0.0, 3000.0),
        ("max_support_height_mm", 1.0, 5000.0),
        ("min_depth_m", 0.01, 20.0),
        ("max_depth_m", 0.02, 30.0),
    ):
        _finite_number(value[field], field, minimum=lower, maximum=upper)
    if value["min_support_height_mm"] >= value["max_support_height_mm"]:
        _fail("support height range is empty", "min_support_height_mm")
    if value["min_depth_m"] >= value["max_depth_m"]:
        _fail("depth range is empty", "min_depth_m")
    if value["largest_component_only"] is not True:
        _fail("largest_component_only must be true", "largest_component_only")
    if value["depth_semantics"] != "axial_z":
        _fail("formal depth semantics must be axial_z", "depth_semantics")
    if value["selection_status"] != "formal_stage2_stage3a":
        _fail("formal selection status mismatch", "selection_status")
    return value


def bind_stage7d_profiles(
    stage7d_profile_path: Path,
    authority_dir: Path | None = None,
) -> Stage7DContext:
    authority = load_formal_geometry_authority(authority_dir)
    profile = validate_stage7d_profile(
        load_strict_json(stage7d_profile_path, code="E_STAGE7D_PROFILE")
    )
    provenance = authority.provenance()
    payloads = provenance["payloads"]
    return Stage7DContext(
        camera_model=authority.camera_model,
        gravity_up_camera=authority.gravity_up_camera,
        camera_height_mm=authority.camera_height_mm,
        profile=profile,
        authority=authority,
        camera_summary={
            "authority": "STAGE2",
            "profile_id": provenance["profile_id"],
            "sha256": payloads["stage2"]["promoted_sha256"],
        },
        camera_world_summary={
            "authority": "STAGE3A_GRAVITY",
            "full_world_rotation_authority": False,
            "camera_height_mm": authority.camera_height_mm,
            "sha256": payloads["stage3a"]["promoted_sha256"],
        },
        camera_tof_summary={
            "authority": "STAGE2",
            "range_semantics": "axial_z",
            "zone_order": "raw_index_0_to_63_identity",
            "sha256": payloads["stage2"]["promoted_sha256"],
        },
    )


def _require_keys(value: dict[str, Any], required: set[str], field: str) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise ScanContractError(
            "E_STAGE7D_OUTPUT_SCHEMA", "required fields are missing", field, missing
        )


def validate_horizontal_planes_document(value: dict[str, Any]) -> dict[str, Any]:
    _require_keys(
        value,
        {
            "schema_version", "scan_id", "frame_id", "depth_hypothesis",
            "depth_unit", "world_geometry_unit", "camera_profile",
            "camera_world_profile", "camera_tof_profile", "world_frame",
            "world_height_statistics", "planes", "rejected_candidates", "warnings",
        },
        "$",
    )
    if value["schema_version"] != HORIZONTAL_PLANES_SCHEMA:
        raise ScanContractError(
            "E_STAGE7D_OUTPUT_SCHEMA", "wrong schema_version", "schema_version"
        )
    if value["depth_hypothesis"] != "axial_z":
        raise ScanContractError(
            "E_STAGE7D_OUTPUT_SCHEMA", "formal depth must be axial_z", "depth_hypothesis"
        )
    if value["depth_unit"] != "m" or value["world_geometry_unit"] != "mm":
        raise ScanContractError(
            "E_STAGE7D_OUTPUT_SCHEMA", "unit contract mismatch", "depth_unit"
        )
    for plane in value["planes"]:
        _require_keys(
            plane,
            {
                "plane_id", "label", "normal_world", "median_height_mm",
                "plane_offset_mm", "residual_rms_mm", "residual_p95_mm",
                "residual_max_mm", "pixel_count", "bbox_xyxy", "contour_xy",
                "confidence_diagnostics",
            },
            "planes[]",
        )
        if plane["label"] not in {"ground", "support_surface"}:
            raise ScanContractError(
                "E_STAGE7D_OUTPUT_SCHEMA", "invalid plane label", "planes[].label"
            )
    json.dumps(value, allow_nan=False)
    return value


def validate_stage7d_summary(value: dict[str, Any]) -> dict[str, Any]:
    _require_keys(
        value,
        {
            "schema_version", "scan_id", "status", "selection_status",
            "absolute_geometry_valid", "profiles", "frames", "warnings",
        },
        "$",
    )
    if value["schema_version"] != SUMMARY_SCHEMA:
        raise ScanContractError(
            "E_STAGE7D_OUTPUT_SCHEMA", "wrong summary schema_version", "schema_version"
        )
    if value["selection_status"] != "formal_stage2_stage3a":
        raise ScanContractError(
            "E_STAGE7D_OUTPUT_SCHEMA", "formal selection mismatch", "selection_status"
        )
    if value["absolute_geometry_valid"] is not True:
        raise ScanContractError(
            "E_STAGE7D_OUTPUT_SCHEMA", "formal absolute world-Z must be valid",
            "absolute_geometry_valid"
        )
    json.dumps(value, allow_nan=False)
    return value
