from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.offline_preflight import atomic_write_result, run_preflight


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Validate the formal DELTAR runtime without external network access")
    value.add_argument("--output", type=Path, required=True)
    value.add_argument("--pipeline-root", type=Path, default=ROOT)
    value.add_argument("--profile-bundle", type=Path, default=ROOT / "profiles")
    value.add_argument("--worktree", type=Path, default=(ROOT.parent / "_deltar_formal" / "work" / "deltar_f3c5175_win_cu128"))
    value.add_argument("--checkpoint", type=Path, default=(ROOT.parent / "_deltar_formal" / "assets" / "nyu.pt"))
    value.add_argument("--device", default="cuda:0")
    value.add_argument("--allow-network", action="store_true")
    return value


def main() -> int:
    args = parser().parse_args()
    result = run_preflight(
        pipeline_root=args.pipeline_root,
        profile_bundle=args.profile_bundle,
        worktree=args.worktree,
        checkpoint=args.checkpoint,
        device=args.device,
        deny_network=not args.allow_network,
    )
    atomic_write_result(args.output, result)
    print(json.dumps({
        "status": result["status"],
        "ready": result["ready"],
        "checked_at": result["checked_at"],
        "network_attempt_count": len(result["network_attempts"]),
        "error": result["error"],
    }, ensure_ascii=False))
    return 0 if result["ready"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
