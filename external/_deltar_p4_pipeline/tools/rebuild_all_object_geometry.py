from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.all_object_geometry import (  # noqa: E402
    ARTIFACT_FILENAME,
    load_geometry_profile,
    rebuild_all_object_geometry_from_cache,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild all-object geometry from retained cache without YOLO or DELTAR."
    )
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument(
        "--profile",
        type=Path,
        default=ROOT / "profiles" / "formal_geometry_region_profile.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or (args.result_dir / ARTIFACT_FILENAME)
    document, path = rebuild_all_object_geometry_from_cache(
        args.result_dir,
        load_geometry_profile(args.profile),
        destination=output,
    )
    print(json.dumps({"output": str(path), "summary": document["summary"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
