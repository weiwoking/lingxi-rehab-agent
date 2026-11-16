from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT_DIR = Path(r"C:\ec\_codex_reports\stage2c_camera_tof_validation_toolchain_20260818_01")
TOOL = ROOT / "stage2c_camera_tof_validate.py"
PYTHON = Path(sys.executable)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(root: Path, *, exclude_images: bool = False) -> dict:
    files = []
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if exclude_images and path.suffix.lower() in {".rgb565", ".png", ".jpg", ".jpeg"}: continue
        relative = path.relative_to(root).as_posix(); value = sha(path)
        files.append({"path": relative, "size": path.stat().st_size, "sha256": value})
        digest.update(relative.encode()); digest.update(b"\0"); digest.update(value.encode()); digest.update(b"\n")
    return {"root": str(root), "file_count": len(files), "tree_sha256": digest.hexdigest(), "files": files}


def run(name: str, arguments: list[str], cwd: Path = ROOT) -> None:
    process = subprocess.run(arguments, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8")
    (ROOT / "evidence" / name).write_text(f"exit_code={process.returncode}\n$ {' '.join(arguments)}\n{process.stdout}", encoding="utf-8")
    if process.returncode:
        raise SystemExit(f"evidence command failed: {name}")


def main() -> None:
    evidence = ROOT / "evidence"; evidence.mkdir(exist_ok=True); REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stage2b_root = Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01")
    formal_root = Path(r"C:\ec\_deltar_scan_service_data\validated")
    latest = sorted(path for path in formal_root.glob("scan_*") if path.is_dir() and (path / "scan.json").is_file())[-1]
    candidates = [Path(r"C:\ec\_calibration_workspaces\stage2a_camera_intrinsic_20260817_01\candidate\camera_intrinsics.stage2a_candidate.v1.json"),
                  Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\candidate\camera_tof.stage2b_all16_candidate.v2.json"),
                  Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\candidate\deltar_fr.stage2b_all16_candidate.v2.json")]
    protected_before = {
        "frozen_candidates": {str(path): sha(path) for path in candidates},
        "stage2b_manifest": {"path": str(stage2b_root / "workspace_manifest.json"), "sha256": sha(stage2b_root / "workspace_manifest.json")},
        "stage2b_imports": tree_digest(stage2b_root / "imports"),
        "latest_formal_scan": tree_digest(latest),
        "all_formal_validated_scans": tree_digest(formal_root),
    }
    run("cli_help.txt", [str(PYTHON), str(TOOL), "--help"])
    run("status_empty_registry.txt", [str(PYTHON), str(TOOL), "status"])
    run("register_latest_dry_run.txt", [str(PYTHON), str(TOOL), "register", "--pose", "DRY_RUN_ONLY", "--latest", "--dry-run"])
    run("inspect_empty_registry.txt", [str(PYTHON), str(TOOL), "inspect"])
    run("coverage_empty_registry.txt", [str(PYTHON), str(TOOL), "coverage"])
    run("validate_empty_registry.txt", [str(PYTHON), str(TOOL), "validate"])
    run("summary_empty_registry.txt", [str(PYTHON), str(TOOL), "summary"])
    run("suggest_next_empty_registry.txt", [str(PYTHON), str(TOOL), "suggest-next"])
    run("unit_tests.txt", [str(PYTHON), "-m", "unittest", "-v", "test_stage2c_toolchain.py"])
    run("synthetic_validation.txt", [str(PYTHON), "run_synthetic_validation.py"])
    protected_after = {
        "frozen_candidates": {str(path): sha(path) for path in candidates},
        "stage2b_manifest": {"path": str(stage2b_root / "workspace_manifest.json"), "sha256": sha(stage2b_root / "workspace_manifest.json")},
        "stage2b_imports": tree_digest(stage2b_root / "imports"),
        "latest_formal_scan": tree_digest(latest),
        "all_formal_validated_scans": tree_digest(formal_root),
    }
    proof = {"schema_version": "stage2c_protected_input_proof.v1", "before": protected_before, "after": protected_after,
             "unchanged": protected_before == protected_after, "formal_scan_registered": False}
    (evidence / "protected_inputs_before_after.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    stage2a = json.loads(Path(r"C:\ec\_calibration_workspaces\stage2a_camera_intrinsic_20260817_01\candidate\camera_intrinsics.stage2a_candidate.v1.json").read_text())
    stage2b = json.loads(Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\candidate\camera_tof.stage2b_all16_candidate.v2.json").read_text())
    fr = json.loads(Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\candidate\deltar_fr.stage2b_all16_candidate.v2.json").read_text())
    schema = {"stage2a": {"schema": stage2a["schema_version"], "camera": stage2a["camera"], "board": stage2a["board"]},
              "stage2b": {"schema": stage2b["schema_version"], "status": stage2b["status"], "gauge": stage2b["gauge"],
                          "range_semantics": stage2b["range_semantics"], "tof_model": stage2b["tof_low_dimensional_model"],
                          "transform": stage2b["T_camera_from_tof"], "zone0": stage2b["raw_zone_camera_mapping"][0],
                          "zone63": stage2b["raw_zone_camera_mapping"][63]},
              "deltar_fr": {key: fr[key] for key in ("schema_version", "status", "shape", "dtype", "units", "field_order", "rectangle_semantics", "zone_order", "measurement_semantics", "depth_dependence", "runtime_replaced", "promotion")}}
    (evidence / "candidate_schema_extraction.json").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    included = [path for path in ROOT.rglob("*") if path.is_file() and "imports" not in path.parts and "__pycache__" not in path.parts]
    workspace_tree = [{"path": path.relative_to(ROOT).as_posix(), "size": path.stat().st_size, "sha256": sha(path)} for path in sorted(included)]
    (evidence / "workspace_tree_and_hashes.json").write_text(json.dumps(workspace_tree, indent=2) + "\n", encoding="utf-8")
    included = [path for path in ROOT.rglob("*") if path.is_file() and "imports" not in path.parts and "__pycache__" not in path.parts]
    report = REPORT_DIR / "REPORT.md"
    if report.is_file(): included.append(report)
    zip_path = REPORT_DIR / "stage2c_camera_tof_validation_toolchain_20260818_01_Evidence_Context.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(included)):
            prefix = "REPORT" if path == report else "stage2c_workspace"
            archive.write(path, f"{prefix}/{path.name if path == report else path.relative_to(ROOT).as_posix()}")
    print(json.dumps({"zip": str(zip_path), "sha256": sha(zip_path), "protected_inputs_unchanged": proof["unchanged"],
                      "registered_scans": len(json.loads((ROOT / "workspace_manifest.json").read_text())["poses"])}, indent=2))


if __name__ == "__main__":
    main()
