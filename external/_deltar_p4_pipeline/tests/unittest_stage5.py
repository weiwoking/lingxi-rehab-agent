import unittest
import numpy as np

from deltar_p4_pipeline.scene_geometry import ray_box, ray_plane
from deltar_p4_pipeline.stage5_matching import bbox_iou
from deltar_p4_pipeline.stage5_metrics import distribution


class Stage5UnitTests(unittest.TestCase):
    def test_plane(self):self.assertEqual(ray_plane(np.zeros(3),np.array([[0,1,0.]]),np.array([0,2,0]),np.array([0,-1,0]))[0],2)
    def test_box(self):self.assertEqual(ray_box(np.zeros(3),np.array([[0,1,0.]]),np.array([-.5,1,-.5]),np.array([.5,2,.5]))[0],1)
    def test_iou(self):self.assertEqual(bbox_iou([0,0,2,2],[0,0,2,2]),1)
    def test_metrics(self):self.assertEqual(distribution([1,2,3])["median"],2)


if __name__=="__main__":unittest.main()
