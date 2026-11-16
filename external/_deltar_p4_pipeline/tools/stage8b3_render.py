from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.pc_scene_ui_adapter import build_and_render_pc_scene_ui  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the editable UI input and render HTML from PC-local artifacts only."
    )
    parser.add_argument("job_output", type=Path)
    args = parser.parse_args()
    result_root = args.job_output.resolve()
    manifest = build_and_render_pc_scene_ui(result_root)
    print(
        json.dumps(
            {
                "visualization_complete": manifest["visualization_complete"],
                "frame_count": manifest["frame_count"],
                "candidate_count": manifest["candidate_count"],
                "visualization_total_ms": manifest["processing"]["visualization_total_ms"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
