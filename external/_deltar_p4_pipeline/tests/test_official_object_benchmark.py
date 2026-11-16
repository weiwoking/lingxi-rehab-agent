import numpy as np

from deltar_p4_pipeline.official_object_benchmark import gt_reference, match_detect_segment, pack_mask, region_statistics, unpack_mask


def test_packbits_mask_roundtrip():
    mask = np.zeros((480, 640), dtype=bool)
    mask[30:100, 40:90] = True
    assert np.array_equal(unpack_mask(pack_mask(mask)), mask)


def test_matching_requires_same_class_and_iou():
    det = [{"instance_id": 0, "class_name": "chair", "bbox_xyxy": [0, 0, 100, 100]}]
    seg = [{"instance_id": 0, "class_name": "chair", "bbox_xyxy": [5, 5, 95, 95], "mask_key": "m"}]
    masks = {"m": np.ones((480, 640), dtype=bool)}
    matched, unmatched = match_detect_segment(det, seg, masks)
    assert len(matched) == 1 and not unmatched


def test_gt_reference_uses_eroded_three_pixel_mad_median():
    depth = np.full((480, 640), 2.0, dtype=np.float32)
    mask = np.zeros_like(depth, dtype=bool)
    mask[20:100, 30:110] = True
    reference = gt_reference(depth, mask)
    assert reference["gt_distance_m"] == 2.0
    assert reference["primary_region"] == "segment_eroded_3px"


def test_bbox_contamination_changes_median():
    depth = np.full((20, 20), 4.0, dtype=np.float32)
    depth[5:15, 5:15] = 2.0
    mask = np.zeros_like(depth, dtype=bool)
    mask[5:15, 5:15] = True
    assert region_statistics(depth, mask)["median_m"] == 2.0
    assert region_statistics(depth, np.ones_like(mask))["median_m"] == 4.0
