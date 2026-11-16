from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.pc_scene_ui_adapter import (  # noqa: E402
    UI_INPUT_FILENAME,
    build_pc_scene_ui_input_from_result,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the editable PC scene UI contract from cached PC-local artifacts."
    )
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or (args.result_dir / UI_INPUT_FILENAME)
    document, path = build_pc_scene_ui_input_from_result(
        args.result_dir, output_path=output
    )
    print(json.dumps({"output": str(path), "frame_count": document["frame_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
