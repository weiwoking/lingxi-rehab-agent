from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from .errors import ErrorCode, PipelineError

CANVAS_WIDTH = 640
CANVAS_HEIGHT = 480


@dataclass(frozen=True)
class CanvasResult:
    rgb: np.ndarray
    valid_mask: np.ndarray
    source_to_canvas: np.ndarray
    canvas_to_source: np.ndarray
    valid_region: tuple[int, int, int, int]
    metadata: dict[str, Any]
    warnings: tuple[str, ...]


def _resize(rgb: np.ndarray, width: int, height: int) -> np.ndarray:
    return np.asarray(Image.fromarray(rgb, mode="RGB").resize((width, height), Image.Resampling.BILINEAR))


def _inverse(matrix: np.ndarray) -> np.ndarray:
    try:
        return np.linalg.inv(matrix)
    except np.linalg.LinAlgError as exc:
        raise PipelineError(ErrorCode.TRANSFORM_SINGULAR, "canvas transform is singular", "canvas") from exc


def apply_canvas(rgb: np.ndarray, strategy: str, fill_rgb: list[int] | tuple[int, int, int] = (0, 0, 0)) -> CanvasResult:
    if rgb.shape != (360, 640, 3) or rgb.dtype != np.uint8:
        raise PipelineError(ErrorCode.IMAGE_FORMAT, "canvas source must be 360x640 uint8 RGB", "image")
    fill = np.asarray(fill_rgb, dtype=np.uint8)
    if fill.shape != (3,):
        raise PipelineError(ErrorCode.TYPE, "fill_rgb must have three uint8-compatible values", "canvas.fill_rgb")

    if strategy == "letterbox_640x480":
        matrix = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 60.0], [0.0, 0.0, 1.0]])
        canvas = np.empty((480, 640, 3), dtype=np.uint8)
        canvas[:] = fill
        canvas[60:420] = rgb
        valid = np.zeros((480, 640), dtype=bool)
        valid[60:420] = True
        region = (60, 0, 420, 640)
        meta = {"top_padding": 60, "bottom_padding": 60, "scale_x": 1.0, "scale_y": 1.0, "lost_fov_ratio": 0.0}
        warnings: tuple[str, ...] = ()
    elif strategy == "resize_640x480":
        sy = 480.0 / 360.0
        matrix = np.array([[1.0, 0.0, 0.0], [0.0, sy, 0.0], [0.0, 0.0, 1.0]])
        canvas = _resize(rgb, 640, 480)
        valid = np.ones((480, 640), dtype=bool)
        region = (0, 0, 480, 640)
        meta = {"scale_x": 1.0, "scale_y": sy, "lost_fov_ratio": 0.0}
        warnings = ("GEOMETRIC_DISTORTION_NON_UNIFORM_SCALE",)
    elif strategy == "scale_to_fill_center_crop_640x480":
        theoretical_scale = 480.0 / 360.0
        scaled_width = int(round(640 * theoretical_scale))
        scaled_height = 480
        scale_x = scaled_width / 640.0
        scale_y = scaled_height / 360.0
        crop_left = (scaled_width - 640) // 2
        matrix = np.array([[scale_x, 0.0, -float(crop_left)], [0.0, scale_y, 0.0], [0.0, 0.0, 1.0]])
        scaled = _resize(rgb, scaled_width, scaled_height)
        canvas = scaled[:, crop_left : crop_left + 640]
        valid = np.ones((480, 640), dtype=bool)
        region = (0, 0, 480, 640)
        lost = 1.0 - 640.0 / scaled_width
        meta = {
            "theoretical_scale": theoretical_scale,
            "scale_x": scale_x,
            "scale_y": scale_y,
            "scaled_width": scaled_width,
            "scaled_height": scaled_height,
            "crop_left": crop_left,
            "crop_right": scaled_width - crop_left - 640,
            "lost_fov_ratio": lost,
        }
        warnings = ("HORIZONTAL_FIELD_OF_VIEW_CROPPED", "INTEGER_RASTER_SCALE_X_DIFFERS_SLIGHTLY_FROM_SCALE_Y")
    elif strategy == "direct_640x360":
        matrix = np.eye(3)
        canvas = rgb.copy()
        valid = np.ones((360, 640), dtype=bool)
        region = (0, 0, 360, 640)
        meta = {"formal_candidate": True, "scale_x": 1.0, "scale_y": 1.0, "lost_fov_ratio": 0.0}
        warnings = ()
    elif strategy == "direct_640x384_pad32":
        matrix = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 12.0], [0.0, 0.0, 1.0]])
        canvas = np.empty((384, 640, 3), dtype=np.uint8)
        canvas[:] = fill
        canvas[12:372] = rgb
        valid = np.zeros((384, 640), dtype=bool)
        valid[12:372] = True
        region = (12, 0, 372, 640)
        meta = {"probe_only": True, "top_padding": 12, "bottom_padding": 12, "lost_fov_ratio": 0.0}
        warnings = ("DIRECT_SIZE_PROBE_NOT_A_FROZEN_STRATEGY",)
    else:
        raise PipelineError(ErrorCode.PROFILE_UNSUPPORTED, f"unsupported canvas strategy: {strategy}", "canvas.strategy")
    return CanvasResult(canvas, valid, matrix, _inverse(matrix), region, meta, warnings)


