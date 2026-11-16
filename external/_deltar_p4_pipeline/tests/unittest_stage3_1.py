from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from deltar_p4_pipeline.object_depth import evaluate_object_depth
from deltar_p4_pipeline.stage3_1 import frozen_candidate, frozen_canvas_delta, same_region_same_statistic, valid_aware_resize_depth

ROOT=Path(__file__).parents[1]; OUT=ROOT/"outputs_stage3_1"


class Stage31ContractTests(unittest.TestCase):
    def load(self,name): return json.loads((OUT/name).read_text(encoding="utf-8"))
    def test_resize_methods(self):
        for method in ("bilinear","nearest","area"): self.assertEqual(valid_aware_resize_depth(np.ones((480,640)),method)[0].shape,(360,640))
    def test_resize_valid_aware(self):
        x=np.zeros((4,4),np.float32);x[:,:2]=2;y,v=valid_aware_resize_depth(x,"bilinear",(8,8));self.assertTrue(np.allclose(y[v],2))
    def test_same_method(self):
        p,_=evaluate_object_depth([0,0,20,20],np.full((360,640),2.0));g,_=evaluate_object_depth([0,0,20,20],np.ones((360,640)));self.assertEqual(same_region_same_statistic(p,g,"bbox_full","median")["absolute_error_m"],1)
    def test_frozen(self):
        p,_=evaluate_object_depth([0,0,20,20],np.ones((360,640)));self.assertEqual(frozen_candidate(p,{"region_method":"bbox_full","statistic_method":"median"})["statistic_method"],"median")
    def test_delta(self):
        p,_=evaluate_object_depth([0,0,20,20],np.ones((360,640)));q,_=evaluate_object_depth([0,0,20,20],np.full((360,640),1.5));self.assertEqual(frozen_canvas_delta(p,q,{"region_method":"bbox_full","statistic_method":"median"})["absolute_delta_m"],.5)
    def test_counts(self):
        m=self.load("object_set_manifest.json");self.assertEqual((m["detection_native_count"],m["segmentation_native_count"],m["matched_pair_count"]),(46,36,21))
    def test_unmatched(self):
        m=self.load("object_set_manifest.json");self.assertEqual((m["unmatched_detection_count"],m["unmatched_segmentation_count"]),(25,15))
    def test_matrices(self):
        self.assertEqual(self.load("segmentation_method_matrix.json")["object_count"],36);self.assertEqual(self.load("detection_method_matrix.json")["object_count"],46)
    def test_resize_no_flip(self): self.assertFalse(self.load("gt_resize_sensitivity.json")["significant_flip"])
    def test_schema(self): self.assertEqual(self.load("official_144/stage3_1_object_depth_results.json")["schema_version"],"deltar_stage3_1_object_depth.v1")


if __name__=="__main__": unittest.main()
