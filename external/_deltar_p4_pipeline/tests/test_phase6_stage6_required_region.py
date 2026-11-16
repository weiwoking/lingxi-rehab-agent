from __future__ import annotations

import numpy as np
import pytest

from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.stage6_formal import _record


def _instance(*, segmentation: bool, bbox: list[float] | None = None) -> dict[str, object]:
    return {
        "instance_id": 3,
        "class_id": 56,
        "class_name": "chair",
        "confidence": 0.8125,
        "bbox_xyxy": bbox or [31.25, 22.5, 411.75, 318.25],
        **({"mask_key": "instance_003"} if segmentation else {}),
    }


def _assert_exact(
    source: str,
    instance: dict[str, object],
    depths: dict[str, np.ndarray],
    mask: np.ndarray | None,
) -> None:
    generic = _record("frame_000007", source, instance, depths, mask)
    required = _record(
        "frame_000007", source, instance, depths, mask, required_region_only=True
    )
    assert required == generic


@pytest.mark.parametrize("seed", range(8))
def test_required_region_is_exact_for_randomized_valid_inputs(seed: int) -> None:
    rng = np.random.default_rng(20260810 + seed)
    direct = rng.uniform(0.35, 6.0, size=(360, 640)).astype(np.float32)
    letterbox = rng.uniform(0.35, 6.0, size=(360, 640)).astype(np.float32)
    direct[rng.random(direct.shape) < 0.03] = 0.0
    letterbox[rng.random(letterbox.shape) < 0.03] = np.nan
    depths = {"direct_640x360": direct, "letterbox_640x480": letterbox}
    x1 = int(rng.integers(0, 180))
    y1 = int(rng.integers(0, 100))
    x2 = int(rng.integers(400, 640))
    y2 = int(rng.integers(240, 360))
    bbox = [float(x1), float(y1), float(x2), float(y2)]
    _assert_exact("detection", _instance(segmentation=False, bbox=bbox), depths, None)
    mask = rng.random((360, 640)) > 0.63
    mask[y1:y2, x1:x2] = True
    _assert_exact("segmentation", _instance(segmentation=True, bbox=bbox), depths, mask)


@pytest.mark.parametrize(
    ("source", "depth_value", "bbox", "mask_kind"),
    [
        ("detection", 0.0, [10.0, 10.0, 200.0, 200.0], None),
        ("detection", 1.0, [10.0, 10.0, 11.0, 11.0], None),
        ("segmentation", 1.0, [10.0, 10.0, 200.0, 200.0], "empty"),
        ("segmentation", 1.0, [10.0, 10.0, 200.0, 200.0], "tiny"),
        ("segmentation", 2.5, [0.0, 0.0, 640.0, 360.0], "full"),
    ],
)
def test_required_region_is_exact_for_degenerate_and_rejected_inputs(
    source: str,
    depth_value: float,
    bbox: list[float],
    mask_kind: str | None,
) -> None:
    depth = np.full((360, 640), depth_value, dtype=np.float32)
    depths = {"direct_640x360": depth, "letterbox_640x480": depth.copy()}
    mask = None
    if mask_kind is not None:
        mask = np.zeros((360, 640), dtype=bool)
        if mask_kind == "tiny":
            mask[10:13, 10:13] = True
        elif mask_kind == "full":
            mask[:] = True
    _assert_exact(source, _instance(segmentation=source == "segmentation", bbox=bbox), depths, mask)


@pytest.mark.parametrize(
    ("source", "bad_depth", "bad_mask"),
    [
        ("detection", True, False),
        ("segmentation", True, False),
        ("segmentation", False, True),
    ],
)
def test_required_region_shape_errors_match_generic(
    source: str, bad_depth: bool, bad_mask: bool
) -> None:
    shape = (12, 13) if bad_depth else (360, 640)
    depth = np.ones(shape, dtype=np.float32)
    depths = {"direct_640x360": depth, "letterbox_640x480": depth.copy()}
    mask = np.ones((12, 13) if bad_mask else (360, 640), dtype=bool) if source == "segmentation" else None
    instance = _instance(segmentation=source == "segmentation")
    errors = []
    for required in (False, True):
        with pytest.raises(PipelineError) as caught:
            _record(
                "frame_000007",
                source,
                instance,
                depths,
                mask,
                required_region_only=required,
            )
        errors.append(caught.value.as_dict())
    assert errors[1] == errors[0]


def test_required_region_missing_segmentation_mask_matches_generic() -> None:
    depth = np.ones((360, 640), dtype=np.float32)
    depths = {"direct_640x360": depth, "letterbox_640x480": depth.copy()}
    _assert_exact("segmentation", _instance(segmentation=True), depths, None)
