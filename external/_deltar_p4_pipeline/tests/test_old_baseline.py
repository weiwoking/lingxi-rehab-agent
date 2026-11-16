import json
from pathlib import Path

from deltar_p4_pipeline.baseline_old_fusion import covered_zones, mad_filter, evaluate_old_detection

ROOT=Path(__file__).resolve().parents[1]


def test_covered_zone_fallback_and_mad():
    assert len(covered_zones([1,1,2,2]))==1
    assert 10000 not in mad_filter([1000,1001,999,10000])


def test_old_baseline_uses_tof_not_gt():
    parent=ROOT/"samples"/"stage5_analytic"/"F1_floor_boxes"
    if not parent.is_dir():return
    tof=json.loads((parent/"tof.json").read_text());cal=json.loads((parent/"calibration.json").read_text())
    result=evaluate_old_detection({"bbox_xyxy":[100,100,300,300]},tof,cal)
    assert result["status"] in {"valid","rejected"}
    if result["status"]=="valid":assert result["algorithm_provenance"]["gt_mask_used"] is False
