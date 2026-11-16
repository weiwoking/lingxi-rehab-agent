from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.cli import DEFAULT_CHECKPOINT, DEFAULT_WORKTREE, run_sample
from deltar_p4_pipeline.deltar_bridge import DeltarBridge
from deltar_p4_pipeline.errors import PipelineError


def main() -> None:
    output_root = ROOT / "outputs"
    bridge = DeltarBridge(DEFAULT_WORKTREE, DEFAULT_CHECKPOINT, "cuda:0")
    samples = []
    for group in (ROOT / "samples" / "official_reverse", ROOT / "samples" / "synthetic"):
        samples.extend(sorted(path for path in group.iterdir() if path.is_dir()))
    results = []
    expected_rejections = {"all_invalid": "E_NO_VALID_ZONES"}
    for sample in samples:
        try:
            result = run_sample(sample, output_root, bridge=bridge)
            results.append({"sample_id": sample.name, "status": result["status"], "strategy": result["canvas"]["strategy"], "depth": result["depth"], "timing": result["timing"]})
        except PipelineError as exc:
            expected_code = expected_rejections.get(sample.name)
            matrix_status = "rejected_expected" if exc.code == expected_code else "error"
            results.append({"sample_id": sample.name, **exc.as_dict(), "matrix_status": matrix_status})
    summary = {
        "schema_version": "deltar_p4_stage2_matrix.v1.1",
        "total": len(results),
        "success": sum(item["status"] == "success" for item in results),
        "expected_rejections": sum(item.get("matrix_status") == "rejected_expected" for item in results),
        "errors": sum(item.get("matrix_status") == "error" for item in results),
        "formal_stage3_candidates": ["direct_640x360", "letterbox_640x480"],
        "backup_candidate": "scale_to_fill_center_crop_640x480",
        "not_recommended_for_freeze": "resize_640x480",
        "results": results,
    }
    (output_root / "stage2_matrix.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"total": summary["total"], "success": summary["success"], "errors": summary["errors"]}))


if __name__ == "__main__":
    main()
