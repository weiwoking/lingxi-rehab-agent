from __future__ import annotations

import unittest
from pathlib import Path
import numpy as np

from deltar_p4_pipeline.depth_statistics import compute_statistics
from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.object_depth import choose_candidates, evaluate_object_depth
from deltar_p4_pipeline.object_matching import bbox_iou, greedy_match
from deltar_p4_pipeline.object_regions import bbox_center_mask, bbox_mask, binary_erosion, boundary_band, build_regions
from deltar_p4_pipeline.stage3_validation import depth_metrics
from deltar_p4_pipeline.yolo_contracts import clip_bbox, resize_mask_nearest, validate_mask


class Stage3CoreCompatibilityTests(unittest.TestCase):
    def test_bbox_clip(self): self.assertEqual(clip_bbox([-1,0,641,360]),[0.0,0.0,640.0,360.0])
    def test_bbox_half_open(self): self.assertEqual(int(bbox_mask([0,0,3,2]).sum()),6)
    def test_mask_nearest(self): self.assertEqual(resize_mask_nearest(np.ones((2,2))).shape,(360,640))
    def test_empty_mask(self):
        with self.assertRaises(PipelineError): validate_mask(np.zeros((360,640)),[0,0,10,10])
    def test_iou(self): self.assertAlmostEqual(bbox_iou([0,0,10,10],[5,0,15,10]),1/3)
    def test_match(self):
        d=[{"instance_id":0,"class_name":"cup","bbox_xyxy":[0,0,10,10]}]; s=[{"instance_id":1,"class_name":"cup","bbox_xyxy":[0,0,10,10]}]
        self.assertEqual(greedy_match(d,s)[0]["match_status"],"matched")
    def test_center(self): self.assertEqual(int(bbox_center_mask([0,0,100,100]).sum()),2500)
    def test_erosion(self):
        m=np.ones((5,5),bool); self.assertEqual(int(binary_erosion(m).sum()),9)
    def test_boundary(self):
        m=np.ones((5,5),bool); self.assertEqual(int(boundary_band(m).sum()),16)
    def test_flat_stats(self): self.assertEqual(compute_statistics(np.ones(20))["median"],1)
    def test_bimodal(self): self.assertTrue(compute_statistics(np.r_[np.ones(10),np.full(20,3)])["multimodal"])
    def test_outlier(self): self.assertEqual(compute_statistics(np.r_[np.ones(20),100])["mad_filtered_median"],1)
    def test_small_region(self): self.assertEqual(build_regions([0,0,1,1],np.ones((360,640)))["bbox_full"]["status"],"rejected")
    def test_regions(self): self.assertIn("bbox_depth_cluster",build_regions([0,0,20,20],np.ones((360,640))))
    def test_object_depth(self):
        d=np.ones((360,640)); r,_=evaluate_object_depth([0,0,20,20],d); self.assertEqual(choose_candidates(r,{"region_method":"bbox_depth_cluster","statistic_method":"trimmed_mean"})["status"],"valid")
    def test_metrics(self):
        a=np.ones((3,3)); self.assertEqual(depth_metrics(a+1,a,np.ones((3,3),bool))["mae_m"],1)
    def test_worker_batch_names(self):
        source=(Path(__file__).parents[1]/"workers"/"yolo_worker.py").read_text(encoding="utf-8")
        self.assertIn('f"{batch_index:03d}_{source_path.stem}"',source)


if __name__ == "__main__": unittest.main()
