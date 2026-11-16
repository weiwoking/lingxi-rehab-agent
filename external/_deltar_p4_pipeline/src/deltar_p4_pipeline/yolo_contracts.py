from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .errors import ErrorCode, PipelineError

SCHEMA_VERSION = "deltar_stage3_yolo.v1"
SOURCE_SHAPE = (360, 640)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clip_bbox(bbox: list[float] | tuple[float, ...], width: int = 640, height: int = 360) -> list[float]:
    if len(bbox) != 4 or not np.isfinite(np.asarray(bbox, dtype=float)).all():
        raise PipelineError(ErrorCode.YOLO_OUTPUT_SCHEMA, "bbox_xyxy must contain four finite numbers", "bbox_xyxy")
    x1, y1, x2, y2 = (float(v) for v in bbox)
    clipped = [max(0.0, min(float(width), x1)), max(0.0, min(float(height), y1)),
               max(0.0, min(float(width), x2)), max(0.0, min(float(height), y2))]
    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
        raise PipelineError(ErrorCode.YOLO_OUTPUT_SCHEMA, "bbox is empty after clipping", "bbox_xyxy")
    return clipped


def bbox_to_slices(bbox: list[float] | tuple[float, ...], width: int = 640, height: int = 360) -> tuple[slice, slice]:
    x1, y1, x2, y2 = clip_bbox(bbox, width, height)
    ix1, iy1 = int(np.floor(x1)), int(np.floor(y1))
    ix2, iy2 = int(np.ceil(x2)), int(np.ceil(y2))
    return slice(max(0, iy1), min(height, iy2)), slice(max(0, ix1), min(width, ix2))


def resize_mask_nearest(mask: np.ndarray, shape: tuple[int, int] = SOURCE_SHAPE) -> np.ndarray:
    arr = np.asarray(mask)
    if arr.ndim != 2:
        raise PipelineError(ErrorCode.YOLO_MASK_SHAPE, "mask must be two-dimensional", "mask")
    if arr.shape != shape:
        arr = np.asarray(Image.fromarray(arr.astype(np.uint8), mode="L").resize((shape[1], shape[0]), Image.Resampling.NEAREST))
    return arr.astype(bool, copy=False)


def validate_mask(mask: np.ndarray, bbox: list[float] | tuple[float, ...], shape: tuple[int, int] = SOURCE_SHAPE) -> np.ndarray:
    arr = resize_mask_nearest(mask, shape)
    if arr.shape != shape:
        raise PipelineError(ErrorCode.YOLO_MASK_SHAPE, f"mask must have shape {shape}", "mask")
    bounded = np.zeros(shape, dtype=bool)
    ys, xs = bbox_to_slices(bbox, shape[1], shape[0])
    bounded[ys, xs] = arr[ys, xs]
    if not bounded.any():
        raise PipelineError(ErrorCode.YOLO_MASK_EMPTY, "instance mask is empty inside its bbox", "mask")
    return bounded


def validate_yolo_document(document: dict[str, Any], masks: dict[str, np.ndarray] | None = None) -> dict[str, Any]:
    if document.get("schema_version") != SCHEMA_VERSION:
        raise PipelineError(ErrorCode.YOLO_OUTPUT_SCHEMA, "unsupported YOLO schema_version", "schema_version")
    image = document.get("image")
    if not isinstance(image, dict) or (image.get("width"), image.get("height")) not in {(640, 360), (640, 480)}:
        raise PipelineError(ErrorCode.YOLO_OUTPUT_SCHEMA, "YOLO output must use an approved 640x360 or official 640x480 source canvas", "image")
    source_shape = (int(image["height"]), int(image["width"]))
    model = document.get("model")
    if not isinstance(model, dict) or model.get("task") not in {"detect", "segment"}:
        raise PipelineError(ErrorCode.YOLO_TASK, "YOLO task must be detect or segment", "model.task")
    instances = document.get("instances")
    if not isinstance(instances, list):
        raise PipelineError(ErrorCode.YOLO_OUTPUT_SCHEMA, "instances must be a list", "instances")
    seen: set[int] = set()
    for index, instance in enumerate(instances):
        if not isinstance(instance, dict) or type(instance.get("instance_id")) is not int:
            raise PipelineError(ErrorCode.YOLO_OUTPUT_SCHEMA, "invalid instance_id", f"instances[{index}]")
        instance_id = instance["instance_id"]
        if instance_id in seen:
            raise PipelineError(ErrorCode.YOLO_OUTPUT_SCHEMA, "duplicate instance_id", f"instances[{index}].instance_id")
        seen.add(instance_id)
        instance["bbox_xyxy"] = clip_bbox(instance.get("bbox_xyxy", []), source_shape[1], source_shape[0])
        confidence = instance.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
            raise PipelineError(ErrorCode.YOLO_OUTPUT_SCHEMA, "confidence must be in [0,1]", f"instances[{index}].confidence")
        key = instance.get("mask_key")
        if model["task"] == "detect" and key is not None:
            raise PipelineError(ErrorCode.YOLO_OUTPUT_SCHEMA, "detection instance cannot have mask_key", f"instances[{index}].mask_key")
        if model["task"] == "segment":
            if not isinstance(key, str) or masks is None or key not in masks:
                raise PipelineError(ErrorCode.YOLO_OUTPUT_SCHEMA, "segmentation instance mask_key is missing", f"instances[{index}].mask_key")
            masks[key] = validate_mask(masks[key], instance["bbox_xyxy"], source_shape)
    return document


def load_yolo_output(json_path: Path, npz_path: Path | None = None) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    try:
        document = json.loads(json_path.read_text(encoding="utf-8"))
        masks: dict[str, np.ndarray] = {}
        if npz_path is not None:
            with np.load(npz_path, allow_pickle=False) as archive:
                masks = {key: archive[key] for key in archive.files}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PipelineError(ErrorCode.YOLO_OUTPUT_SCHEMA, f"failed to read YOLO output: {exc}", str(json_path)) from exc
    return validate_yolo_document(document, masks), masks
