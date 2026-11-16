import copy
import pytest

from deltar_p4_pipeline.analytic_renderer import stage5_scene_family
from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.stage5_contracts import require_no_gt_leakage, validate_scene


def test_scene_contract_and_gt_separation():
    assert validate_scene(stage5_scene_family()[0])["camera"]["width"]==640
    require_no_gt_leakage({"gt_used_for_inference":False,"inference_inputs":{"rgb":True,"tof":True}})


def test_gt_leakage_rejected():
    with pytest.raises(PipelineError,match="GT field"):
        require_no_gt_leakage({"gt_used_for_inference":False,"inference_inputs":{"gt_depth":True}})


def test_bad_camera_rejected():
    scene=copy.deepcopy(stage5_scene_family()[0]);scene["camera"]["width"]=320
    with pytest.raises(PipelineError):validate_scene(scene)
