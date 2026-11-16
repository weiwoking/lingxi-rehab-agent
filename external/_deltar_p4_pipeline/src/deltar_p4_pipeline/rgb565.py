from __future__ import annotations

from pathlib import Path

import numpy as np

from .errors import ErrorCode, PipelineError

WIDTH = 640
HEIGHT = 360
STRIDE_BYTES = 1280
PAYLOAD_BYTES = HEIGHT * STRIDE_BYTES
PIXEL_FORMAT = "RGB565_LE"


def _expand5(value: np.ndarray) -> np.ndarray:
    return ((value << 3) | (value >> 2)).astype(np.uint8)


def _expand6(value: np.ndarray) -> np.ndarray:
    return ((value << 2) | (value >> 4)).astype(np.uint8)


def decode_rgb565_le(payload: bytes, *, width: int = WIDTH, height: int = HEIGHT, stride: int = STRIDE_BYTES) -> np.ndarray:
    expected = height * stride
    if len(payload) != expected:
        raise PipelineError(ErrorCode.IMAGE_SIZE, f"expected {expected} bytes, got {len(payload)}", "image.rgb565")
    if stride != width * 2:
        raise PipelineError(ErrorCode.IMAGE_FORMAT, "only tightly packed RGB565_LE stride is supported", "image.stride")
    words = np.frombuffer(payload, dtype="<u2").reshape(height, width)
    red = _expand5((words >> 11) & 0x1F)
    green = _expand6((words >> 5) & 0x3F)
    blue = _expand5(words & 0x1F)
    return np.stack((red, green, blue), axis=-1)


def read_rgb565_le(path: Path) -> np.ndarray:
    if not path.is_file():
        raise PipelineError(ErrorCode.IMAGE_MISSING, "image.rgb565 is missing", str(path))
    return decode_rgb565_le(path.read_bytes())


def encode_rgb565_le(rgb: np.ndarray) -> bytes:
    arr = np.asarray(rgb)
    if arr.dtype != np.uint8 or arr.ndim != 3 or arr.shape[2] != 3:
        raise PipelineError(ErrorCode.TYPE, "RGB image must be HxWx3 uint8", "rgb")
    # Deterministic truncation to the most significant RGB565 bits.
    words = ((arr[..., 0].astype(np.uint16) >> 3) << 11)
    words |= ((arr[..., 1].astype(np.uint16) >> 2) << 5)
    words |= arr[..., 2].astype(np.uint16) >> 3
    return words.astype("<u2", copy=False).tobytes(order="C")


def roundtrip_metrics(rgb: np.ndarray) -> dict[str, object]:
    decoded = decode_rgb565_le(encode_rgb565_le(rgb), width=rgb.shape[1], height=rgb.shape[0], stride=rgb.shape[1] * 2)
    diff = np.abs(rgb.astype(np.int16) - decoded.astype(np.int16))
    mse = np.mean((rgb.astype(np.float64) - decoded.astype(np.float64)) ** 2, axis=(0, 1))
    # JSON has no portable Infinity value; null denotes the zero-MSE/infinite-PSNR case.
    psnr = [None if value == 0 else float(10 * np.log10((255.0**2) / value)) for value in mse]
    return {
        "mae_rgb": np.mean(diff, axis=(0, 1)).tolist(),
        "max_error_rgb": np.max(diff, axis=(0, 1)).tolist(),
        "psnr_rgb_db": psnr,
        "red_blue_not_swapped": bool(np.mean(diff[..., 0]) <= 8 and np.mean(diff[..., 2]) <= 8),
    }
