from __future__ import annotations

import json
from pathlib import Path

from deltar_p4_pipeline.scan_strategy import coverage_table
from deltar_p4_pipeline.stage5_2b_validation import distance_analysis, scan_recommendation_with_evidence, write_json


ROOT = Path(__file__).resolve().parents[1]


def run() -> dict:
    output = ROOT / "outputs_stage5_2b"
    analysis = distance_analysis(ROOT)
    write_json(output / "distance_band_analysis.json", analysis)
    write_json(output / "tof_coverage_table.json", coverage_table())
    write_json(output / "scan_distance_recommendation.json", scan_recommendation_with_evidence(ROOT))
    return analysis


if __name__ == "__main__":
    result = run()
    print(json.dumps({"status": "PASS", "objects": sum(item["object_count"] for item in result["bands"])}, ensure_ascii=False))
