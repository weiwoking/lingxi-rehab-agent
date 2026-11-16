from __future__ import annotations

import json
from pathlib import Path

from deltar_p4_pipeline.stage5_2b_validation import hardware_audit, write_json


ROOT = Path(__file__).resolve().parents[1]


def run() -> dict:
    value = hardware_audit(ROOT)
    write_json(ROOT / "outputs_stage5_2b" / "hardware_spec_audit.json", value)
    return value


if __name__ == "__main__":
    result = run()
    print(json.dumps({"status": "PASS", "sources": len(result["sources"])}, ensure_ascii=False))
