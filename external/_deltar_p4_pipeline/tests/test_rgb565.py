import numpy as np
import pytest

from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.rgb565 import decode_rgb565_le, encode_rgb565_le, roundtrip_metrics


def test_known_primary_vectors() -> None:
    words = np.asarray([0xF800, 0x07E0, 0x001F, 0xFFFF, 0x0000], dtype="<u2")
    decoded = decode_rgb565_le(words.tobytes(), width=5, height=1, stride=10)[0]
    assert decoded.tolist() == [[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 255], [0, 0, 0]]


def test_roundtrip_bounds_and_no_red_blue_swap() -> None:
    rgb = np.zeros((360, 640, 3), dtype=np.uint8)
    rgb[..., 0] = np.arange(640, dtype=np.uint16)[None, :] % 256
    rgb[..., 1] = np.arange(360, dtype=np.uint16)[:, None] % 256
    rgb[..., 2] = 173
    metrics = roundtrip_metrics(rgb)
    assert all(actual <= bound for actual, bound in zip(metrics["max_error_rgb"], [7, 3, 7]))
    assert metrics["red_blue_not_swapped"] is True


def test_encoder_is_little_endian() -> None:
    rgb = np.asarray([[[255, 0, 0]]], dtype=np.uint8)
    assert encode_rgb565_le(rgb) == b"\x00\xf8"


def test_wrong_size_rejected() -> None:
    with pytest.raises(PipelineError, match="expected") as caught:
        decode_rgb565_le(b"\0" * 4)
    assert caught.value.code == "E_IMAGE_SIZE"
