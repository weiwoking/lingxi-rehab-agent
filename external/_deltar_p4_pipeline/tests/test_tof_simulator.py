import copy
import numpy as np

from deltar_p4_pipeline.analytic_renderer import stage5_scene_family
from deltar_p4_pipeline.tof_simulator import simulate_tof, tof_rays


def test_viewpoint_baseline_and_rotation_change_rays():
    scene=next(s for s in stage5_scene_family() if s["scenario_family"]=="F12")
    origin,rays,zones=tof_rays(scene)
    assert np.isclose(np.linalg.norm(rays,axis=1),1).all() and len(np.unique(zones))==64
    assert not np.allclose(origin,np.asarray(scene["camera"]["T_ground_from_camera"])[:3,3])


def test_noise_invalid_status_and_contract():
    scene=next(s for s in stage5_scene_family() if s["scenario_family"]=="F10")
    tof,meta=simulate_tof(scene,seed=4)
    assert len(tof["distance_mm"])==64 and tof["target_status"][0]==255 and tof["nb_target_detected"][0]==0
    assert meta["approximate_sensor_model"] is True and meta["tof_viewpoint_used"] is True


def test_in_plane_rotation_changes_ordered_rays():
    scene=stage5_scene_family()[0];_,r0,_=tof_rays(scene);rot=copy.deepcopy(scene);rot["tof"]["in_plane_rotation_deg"]=90;_,r1,_=tof_rays(rot)
    assert not np.allclose(r0,r1)
