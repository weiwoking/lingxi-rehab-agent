import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_real_stage5_matrix_if_present():
    path=ROOT/"outputs_stage5"/"full_matrix.json"
    if not path.is_file():return
    data=json.loads(path.read_text(encoding="utf-8"))
    assert data["status"]=="success" and data["scene_count"]>=12 and data["gt_used_for_inference"] is False
    assert set(data["layer_summaries"])=={"L0","L1","L2","L3"}
