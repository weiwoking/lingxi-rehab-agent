from __future__ import annotations

import json
from pathlib import Path

from deltar_p4_pipeline.core_class_contract import core_contract
from deltar_p4_pipeline.stage5_2b_validation import core_performance, write_json


ROOT = Path(__file__).resolve().parents[1]


def run() -> dict:
    output = ROOT / "outputs_stage5_2b"
    performance = core_performance(ROOT)
    write_json(output / "core_class_contract.json", core_contract())
    write_json(output / "core_class_performance.json", performance)
    return performance


if __name__ == "__main__":
    result = run()
    print(json.dumps({"status": "PASS", "core_classes": len(result["classes"])}, ensure_ascii=False))