def transform_intrinsics(intrinsics: dict[str, float], matrix: np.ndarray) -> dict[str, float]:
    fx, fy = float(intrinsics["fx"]), float(intrinsics["fy"])
    cx, cy = float(intrinsics["cx"]), float(intrinsics["cy"])
    return {
        "fx": float(matrix[0, 0] * fx),
        "fy": float(matrix[1, 1] * fy),
        "cx": float(matrix[0, 0] * cx + matrix[0, 2]),
        "cy": float(matrix[1, 1] * cy + matrix[1, 2]),
        "status": "TEST_INTRINSICS_NOT_REAL_CALIBRATION",
    }


def transform_fr(fr: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    arr = np.asarray(fr, dtype=np.float64)
    if arr.shape != (64, 4):
        raise PipelineError(ErrorCode.FR_SHAPE, "fr must have shape 64x4", "fr.values")
    out = np.empty_like(arr)
    for index, (sy, sx, ey, ex) in enumerate(arr):
        p0 = matrix @ np.array([sx, sy, 1.0])
        p1 = matrix @ np.array([ex, ey, 1.0])
        out[index] = [p0[1], p0[0], p1[1], p1[0]]
    return out


def regular_grid_source() -> np.ndarray:
    ys = np.linspace(0.0, 360.0, 9)
    xs = np.linspace(0.0, 640.0, 9)
    return np.asarray([[ys[row], xs[col], ys[row + 1], xs[col + 1]] for row in range(8) for col in range(8)], dtype=np.float64)


def map_depth_to_source(depth: np.ndarray, result: CanvasResult) -> tuple[np.ndarray, np.ndarray]:
    image = Image.fromarray(depth.astype(np.float32), mode="F")
    if depth.shape == (360, 640):
        return depth.astype(np.float32, copy=True), np.ones((360, 640), dtype=bool)
    # PIL affine output->input coefficients implement the recorded canvas->source relation in reverse.
    forward = result.source_to_canvas
    coeff = (float(forward[0, 0]), float(forward[0, 1]), float(forward[0, 2]), float(forward[1, 0]), float(forward[1, 1]), float(forward[1, 2]))
    mapped = np.asarray(image.transform((640, 360), Image.Transform.AFFINE, coeff, resample=Image.Resampling.BILINEAR), dtype=np.float32)
    mask_img = Image.fromarray((result.valid_mask.astype(np.uint8) * 255), mode="L")
    valid = np.asarray(mask_img.transform((640, 360), Image.Transform.AFFINE, coeff, resample=Image.Resampling.NEAREST)) > 0
    return mapped, valid
