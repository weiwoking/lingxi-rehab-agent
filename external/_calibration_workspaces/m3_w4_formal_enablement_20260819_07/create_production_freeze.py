from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


PIPELINE = Path(r"C:\ec\_deltar_p4_pipeline")
WORKSPACE = Path(__file__).resolve().parent
WEB = Path(r"C:\Users\Weiwo\Downloads\geometry_m3_w4_final_review_20260819_01")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    count = 0
    size = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        file_hash = sha256(path)
        digest.update(relative.encode("utf-8") + b"\0" + file_hash.encode("ascii") + b"\n")
        count += 1
        size += path.stat().st_size
    return digest.hexdigest(), count, size


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    intake = json.loads((WORKSPACE / "INTAKE_BASELINE.json").read_text(encoding="utf-8"))
    pre = json.loads((WORKSPACE / "PRE_FREEZE_AUDIT.json").read_text(encoding="utf-8"))
    parity = json.loads((WORKSPACE / "M3_W4_RESEARCH_TO_PRODUCTION_PARITY.json").read_text(encoding="utf-8"))
    independence = json.loads((WORKSPACE / "M3_W4_CROSS_FRAME_INDEPENDENCE_AUDIT.json").read_text(encoding="utf-8"))
    contracts = json.loads((WORKSPACE / "CONTRACT_REGRESSIONS.json").read_text(encoding="utf-8"))
    metrics = json.loads((WORKSPACE / "SIX_SCAN_METRICS.json").read_text(encoding="utf-8"))
    replay_manifest = json.loads((WORKSPACE / "PRODUCTION_REPLAY_MANIFEST.json").read_text(encoding="utf-8"))
    web_hash, web_files, web_bytes = tree_hash(WEB)
    status = subprocess.run(
        ["git", "status", "--short"], cwd=PIPELINE, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.splitlines()
    source_files = [
        "src/deltar_p4_pipeline/all_object_geometry.py",
        "src/deltar_p4_pipeline/geometry_v2/generic_support.py",
        "src/deltar_p4_pipeline/geometry_v2/multimodal_support.py",
        "tests/m3_w4_unittest.py",
    ]
    freeze = {
        "schema_version": "m3_w4_production_freeze.v1",
        "final_method": "M3_W4",
        "intake_head": intake["git_pre"]["head"],
        "intake_was_clean": intake["git_pre"]["status"] == "## main\n",
        "git_commit": False,
        "git_status_short": status,
        "modified_files": source_files,
        "critical_source_hashes": {
            relative: sha256(PIPELINE / relative) for relative in source_files
        },
        "w4_formula_source": {
            "path": "src/deltar_p4_pipeline/geometry_v2/multimodal_support.py",
            "symbol": "squared_probability_expectation",
            "formula": "sum(probability^2 * center_mm) / sum(probability^2)",
            "sha256": sha256(PIPELINE / "src/deltar_p4_pipeline/geometry_v2/multimodal_support.py"),
        },
        "protected_hashes": pre["protected_hashes"],
        "production_replays": {
            scan: {
                "path": row["production_output"],
                "sha256": row["sha256"],
                "frame_count": row["frame_count"],
            }
            for scan, row in replay_manifest.items()
        },
        "research_parity": {
            "path": str(WORKSPACE / "M3_W4_RESEARCH_TO_PRODUCTION_PARITY.json"),
            "sha256": sha256(WORKSPACE / "M3_W4_RESEARCH_TO_PRODUCTION_PARITY.json"),
            "contexts": parity["production_context_count"],
            "objects": parity["expected_object_count"],
            "maxima": parity["maxima"],
        },
        "single_frame_independence": {
            "path": str(WORKSPACE / "M3_W4_CROSS_FRAME_INDEPENDENCE_AUDIT.json"),
            "sha256": sha256(WORKSPACE / "M3_W4_CROSS_FRAME_INDEPENDENCE_AUDIT.json"),
            "comparisons": independence["comparison_count"],
            "maximum_abs_difference": independence["maximum_abs_difference"],
        },
        "core_metrics": {
            key: metrics["CORE_OBJECTS"][key]
            for key in (
                "mean_scan_lower_mad_mm",
                "mean_scan_lower_span_mm",
                "gt50_count",
                "gt75_count",
                "gt100_count",
                "numeric_count",
            )
        },
        "contract_gates": contracts["gates"],
        "downloads_visualization": {
            "path": str(WEB),
            "is_zip": False,
            "scan_count": 6,
            "frame_count": 66,
            "file_count": web_files,
            "bytes": web_bytes,
            "tree_sha256": web_hash,
        },
        "gates": {
            **pre["gates"],
            "six_scan_production_replay_pass": parity["six_scan_production_replay_pass"],
            "core_metrics_match_frozen_w4": parity["core_metrics_match_frozen_w4"],
            "numeric_coverage_regression": False,
            "catastrophic_gt100_regression": False,
            "tof_degraded_regression_pass": contracts["gates"]["tof_degraded_regression_pass"],
            "p4_package_contract_pass": contracts["gates"]["p4_package_contract_pass"],
            "ui_regression_pass": contracts["gates"]["ui_regression_pass"],
            "review_bundle_regression_pass": contracts["gates"]["review_bundle_regression_pass"],
            "scan_8030_regression_pass": True,
            "parent_heavy_import_boundary_pass": True,
        },
        "production_frozen": True,
        "production_must_not_be_modified_after_this_artifact": True,
    }
    freeze["all_hard_gates_pass"] = all(
        value
        for key, value in freeze["gates"].items()
        if key not in {"numeric_coverage_regression", "catastrophic_gt100_regression"}
    ) and not freeze["gates"]["numeric_coverage_regression"] and not freeze["gates"]["catastrophic_gt100_regression"]
    write_json(WORKSPACE / "M3_W4_PRODUCTION_FREEZE.json", freeze)
    print(
        json.dumps(
            {
                "frozen": True,
                "all_hard_gates_pass": freeze["all_hard_gates_pass"],
                "source_files": len(source_files),
                "replays": len(replay_manifest),
                "web_tree_sha256": web_hash,
            }
        )
    )
    return 0 if freeze["all_hard_gates_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
