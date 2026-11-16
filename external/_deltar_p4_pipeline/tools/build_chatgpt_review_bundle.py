from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.chatgpt_review_bundle import (  # noqa: E402
    build_chatgpt_review_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build or atomically replace a scan's ChatGPT Review ZIP."
    )
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--terminal-status")
    parser.add_argument("--failure-reason")
    args = parser.parse_args()
    outcome = build_chatgpt_review_bundle(
        args.result_dir,
        scan_terminal_status=args.terminal_status,
        terminal_failure_reason=args.failure_reason,
    )
    print(
        json.dumps(
            {
                "status": "success",
                "zip_path": str(outcome.zip_path),
                "bundle_status": outcome.manifest["bundle_status"],
                "included_file_count": len(outcome.manifest["included_files"]),
                "preview_policy": outcome.manifest["preview_policy"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

