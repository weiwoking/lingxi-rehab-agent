from __future__ import annotations

import json
from pathlib import Path

from deltar_p4_pipeline.stage5_2b_validation import run_all


ROOT = Path(__file__).resolve().parents[1]


def run() -> dict[str, str]:
    return {name: str(path) for name, path in run_all(ROOT).items()}


if __name__ == "__main__":
    outputs = run()
    print(json.dumps({"status": "PASS", "output_count": len(outputs), "outputs": outputs}, ensure_ascii=False))
