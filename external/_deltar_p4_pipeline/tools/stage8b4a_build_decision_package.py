from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.stage8b4a_package import build_and_write_stage8b4a  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build dual-gate, selected-v2, and the current P4 decision package from "
            "the all-object geometry authority. Never invokes inference."
        )
    )
    parser.add_argument("job_output", type=Path)
    args = parser.parse_args()
    result = build_and_write_stage8b4a(args.job_output, repository_root=ROOT)
    print(
        json.dumps(
            {
                "scan_id": result["package"]["scan_id"],
                "gate_object_count": len(result["gate_document"]["objects"]),
                "selected_object_count": len(result["package"]["objects"]),
                "package_bytes": result["package_bytes"],
                "package_sha256": result["package_sha256"],
                "inference_invocations": result["diagnostics"][
                    "inference_invocations"
                ],
                "wall_clock_ms": result["wall_clock_ms"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
