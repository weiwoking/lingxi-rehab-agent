import json
from pathlib import Path

import numpy as np

ROOT=Path(__file__).resolve().parents[1]


def test_rgb565_and_truth_artifacts():
    parent=ROOT/"samples"/"stage5_analytic"/"F1_floor_boxes"
    if not parent.is_dir():return
    assert (parent/"image.rgb565").stat().st_size==460800
    truth=json.loads((parent/"ground_truth.json").read_text());assert truth["gt_used_for_inference"] is False
    assert np.load(parent/"ground_truth_depth.npy",allow_pickle=False).shape==(360,640)
