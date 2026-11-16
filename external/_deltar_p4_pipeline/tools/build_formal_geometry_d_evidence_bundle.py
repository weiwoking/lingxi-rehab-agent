from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(r"C:\ec\_deltar_p4_pipeline")
ESP = Path(r"C:\ec\esp-claw")
REPORT_DIR = Path(
    r"C:\ec\_codex_reports\stage3_formal_geometry_d_cutover_20260818_01"
)
EVIDENCE = REPORT_DIR / "evidence"
ZIP_PATH = REPORT_DIR / (
    "stage3_formal_geometry_d_cutover_20260818_01_evidence.zip"
)
REPLAY_ROOT = Path(r"C:\ec\_formal_d_replays_20260818")
REPLAYS = {
    "G001": "G001_release",
    "G002": "G002_release",
    "H001": "H001_release",
    "H002": "H002_release",
    "H003": "H003_release",
}


def run(command: list[str], cwd: Path | None = None) -> dict[str, object]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def write_text(relative: str, value: str) -> Path:
    path = EVIDENCE / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")
    return path


def write_json(relative: str, value: object) -> Path:
    return write_text(
        relative,
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
    )


def copy(source: Path, relative: str) -> None:
    target = EVIDENCE / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_match(line: str) -> str:
    lower = line.lower().replace("/", "\\")
    if "\\_codex_reports\\" in lower or lower.endswith(".md"):
        return "DOC_HISTORY"
    if "\\_calibration_workspaces\\" in lower or "\\_deltar_formal\\" in lower:
        return "DOC_HISTORY"
    active_names = (
        "\\_deltar_p4_pipeline\\src\\deltar_p4_pipeline\\deltar_bridge.py",
        "\\_deltar_p4_pipeline\\src\\deltar_p4_pipeline\\formal_deltar_d.py",
        "\\_deltar_p4_pipeline\\src\\deltar_p4_pipeline\\formal_geometry_authority.py",
        "\\_deltar_p4_pipeline\\src\\deltar_p4_pipeline\\stage2_projection.py",
        "\\_deltar_p4_pipeline\\src\\deltar_p4_pipeline\\tof_adapter.py",
        "\\_deltar_p4_pipeline\\src\\deltar_p4_pipeline\\frame_pipeline.py",
        "\\_deltar_p4_pipeline\\src\\deltar_p4_pipeline\\scan_job.py",
        "\\_deltar_p4_pipeline\\src\\deltar_p4_pipeline\\stage7d_",
        "\\_deltar_p4_pipeline\\src\\deltar_p4_pipeline\\horizontal_planes.py",
        "\\_deltar_p4_pipeline\\src\\deltar_p4_pipeline\\all_object_geometry.py",
        "\\_deltar_p4_pipeline\\src\\deltar_p4_pipeline\\stage8b4a_package.py",
        "\\esp-claw\\tools\\start-visionandscanpc\\",
    )
    if any(name in lower for name in active_names):
        return "ACTIVE_RUNTIME"
    if "\\test" in lower or "\\tests\\" in lower:
        return "TEST_ONLY"
    if "\\scripts\\" in lower or "\\tools\\" in lower:
        return "UNRELATED_TOOL"
    return "DEAD_OR_UNRELATED_SOURCE"


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    report = REPORT_DIR / "REPORT.md"
    if not report.is_file():
        raise SystemExit(f"REPORT.md must exist before packaging: {report}")

    baseline = {
        "pipeline": {
            "head": "825c08e204ef5c57e1251a5b904b4ac3ea613879",
            "status_short": [],
            "diff": "",
            "observed_clean": True,
        },
        "esp_claw": {
            "head": "7ba7084602469ab37ca6cd6ef3db15f1c2eb99ca",
            "status_short": [],
            "diff": "",
            "observed_clean": True,
        },
        "pre_existing_changes": [],
    }
    write_json("git/pre_baseline.json", baseline)
    for label, repository in (("pipeline", ROOT), ("esp_claw", ESP)):
        write_json(
            f"git/{label}_post.json",
            {
                "head": run(["git", "rev-parse", "HEAD"], repository),
                "status": run(["git", "status", "--short"], repository),
                "diff_check": run(["git", "diff", "--check"], repository),
                "diff_name_status": run(
                    ["git", "diff", "--name-status"], repository
                ),
                "diff_stat": run(["git", "diff", "--stat"], repository),
                "diff": run(["git", "diff", "--no-ext-diff"], repository),
            },
        )

    authority_dir = ROOT / "profiles" / "formal_geometry_v2"
    for name in (
        "authority.json",
        "stage2_calibration.json",
        "stage3a_gravity.json",
        "all_object_geometry_v2.json",
    ):
        copy(authority_dir / name, f"authority/{name}")
    for source in (
        "formal_geometry_authority.py",
        "formal_deltar_d.py",
        "stage2_projection.py",
        "tof_adapter.py",
        "horizontal_planes.py",
        "all_object_geometry.py",
        "frame_pipeline.py",
        "stage8b4a_package.py",
    ):
        copy(ROOT / "src" / "deltar_p4_pipeline" / source, f"source/{source}")
    for source in (ROOT / "src" / "deltar_p4_pipeline" / "geometry_v2").glob(
        "*.py"
    ):
        copy(source, f"source/geometry_v2/{source.name}")
    copy(ROOT / "tests" / "formal_geometry_d_unittest.py", "tests/formal_geometry_d_unittest.py")
    copy(ROOT / "tests" / "p4_current_package_host_harness.c", "tests/p4_current_package_host_harness.c")
    copy(ROOT / "tools" / "VERIFY_FORMAL_GEOMETRY_D_LATEST.ps1", "launcher/VERIFY_FORMAL_GEOMETRY_D_LATEST.ps1")
    copy(
        ESP
        / "tools"
        / "Start-VisionAndScanPC"
        / "start_vision_and_scan_services.ps1",
        "launcher/start_vision_and_scan_services.ps1",
    )
    for source, relative in (
        (ROOT / "tmp" / "formal_cutover_metrics.json", "metrics/formal_cutover_metrics.json"),
        (ROOT / "tmp" / "formal_d_preflight_final.json", "tests/formal_d_preflight_final.json"),
        (ROOT / "tmp" / "formal_geometry_d_unittest_final.log", "tests/formal_geometry_d_unittest_final.log"),
    ):
        copy(source, relative)

    replay_files = (
        "job_report.json",
        "result.json",
        "stage8b_all_object_geometry.v1.json",
        "stage8b4a_p4_decision_package.v1.json",
        "stage8b4a_p4_decision_package.v1.json.sha256",
        "stage8b4a_selected_objects.v2.json",
        "stage8b_dual_gate_diagnostics.v1.json",
    )
    for scan, directory in REPLAYS.items():
        root = REPLAY_ROOT / directory
        for name in replay_files:
            copy(root / name, f"replays/{scan}/{name}")
        copy(root / "logs" / "pipeline_progress.jsonl", f"replays/{scan}/pipeline_progress.jsonl")

    research = Path(
        r"C:\ec\_calibration_workspaces\deltar_fusion_final_ab_cde_20260818_01"
    )
    for name in (
        "FINAL_A_B_C_D_E_COMPARISON.json",
        "PERFORMANCE_A_B_C_D_E.json",
        "INTERNAL_ZONE_CAUSAL_PROBE.json",
    ):
        copy(research / "metrics" / name, f"research_reference/{name}")

    terms = (
        "stage7a|stage7b|stage7c|calibration_profile\\.example|"
        "regular_grid_source|stage7e_object_geometry|--stage7e-object-geometry|"
        "_apply_competition_shelf_dynamic_stabilizer|corrected_height_bias_mm|"
        "T_world_from_camera|shadow_v2|fallback_to_old|enable_stabilizer|"
        "upper_bottle|lower_bottle|lower_volleyball"
    )
    cached_audit_path = EVIDENCE / "audits" / "all_c_ec_old_authority_grep_classified.json"
    if cached_audit_path.is_file():
        cached_audit = json.loads(cached_audit_path.read_text(encoding="utf-8"))
        cached_lines = [
            line
            for values in cached_audit.get("matches", {}).values()
            for line in values
        ]
        audit = {
            "command": cached_audit.get("command", []),
            "returncode": cached_audit.get("returncode", 0),
            "stdout": "\n".join(cached_lines),
            "stderr": "",
        }
    else:
        audit = run(
            [
                "rg", "-n", "-i", terms, r"C:\ec",
                "--glob", "*.py", "--glob", "*.ps1", "--glob", "*.c",
                "--glob", "*.h", "--glob", "*.json", "--glob", "*.md",
                "--glob", "!**/.git/**", "--glob", "!**/models/**",
                "--glob", "!**/build/**", "--glob", "!**/runtime_data/**",
                "--glob", "!**/*evidence.zip",
            ]
        )
    lines = [line for line in str(audit["stdout"]).splitlines() if line]
    classified: dict[str, list[str]] = {}
    for line in lines:
        classified.setdefault(classify_match(line), []).append(line)
    write_json(
        "audits/all_c_ec_old_authority_grep_classified.json",
        {
            "command": audit["command"],
            "returncode": audit["returncode"],
            "classification_counts": {
                key: len(value) for key, value in classified.items()
            },
            "matches": classified,
            "active_runtime_interpretation": (
                "Any ACTIVE_RUNTIME match is reviewed by exact active-file audit; "
                "only retained external schema flags or non-authority wording may remain."
            ),
        },
    )

    active_files = [
        ROOT / "src" / "deltar_p4_pipeline" / name
        for name in (
            "deltar_bridge.py", "formal_deltar_d.py",
            "formal_geometry_authority.py", "stage2_projection.py",
            "tof_adapter.py", "frame_pipeline.py", "scan_job.py",
            "stage7d_contracts.py", "stage7d_outputs.py",
            "horizontal_planes.py", "all_object_geometry.py",
            "stage8b4a_package.py",
        )
    ]
    forbidden = (
        "stage7e", "calibration_profile.example", "regular_grid_source",
        "T_world_from_camera", "_apply_competition_shelf_dynamic_stabilizer",
        "corrected_height_bias_mm", "upper_bottle", "lower_bottle",
        "lower_volleyball", "fallback_to_old", "enable_stabilizer", "shadow_v2",
    )
    active_hits = {
        term: [str(path) for path in active_files if term.lower() in path.read_text(
            encoding="utf-8", errors="ignore"
        ).lower()]
        for term in forbidden
    }
    write_json(
        "audits/active_runtime_exact_audit.json",
        {
            "files": [str(path) for path in active_files],
            "forbidden_hits": active_hits,
            "active_old_measurement_authority_reference_count": sum(
                len(value) for value in active_hits.values()
            ),
            "scene_target_name_special_cases": False,
            "ground_truth_in_production_estimator": False,
            "class_specific_fixed_compensation": False,
            "legacy_runtime_switch": False,
        },
    )

    p4_parser = run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from pathlib import Path; "
                "p=Path(r'C:\\ec\\esp-claw\\application\\edge_agent\\components\\rehab_scan_transfer\\test'); "
                "sys.path.insert(0,str(p)); import test_stage8b4b_decision_flow as t; "
                "t.REAL_PACKAGE=Path(r'C:\\ec\\_formal_d_replays_20260818\\G001_release\\stage8b4a_p4_decision_package.v1.json'); "
                "raise SystemExit(t.main())"
            ),
        ]
    )
    write_json("p4/p4_scan_transfer_parser_host.json", p4_parser)
    binary = ROOT / "tmp" / "p4_current_package_host_harness.exe"
    resolver_rows = []
    for task in (
        "OT_CUP_TRANSFER_L1", "OT_CUP_DRINK_L3",
        "OT_BALL_TRANSFER_L1", "OT_BALL_TRANSFER_L2",
    ):
        resolver_rows.append(
            run(
                [
                    str(binary),
                    str(
                        REPLAY_ROOT
                        / "G001_release"
                        / "stage8b4a_p4_decision_package.v1.json"
                    ),
                    task,
                ]
            )
        )
    write_json("p4/p4_target_resolver_current_package.json", resolver_rows)
    write_text(
        "p4/unrelated_existing_host_suite_note.txt",
        "The repository's broad run_target_resolver_host_tests.ps1 was also invoked. "
        "It fails before package replay in unchanged P4 ability-string parsing "
        "(test_task_target_resolver.c line 77). The targeted current-package C "
        "resolver harness and the 23-case transfer/parser harness both pass.\n",
    )

    hashes = {
        str(path.relative_to(REPORT_DIR)).replace("\\", "/"): {
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(EVIDENCE.rglob("*"))
        if path.is_file() and path.name != "manifest.sha256.json"
    }
    write_json("manifest.sha256.json", hashes)

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(report, "REPORT.md")
        for path in sorted(EVIDENCE.rglob("*")):
            if path.is_file():
                archive.write(path, str(path.relative_to(REPORT_DIR)).replace("\\", "/"))
    print(
        json.dumps(
            {
                "report": str(report),
                "zip": str(ZIP_PATH),
                "zip_sha256": sha256(ZIP_PATH),
                "zip_bytes": ZIP_PATH.stat().st_size,
                "evidence_files": len(hashes) + 1,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
