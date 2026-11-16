import numpy as np

from deltar_p4_pipeline.scene_geometry import ray_box, ray_plane


def test_ray_plane_hit_and_behind():
    rays=np.array([[0,1,0],[0,-1,0]],dtype=float)
    hit=ray_plane(np.zeros(3),rays,np.array([0,2,0]),np.array([0,-1,0]))
    assert hit[0]==2 and np.isinf(hit[1])


def test_ray_box_hit_and_miss():
    result=ray_box(np.zeros(3),np.array([[0,1,0],[1,0,0]],float),np.array([-.5,1,-.5]),np.array([.5,2,.5]))
    assert result[0]==1 and np.isinf(result[1])
