from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.pc_scene_ui_renderer import render_pc_scene_ui  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a standalone PC scene HTML from one editable UI input JSON."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = render_pc_scene_ui(args.input, args.output)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({"input": str(args.input.resolve()), "output": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
