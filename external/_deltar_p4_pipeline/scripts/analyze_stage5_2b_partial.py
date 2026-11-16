from __future__ import annotations

import json
from pathlib import Path

from deltar_p4_pipeline.partial_object import contract_definition
from deltar_p4_pipeline.stage5_2b_validation import border_analysis, write_json


ROOT = Path(__file__).resolve().parents[1]


def run() -> dict:
    output = ROOT / "outputs_stage5_2b"
    analysis = border_analysis(ROOT)
    write_json(output / "partial_object_contract.json", contract_definition())
    write_json(output / "border_partial_analysis.json", analysis)
    return analysis


if __name__ == "__main__":
    result = run()
    print(json.dumps({"status": "PASS", "objects": sum(item["count"] for item in result["groups"])}, ensure_ascii=False))
