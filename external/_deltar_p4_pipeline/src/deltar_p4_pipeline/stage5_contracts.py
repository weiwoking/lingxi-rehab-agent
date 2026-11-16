from __future__ import annotations

from typing import Any

import numpy as np

from .errors import ErrorCode, PipelineError

SCENE_SCHEMA = "deltar_stage5_scene.v1"
TRUTH_SCHEMA = "deltar_stage5_truth.v1"
TOF_SCHEMA = "rehab_tof_raw.v1"


def require_no_gt_leakage(record: dict[str, Any]) -> None:
    if record.get("gt_used_for_inference") is not False:
        raise PipelineError(ErrorCode.STAGE5_GT_LEAKAGE, "inference records must explicitly disable GT", "gt_used_for_inference")
    forbidden = {"ground_truth_depth", "ground_truth_masks", "truth_objects", "gt_mask", "gt_depth"}
    leaked = sorted(forbidden.intersection(record.get("inference_inputs", {})))
    if leaked:
        raise PipelineError(ErrorCode.STAGE5_GT_LEAKAGE, "GT field present in inference inputs", details=leaked)


def validate_scene(scene: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(scene, dict) or scene.get("schema_version") != SCENE_SCHEMA:
        raise PipelineError(ErrorCode.STAGE5_SCENE_SCHEMA, f"schema_version must be {SCENE_SCHEMA}")
    camera = scene.get("camera", {})
    required = ("fx", "fy", "cx", "cy", "T_ground_from_camera")
    if any(key not in camera for key in required) or camera.get("width") != 640 or camera.get("height") != 360:
        raise PipelineError(ErrorCode.STAGE5_CAMERA_MODEL, "camera must be an explicit 640x360 model")
    if float(camera["fx"]) <= 0 or float(camera["fy"]) <= 0:
        raise PipelineError(ErrorCode.STAGE5_CAMERA_MODEL, "focal lengths must be positive")
    transform = np.asarray(camera["T_ground_from_camera"], dtype=np.float64)
    if transform.shape != (4, 4) or not np.all(np.isfinite(transform)):
        raise PipelineError(ErrorCode.STAGE5_CAMERA_MODEL, "T_ground_from_camera must be finite 4x4")
    objects = scene.get("objects")
    if not isinstance(objects, list) or not objects:
        raise PipelineError(ErrorCode.STAGE5_TRUTH_MISSING, "scene must contain truth objects")
    ids = [item.get("object_id") for item in objects]
    if any(not value for value in ids) or len(set(ids)) != len(ids):
        raise PipelineError(ErrorCode.STAGE5_SCENE_SCHEMA, "object_id values must be unique and non-empty")
    for item in objects:
        if item.get("primitive") not in {"box", "billboard", "slanted_plane"}:
            raise PipelineError(ErrorCode.STAGE5_SCENE_SCHEMA, "unsupported primitive", details=item.get("primitive"))
        if float(item.get("height_m", 0)) <= 0 or float(item.get("width_m", 0)) <= 0:
            raise PipelineError(ErrorCode.STAGE5_TRUTH_MISSING, "objects require positive physical extent", details=item.get("object_id"))
    return scene


def validate_tof_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != TOF_SCHEMA:
        raise PipelineError(ErrorCode.STAGE5_TOF_SIMULATION, f"schema_version must be {TOF_SCHEMA}")
    for field in ("distance_mm", "range_sigma_mm", "target_status", "nb_target_detected"):
        if not isinstance(payload.get(field), list) or len(payload[field]) != 64:
            raise PipelineError(ErrorCode.STAGE5_TOF_SIMULATION, f"{field} must contain 64 values", field)
    if any(float(v) < 0 for v in payload["range_sigma_mm"]):
        raise PipelineError(ErrorCode.STAGE5_TOF_SIMULATION, "sigma must be non-negative")
    return payload
