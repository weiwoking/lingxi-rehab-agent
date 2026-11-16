import copy
import json
from pathlib import Path

import numpy as np
import pytest

from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.geometry_quality import compute_absolute_geometry_valid
from deltar_p4_pipeline.point_cloud import build_point_cloud
from deltar_p4_pipeline.stage4_validation import select_canvas_geometry
from deltar_p4_pipeline.synthetic_geometry import rectangle_depth, synthetic_calibration
from scripts.run_stage4_1_hardening import run_unit_equivalence

ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads((ROOT / "geometry_profile.json").read_text())


def _canvas_geometry(distance=2.0):
    return {"distance_m": distance, "object_height_m": 1.0, "bottom_height_m": 0.0, "point_count": 100, "warnings": []}


@pytest.mark.parametrize(
    "direct,letterbox,agreement,expected",
    [
        (True, True, "agree", "agreed"),
        (True, True, "disagree", "disagreed"),
        (True, False, "rejected_candidate", "direct_only"),
        (False, True, "rejected_candidate", "letterbox_only"),
        (False, False, "rejected_candidate", "both_rejected"),
    ],
)
def test_canvas_selection_states(direct, letterbox, agreement, expected):
    results = {
        "direct_640x360": {"status": "success", "geometry": _canvas_geometry()} if direct else {"status": "rejected", "error": {"error_code": "E_TEST"}},
        "letterbox_640x480": {"status": "success", "geometry": _canvas_geometry(2.5 if agreement == "disagree" else 2.0)} if letterbox else {"status": "rejected", "error": {"error_code": "E_TEST"}},
    }
    selection = select_canvas_geometry(results, {"agreement_status": agreement})
    assert selection["canvas_selection_status"] == expected
    if expected == "disagreed":
        assert selection["selected_geometry"] is None
        assert selection["selection_error"]["error_code"] == "E_GEOMETRY_CANVAS_DISAGREEMENT"
    if expected in {"direct_only", "letterbox_only"}:
        assert selection["single_canvas_only"] and not selection["automatic_geometry_valid"]


@pytest.mark.parametrize(
    "calibration,semantics,selection,automatic,critical,expected",
    [
        ("synthetic", "PROVEN", "agreed", True, [], False),
        ("unverified", "PROVEN", "agreed", True, [], False),
        ("measured", "PLAUSIBLE_BUT_UNPROVEN", "agreed", True, [], False),
        ("measured", "PROVEN", "disagreed", False, [], False),
        ("measured", "PROVEN", "direct_only", False, [], False),
        ("measured", "PROVEN", "agreed", True, [], True),
        ("measured", "PROVEN", "agreed", True, ["E_TEST"], False),
    ],
)
def test_absolute_geometry_valid_fail_closed(calibration, semantics, selection, automatic, critical, expected):
    assert compute_absolute_geometry_valid(
        calibration_status=calibration,
        depth_semantics_status=semantics,
        selected_geometry_status="success",
        canvas_selection_status=selection,
        automatic_geometry_valid=automatic,
        critical_geometry_rejections=critical,
    ) is expected


def test_depth_units_are_equivalent_before_all_filters():
    assert run_unit_equivalence(PROFILE)["passed"]


@pytest.mark.parametrize("scale", [True, 0.0, -1.0, float("nan"), float("inf")])
def test_nonpositive_or_nonfinite_scale_is_depth_scale_error(scale):
    calibration = synthetic_calibration()
    calibration["depth"]["scale_to_meter"] = scale
    with pytest.raises(PipelineError) as caught:
        build_point_cloud(np.ones((360, 640)), np.ones((360, 640), bool), calibration, PROFILE)
    assert caught.value.code == "E_DEPTH_SCALE"


def test_extreme_scale_overflow_is_depth_scale_error():
    calibration = synthetic_calibration()
    calibration["depth"]["scale_to_meter"] = 1e308
    with pytest.raises(PipelineError) as caught:
        build_point_cloud(np.full((360, 640), 2.0), np.ones((360, 640), bool), calibration, PROFILE)
    assert caught.value.code == "E_DEPTH_SCALE"


def test_thresholds_remain_meter_values_for_millimeter_input():
    calibration = synthetic_calibration()
    depth_m, mask, _ = rectangle_depth(calibration, distance_m=2.5, bottom_m=0.1, height_m=0.9)
    calibration_mm = copy.deepcopy(calibration)
    calibration_mm["depth"]["scale_to_meter"] = 0.001
    meter = build_point_cloud(depth_m, mask, calibration, PROFILE)
    millimeter = build_point_cloud(depth_m * 1000.0, mask, calibration_mm, PROFILE)
    assert np.array_equal(meter["pixel_uv"], millimeter["pixel_uv"])
    assert meter["filter_metrics"]["gate_half_width_m"] == pytest.approx(millimeter["filter_metrics"]["gate_half_width_m"], abs=1e-12)
