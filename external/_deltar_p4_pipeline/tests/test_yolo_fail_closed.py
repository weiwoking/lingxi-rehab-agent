from __future__ import annotations

from pathlib import Path

import pytest

from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.yolo_worker_client import run_yolo_worker

ROOT = Path(__file__).parents[1]
VISION = Path(r"C:\ec\_conda_envs\vision_interpreter\python.exe")
WORKER = ROOT / "workers" / "yolo_worker.py"
DETECT = Path(r"C:\ec\esp-claw\pc\vision_interpreter\models\yolo11n.pt")
SEGMENT = ROOT / "models" / "yolo11n-seg.pt"
DETECT_SHA = "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"
SEGMENT_SHA = "55ed65c56c91713d23e8402371c6c49a6fd84f257f7dce452e8d70e41dcbe152"
IMAGE = ROOT / "samples" / "stage3_official" / "official_144" / "source_rgb.png"


@pytest.mark.integration
def test_wrong_detect_hash_fails_closed(tmp_path):
    with pytest.raises(PipelineError) as caught:
        run_yolo_worker(VISION, WORKER, DETECT, "detect", [IMAGE], tmp_path, expected_sha256="0" * 64)
    assert caught.value.code == "E_YOLO_MODEL_HASH"


@pytest.mark.integration
def test_wrong_segment_hash_fails_closed(tmp_path):
    with pytest.raises(PipelineError) as caught:
        run_yolo_worker(VISION, WORKER, SEGMENT, "segment", [IMAGE], tmp_path, expected_sha256="f" * 64)
    assert caught.value.code == "E_YOLO_MODEL_HASH"


@pytest.mark.integration
def test_detect_cannot_masquerade_as_segment(tmp_path):
    with pytest.raises(PipelineError) as caught:
        run_yolo_worker(VISION, WORKER, DETECT, "segment", [IMAGE], tmp_path, expected_sha256=DETECT_SHA)
    assert caught.value.code == "E_YOLO_TASK"


@pytest.mark.integration
def test_verified_output_records_expected_and_actual_hash(tmp_path):
    result = run_yolo_worker(VISION, WORKER, SEGMENT, "segment", [IMAGE], tmp_path, expected_sha256=SEGMENT_SHA)[0]["document"]
    assert result["model"]["sha256"] == result["model"]["expected_sha256"] == SEGMENT_SHA
    assert result["model"]["hash_verified"] and result["model"]["task_verified"]
