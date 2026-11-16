from __future__ import annotations

import json
from pathlib import Path

import pytest

from deltar_p4_pipeline.yolo_worker_client import run_yolo_worker

ROOT=Path(__file__).parents[1]
VISION=Path(r"C:\ec\_conda_envs\vision_interpreter\python.exe")
WORKER=ROOT/"workers"/"yolo_worker.py"
DETECT_SHA256="0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"
SEGMENT_SHA256="55ed65c56c91713d23e8402371c6c49a6fd84f257f7dce452e8d70e41dcbe152"


@pytest.fixture(scope="module")
def real_yolo(tmp_path_factory):
    image=ROOT/"samples"/"stage3_official"/"official_144"/"source_rgb.png"
    output=tmp_path_factory.mktemp("real_yolo")
    detect=run_yolo_worker(VISION,WORKER,Path(r"C:\ec\esp-claw\pc\vision_interpreter\models\yolo11n.pt"),"detect",[image],output,expected_sha256=DETECT_SHA256)
    segment=run_yolo_worker(VISION,WORKER,ROOT/"models"/"yolo11n-seg.pt","segment",[image],output,expected_sha256=SEGMENT_SHA256)
    return detect[0],segment[0]


@pytest.mark.integration
def test_real_detection_worker_loads_detect_model(real_yolo):
    detect,_=real_yolo
    assert detect["document"]["model"]["task"]=="detect" and detect["document"]["instances"]


@pytest.mark.integration
def test_real_segmentation_worker_loads_segment_model(real_yolo):
    _,segment=real_yolo
    assert segment["document"]["model"]["task"]=="segment" and segment["masks"]
    assert all(mask.shape==(360,640) and mask.dtype==bool for mask in segment["masks"].values())


@pytest.mark.integration
def test_complete_stage3_matrix_contract():
    matrix=json.loads((ROOT/"outputs_stage3"/"stage3_matrix.json").read_text(encoding="utf-8"))
    assert matrix["status"]=="pass" and matrix["official_sample_count"]>=10 and matrix["matched_object_count"]>0
    assert matrix["gt_used_for_inference"] is False and matrix["deltar_repeatability_exact"] is True


@pytest.mark.integration
def test_sample_contains_direct_letterbox_and_separate_gt():
    root=ROOT/"outputs_stage3"/"official_144"
    result=json.loads((root/"object_depth_results.json").read_text(encoding="utf-8"))
    assert result["deltar_candidates"]==["direct_640x360","letterbox_640x480"]
    assert result["gt_used_for_inference"] is False and (root/"direct_depth_source.npy").is_file() and (root/"letterbox_depth_source.npy").is_file()
