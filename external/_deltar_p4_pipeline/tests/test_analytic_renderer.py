import numpy as np

from deltar_p4_pipeline.analytic_renderer import render_scene, stage5_scene_family


def test_render_shapes_and_consistency():
    result=render_scene(stage5_scene_family()[0])
    assert result["rgb"].shape==(360,640,3) and result["depth"].shape==(360,640)
    assert all(mask.shape==(360,640) for mask in result["masks"].values())
    assert all(np.all(result["depth"][mask]>0) for mask in result["masks"].values() if mask.any())


def test_zbuffer_occlusion_truth():
    result=render_scene(next(s for s in stage5_scene_family() if s["scene_id"]=="F6_occlusion_60"))
    target=next(x for x in result["truth"]["objects"] if x["object_id"]=="target")
    assert target["occlusion_ratio"]>0.1 and target["true_visible_height_m"]<target["true_physical_height_m"]
