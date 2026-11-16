import numpy as np

from deltar_p4_pipeline.stage5_matching import bbox_iou, mask_iou, match_objects


def test_iou_and_class_matching():
    assert bbox_iou([0,0,10,10],[0,0,10,10])==1
    mask=np.eye(3,dtype=bool);assert mask_iou(mask,mask)==1
    rows=match_objects([{"class_name":"chair","bbox_xyxy":[0,0,10,10]}],[{"class_name":"chair","bbox_xyxy":[1,1,9,9]}])
    assert rows[0]["match_status"]=="matched"


def test_unmatched_counts_fp_fn():
    rows=match_objects([{"class_name":"cup","bbox_xyxy":[0,0,2,2]}],[{"class_name":"chair","bbox_xyxy":[0,0,2,2]}])
    assert {r["match_status"] for r in rows}=={"fp","fn"}
