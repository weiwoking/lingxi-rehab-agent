import numpy as np

from .errors import VisionError


def decode_rgb565(
    raw: bytes,
    width: int,
    height: int,
    byte_order: str,
    capture_id: str,
) -> np.ndarray:
    expected = width * height * 2
    if len(raw) != expected:
        raise VisionError(
            code="INVALID_IMAGE_SIZE",
            message=f"Expected {expected} bytes, received {len(raw)}.",
            http_status=400,
            capture_id=capture_id,
        )

    if byte_order == "little_endian":
        dtype = np.dtype("<u2")
    elif byte_order == "big_endian":
        dtype = np.dtype(">u2")
    else:
        raise VisionError(
            code="IMAGE_DECODE_FAILED",
            message=f"Unsupported byte order: {byte_order}",
            http_status=500,
            capture_id=capture_id,
        )

    try:
        pixels = np.frombuffer(raw, dtype=dtype).reshape(height, width)
        red = (((pixels >> 11) & 0x1F) * 255 // 31).astype(np.uint8)
        green = (((pixels >> 5) & 0x3F) * 255 // 63).astype(np.uint8)
        blue = ((pixels & 0x1F) * 255 // 31).astype(np.uint8)
        return np.dstack((blue, green, red))
    except Exception as exc:
        raise VisionError(
            code="IMAGE_DECODE_FAILED",
            message=f"RGB565 decode failed: {exc}",
            http_status=400,
            capture_id=capture_id,
        ) from exc
