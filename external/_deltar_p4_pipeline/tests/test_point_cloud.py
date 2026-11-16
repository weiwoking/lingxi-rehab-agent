import json
from pathlib import Path

import numpy as np
import pytest

from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.point_cloud import FILTERS, bbox_mask, build_point_cloud, erode_mask
from deltar_p4_pipeline.synthetic_geometry import rectangle_depth, synthetic_calibration

ROOT = Path(__file__).resolve().parents[1]


def profile():
    return json.loads((ROOT / "geometry_profile.json").read_text())


def test_all_five_filter_paths_produce_points():
    cal = synthetic_calibration(); depth, mask, _ = rectangle_depth(cal, distance_m=2.5, bottom_m=0, height_m=0.8)
    for method in FILTERS:
        result = build_point_cloud(depth, mask, cal, profile(), stage3_surface_depth_m=2.5, point_filter=method)
        assert result["points_camera"].shape[1] == 3


def test_mask_erosion_removes_rim():
    mask = np.ones((20, 20), dtype=bool)
    assert erode_mask(mask, 3).sum() == 14 * 14


def test_bbox_clips_and_touches_border():
    mask = bbox_mask((360, 640), [-10, -10, 20, 20])
    assert mask.sum() == 400 and mask[0, 0]


def test_wrong_depth_shape_rejected():
    with pytest.raises(PipelineError):
        build_point_cloud(np.ones((10, 10)), np.ones((10, 10), bool), synthetic_calibration(), profile())


def test_too_few_points_rejected():
    depth = np.zeros((360, 640)); mask = np.zeros_like(depth, bool); depth[1, 1] = 2; mask[1, 1] = True
    with pytest.raises(PipelineError):
        build_point_cloud(depth, mask, synthetic_calibration(), profile())


def test_unstable_multimodal_bbox_rejected():
    depth = np.zeros((360, 640)); mask = np.zeros_like(depth, bool); mask[100:120, 100:120] = True
    depth[100:110, 100:120] = 2.0; depth[110:120, 100:120] = 4.0
    p = profile(); p["bbox_peak_min_support"] = 0.6
    with pytest.raises(PipelineError):
        build_point_cloud(depth, mask, synthetic_calibration(), p, source_type="detection_fallback")
