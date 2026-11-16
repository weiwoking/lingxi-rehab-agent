from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from deltar_p4_pipeline.depth_statistics import compute_statistics, find_peaks, select_peak_mask
from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.object_depth import choose_candidates, evaluate_object_depth
from deltar_p4_pipeline.object_matching import bbox_iou, greedy_match
from deltar_p4_pipeline.object_regions import bbox_center_mask, bbox_mask, binary_erosion, boundary_band, build_regions
from deltar_p4_pipeline.stage3_validation import boundary_metrics, depth_metrics, gt_references
from deltar_p4_pipeline.yolo_contracts import clip_bbox, resize_mask_nearest, validate_mask, validate_yolo_document


def doc(task="detect"):
    return {"schema_version":"deltar_stage3_yolo.v1", "model":{"task":task}, "image":{"width":640,"height":360},
            "instances":[{"instance_id":0,"class_id":39,"class_name":"bottle","confidence":0.8,
                          "bbox_xyxy":[-2,10,650,200],"bbox_normalized":[0,0,1,1],"mask_key":"instance_000" if task=="segment" else None}], "warnings":[]}


def test_bbox_clip_and_half_open():
    assert clip_bbox([-2, 10, 650, 200]) == [0.0, 10.0, 640.0, 200.0]
    assert bbox_mask([0,0,2,2]).sum() == 4


def test_yolo_detection_schema():
    assert validate_yolo_document(doc())["instances"][0]["bbox_xyxy"][2] == 640


def test_yolo_segment_schema_and_bbox_limit():
    mask = np.ones((180,320), dtype=bool)
    masks = {"instance_000":mask}
    validate_yolo_document(doc("segment"), masks)
    assert masks["instance_000"].shape == (360,640)
    assert not masks["instance_000"][300].any()


def test_yolo_empty_mask_rejected():
    with pytest.raises(PipelineError): validate_mask(np.zeros((360,640),bool), [0,0,10,10])


def test_nearest_mask_resize_stays_boolean():
    output = resize_mask_nearest(np.array([[0,1],[1,0]],np.uint8))
    assert output.dtype == bool and set(np.unique(output)) == {False,True}


def test_iou_and_class_greedy_match():
    assert bbox_iou([0,0,10,10],[5,0,15,10]) == pytest.approx(1/3)
    d=[{"instance_id":1,"class_name":"cup","bbox_xyxy":[0,0,10,10]}]
    s=[{"instance_id":2,"class_name":"cup","bbox_xyxy":[0,0,10,10]}]
    assert greedy_match(d,s)[0]["match_status"] == "matched"


def test_unmatched_below_threshold():
    d=[{"instance_id":1,"class_name":"cup","bbox_xyxy":[0,0,10,10]}]
    s=[{"instance_id":2,"class_name":"cup","bbox_xyxy":[9,9,20,20]}]
    assert {x["match_status"] for x in greedy_match(d,s)} == {"unmatched_detect","unmatched_segment"}


def test_center_region():
    mask=bbox_center_mask([100,100,200,200],0.5)
    assert mask.sum()==2500 and mask[125,125] and not mask[100,100]


def test_binary_erosion_and_boundary():
    mask=np.zeros((7,7),bool); mask[1:6,1:6]=True
    assert binary_erosion(mask,1).sum()==9
    assert boundary_band(mask,1).sum()==16


def test_statistics_flat_mad_zero():
    stats=compute_statistics(np.full(100,2.0))
    assert stats["mad_m"]==0 and stats["median"]==2 and stats["mad_filtered_median"]==2


def test_statistics_outlier_robustness():
    values=np.r_[np.full(90,2.0),np.full(10,9.0)]
    stats=compute_statistics(values)
    assert stats["median"]==2 and stats["trimmed_mean"]==pytest.approx(2.0)


def test_two_peaks_and_nearest_stable():
    values=np.r_[np.full(60,3.0),np.full(40,1.0)]
    stats=compute_statistics(values)
    assert stats["multimodal"] and stats["dominant_peak_m"]==3 and stats["nearest_stable_surface"]==1


def test_nearest_ignores_tiny_min_cluster():
    values=np.r_[np.full(95,3.0),np.full(5,0.2)]
    assert compute_statistics(values,min_pixels=8)["nearest_stable_surface"]==3


def test_peak_mask_selects_dominant():
    depth=np.full((360,640),3.0,np.float32); region=np.zeros_like(depth,bool); region[:10,:10]=True; depth[:4,:10]=1
    selected,meta=select_peak_mask(depth,region)
    assert selected.sum()==60 and meta["selected_peak_m"]==3


def test_build_regions_with_segment_and_clusters():
    depth=np.full((360,640),2.0,np.float32); seg=np.zeros_like(depth,bool); seg[10:30,10:30]=True
    regions=build_regions([5,5,40,40],depth,segment_mask=seg)
    assert "segment_eroded_3px_depth_cluster" in regions and regions["segment_mask"]["valid_pixel_count"]==400


def test_build_regions_too_small_rejected():
    depth=np.ones((360,640),np.float32)
    assert build_regions([0,0,1,1],depth)["bbox_full"]["status"]=="rejected"


def test_object_depth_contract_and_choice():
    depth=np.full((360,640),2.0,np.float32); seg=np.zeros_like(depth,bool); seg[10:40,10:40]=True
    results,masks=evaluate_object_depth([5,5,50,50],depth,segment_mask=seg)
    chosen=choose_candidates(results,{"region_method":"segment_eroded_3px","statistic_method":"trimmed_mean"})
    assert chosen["status"]=="valid" and chosen["region_method"].startswith("segment") and masks


def test_depth_metrics_known_error():
    gt=np.ones((4,4),np.float32); pred=gt+0.5; region=np.ones((4,4),bool)
    metrics=depth_metrics(pred,gt,region)
    assert metrics["mae_m"]==pytest.approx(0.5) and metrics["rmse_m"]==pytest.approx(0.5)


def test_gt_references_and_boundary_metrics():
    gt=np.ones((20,20),np.float32); mask=np.zeros_like(gt,bool); mask[2:18,2:18]=True
    refs=gt_references(gt,mask); metrics=boundary_metrics(gt,gt,mask)
    assert refs["gt_mask_median_m"]==1 and metrics["boundary_interior_delta_m"]==0


def test_gt_shape_rejected():
    with pytest.raises(PipelineError): depth_metrics(np.ones((2,2)),np.ones((3,3)),np.ones((2,2),bool))


def test_worker_output_names_include_batch_index():
    source = (Path(__file__).parents[1] / "workers" / "yolo_worker.py").read_text(encoding="utf-8")
    assert 'f"{batch_index:03d}_{source_path.stem}"' in source
