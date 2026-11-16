from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent
PYTHON = Path(sys.executable)
REPORT_ROOT = Path(r"C:\ec\_codex_reports")
OLD_TOOL_SHA256 = "18f281a4525ab57f95445448aefbbe24e7bbb4760a8a1119e0b9911e6684d4de"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run(name: str, args: list[str], expected_codes: set[int] = {0}) -> dict:
    process = subprocess.run(
        [str(PYTHON), *args], cwd=WORKSPACE, text=True, encoding="utf-8",
        errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    result = {"command": [str(PYTHON), *args], "exit_code": process.returncode, "output": process.stdout}
    if process.returncode not in expected_codes:
        raise RuntimeError(f"{name} failed ({process.returncode}):\n{process.stdout}")
    write(WORKSPACE / "validation" / f"{name}.txt", process.stdout)
    return result


def choose_report_dir() -> Path:
    for index in range(1, 100):
        candidate = REPORT_ROOT / f"stage3a_fixed_board_mode_fix_20260818_{index:02d}"
        if not candidate.exists():
            candidate.mkdir(parents=True)
            return candidate
    raise RuntimeError("no free report directory suffix")


def main() -> int:
    test = run("fixed_board_test_transcript", ["-m", "unittest", "discover", "-s", "tests", "-v"])
    help_result = run("fixed_board_cli_help", ["stage3a_camera_world.py", "--help"])
    status_result = run("fixed_board_empty_status", ["stage3a_camera_world.py", "status"])
    set_board_result = run(
        "fixed_board_idempotent_set",
        ["stage3a_camera_world.py", "set-board-height", "--active-bottom-mm", "912", "--uncertainty-mm", "5"],
    )
    export_refusal = run(
        "fixed_board_empty_candidate_refusal",
        ["stage3a_camera_world.py", "export-candidate"], expected_codes={2},
    )
    test_match = re.search(r"Ran (\d+) tests", test["output"])
    if not test_match:
        raise RuntimeError("cannot determine test count")
    test_count = int(test_match.group(1))
    status = json.loads(status_result["output"])
    if any(
        [
            status["board_mode"] != "FIXED_VERTICAL_BOARD",
            status["board_moved_between_poses"] is not False,
            status["capture_mode"] != "VEHICLE_MULTI_DISTANCE",
            status["fixed_board_active_bottom_height_mm"] != 912.0,
            status["fixed_board_height_uncertainty_mm"] != 5.0,
            status["registered_physical_pose_count"] != 0,
        ]
    ):
        raise RuntimeError(f"fixed-board empty status mismatch: {status}")
    if (WORKSPACE / "candidate" / "camera_world_pose.stage3a_candidate.v1.json").exists():
        raise RuntimeError("real candidate unexpectedly exists")
    if (WORKSPACE / "diagnostics" / "solution.json").exists():
        raise RuntimeError("real solution unexpectedly exists")

    tool_path = WORKSPACE / "stage3a_camera_world.py"
    guide_path = WORKSPACE / "CAPTURE_GUIDE_STAGE3A.md"
    tool_hash = sha256(tool_path)
    if tool_hash == OLD_TOOL_SHA256:
        raise RuntimeError("tool SHA is unchanged from rejected implementation")
    tool_text = tool_path.read_text(encoding="utf-8")
    guide_text = guide_path.read_text(encoding="utf-8")
    for label, text in (("tool", tool_text), ("guide", guide_text)):
        for required in ("FIXED_VERTICAL_BOARD", "VEHICLE_MULTI_DISTANCE", "near", "mid", "far"):
            if required not in text:
                raise RuntimeError(f"{label} missing required fixed-board token: {required}")
    forbidden_checks = {
        "tool": [r"W01[^\n]*\blow\b", r"W02[^\n]*middle height", r"W03[^\n]*\bhigh\b", r"move board"],
        "guide": [r"W01：低高度", r"W02：中高度", r"W03：高高度", r"把板拆下", r"<W01_MM>"],
    }
    for label, patterns in forbidden_checks.items():
        text = tool_text if label == "tool" else guide_text
        for pattern in patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                raise RuntimeError(f"{label} contains forbidden moving-board pattern: {pattern}")

    baseline = json.loads((WORKSPACE / "protected_hashes_before.json").read_text(encoding="utf-8"))
    entries = [baseline["stage2_final_bundle"], baseline["old_stage7c_result"], *baseline["read_only_source_files"]]
    checks = []
    for entry in entries:
        observed = sha256(Path(entry["path"]))
        checks.append({
            "path": entry["path"], "before_sha256": entry["sha256"],
            "after_sha256": observed, "unchanged": observed == entry["sha256"],
        })
    protected = {
        "schema_version": "stage3a_fixed_board_protected_hashes_after.v1",
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "all_unchanged": all(item["unchanged"] for item in checks),
        "formal_runtime_modified": False,
        "checks": checks,
    }
    if not protected["all_unchanged"]:
        raise RuntimeError("protected authority/source hash changed")
    protected_path = WORKSPACE / "protected_hashes_after_fixed_board_fix.json"
    write(protected_path, json.dumps(protected, indent=2, ensure_ascii=False) + "\n")

    report_dir = choose_report_dir()
    report = f"""# Stage3A Fixed Board Mode Correction Report

`STAGE3A_FIXED_BOARD_MODE_READY = true`

- old tool SHA = `{OLD_TOOL_SHA256}`
- new tool SHA = `{tool_hash}`
- tool changed = `true`
- board mode = `FIXED_VERTICAL_BOARD`
- fixed active bottom = `912 ±5 mm`
- capture mode = `VEHICLE_MULTI_DISTANCE`
- real captures registered = `0`
- formal runtime modified = `false`
- promotion = `false`

## Corrected physical capture contract

ChArUco remains permanently fixed to the rigid vertical cabinet door and does not cross a seam. `board_moved_between_poses=false`. W01/W02/W03 now mean vehicle near / mid / far at approximately 0.6 / 0.8 / 1.0 m; actual board-plane distance is estimated from PnP. W04 is an optional vehicle reposition holdout and is explicitly excluded from the W01–W03 primary solve.

`set-board-height --active-bottom-mm 912 --uncertainty-mm 5` writes or idempotently verifies `fixed_board_height.json`. Once configured, a mismatch fails closed. `register` no longer requires a board-height argument; its compatibility height arguments can only assert the frozen value.

## Coverage and validation changes

Coverage reports physical-pose count, PnP board-plane distance, near/mid/far role, board centre/coverage, reprojection quality, pose orientation, orientation-vs-distance, and fixed active-bottom residual-vs-distance.

The primary solve uses exactly one robust pose mean from each of W01 near, W02 mid, and W03 far. Validation now reports reconstructed fixed active-bottom Z and signed/absolute residual per pose, overall median/P95/RMS, residual-vs-distance slope, per-pose matrix and diagnostic RPY, SO(3) geodesic spread/trend, physical-pose LOPO, optional W04 repeatability, and the unchanged 962/972/982 mm Camera-height sensitivity.

Stable three-pose evidence yields `EVIDENCE_READY_FOR_OWNER_JUDGMENT`; it does not promote a candidate and does not start Stage3B.

## Verification

- `{test_count}` updated tests passed. They cover frozen board configuration, post-set freeze, register mismatch refusal, no repeated height requirement, near/mid/far recovery, fixed-height reconstruction across distance, residual trend existence, orientation trend, W04 exclusion/holdout policy, guidance text, candidate schema, Stage2 SHA, protected hashes, and the unchanged mathematical contracts.
- New tool SHA differs from the rejected SHA.
- Tool and capture guide contain `FIXED_VERTICAL_BOARD`, `VEHICLE_MULTI_DISTANCE`, and near/mid/far semantics.
- Tool and guide self-check found no W01/W02/W03 board-position instructions and no instruction to move the board.
- Stage2 bundle, old Stage7C result, Camera World/Stage7D source files, and schema all retain their before/after SHA-256 values.
- Registry remains empty; no latest scan was registered, no real solve/candidate was produced, and no formal/runtime file was modified.

## Exact W01 next instruction

Keep the ChArUco fixed. Place the vehicle at the W01 near position, approximately 0.6 m from the cabinet. Select two symmetric rigid points on the vehicle, measure each point to the cabinet plane, adjust vehicle yaw until the left/right difference is preferably ≤2–3 mm (≤5 mm is acceptable and recorded), wait 2–3 seconds, capture exactly one formal 11-frame scan, then run:

```powershell
& 'C:\\ec\\_deltar_formal\\envs\\deltar_py311_torch271_cu128\\python.exe' `
  'C:\\ec\\_calibration_workspaces\\stage3a_camera_world_20260818_01\\stage3a_camera_world.py' `
  register --pose W01 --latest --vehicle-alignment-delta-mm <LEFT_RIGHT_ABS_DIFF_MM>
```

Then run `inspect --pose W01` and `suggest-next`.
"""
    report_path = report_dir / "REPORT.md"
    write(report_path, report)
    report_text = report_path.read_text(encoding="utf-8")
    for required in ("FIXED_VERTICAL_BOARD", "near / mid / far", "STAGE3A_FIXED_BOARD_MODE_READY = true"):
        if required not in report_text:
            raise RuntimeError(f"report missing required correction token: {required}")

    files = sorted(
        path for path in WORKSPACE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    tree_path = WORKSPACE / "validation" / "workspace_tree_fixed_board.txt"
    write(tree_path, "\n".join(str(path.relative_to(WORKSPACE)).replace("\\", "/") for path in files) + "\n")
    whitelist = [
        "stage3a_camera_world.py", "candidate_schema.json", "workspace_manifest.json",
        "frozen_authority_manifest.json", "camera_height.json", "fixed_board_height.json",
        "CAPTURE_GUIDE_STAGE3A.md", "STAGE3B_HANDOFF.md", "protected_hashes_before.json",
        "protected_hashes_after_fixed_board_fix.json", "observations/registry.json",
        "tests/test_stage3a_camera_world.py", "source_audit/SOURCE_FIRST_AUDIT.md",
        "source_audit/OLD_STAGE7C_IMPLEMENTATION_SNIPPETS.txt",
        "validation/fixed_board_test_transcript.txt", "validation/fixed_board_cli_help.txt",
        "validation/fixed_board_empty_status.txt", "validation/fixed_board_idempotent_set.txt",
        "validation/fixed_board_empty_candidate_refusal.txt", "validation/workspace_tree_fixed_board.txt",
    ]
    zip_path = report_dir / "stage3a_fixed_board_mode_fix_evidence_20260818_01.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(report_path, "REPORT.md")
        for relative in whitelist:
            archive.write(WORKSPACE / relative, f"workspace/{relative}")
    evidence = {
        "report": {"path": str(report_path), "sha256": sha256(report_path)},
        "evidence_zip": {"path": str(zip_path), "sha256": sha256(zip_path)},
        "tool": {"path": str(tool_path), "old_sha256": OLD_TOOL_SHA256, "new_sha256": tool_hash, "changed": True},
        "test_count": test_count,
        "test_exit_code": test["exit_code"],
        "candidate_refusal_exit_code": export_refusal["exit_code"],
        "set_board_height_exit_code": set_board_result["exit_code"],
        "real_captures_registered": 0,
        "formal_runtime_modified": False,
    }
    hashes_path = report_dir / "evidence_hashes.json"
    write(hashes_path, json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"report_dir": str(report_dir), **evidence}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
