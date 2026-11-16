from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent
REPORT_DIR = Path(r"C:\ec\_codex_reports\stage3a_gravity_authority_exec_20260818_01")
REPORT = REPORT_DIR / "REPORT.md"
EVIDENCE_DIR = REPORT_DIR / "evidence_context"
ZIP_PATH = REPORT_DIR / "stage3a_gravity_authority_exec_20260818_01_evidence.zip"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        cwd=WORKSPACE,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = completed.stdout + completed.stderr
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}\n{output}")
    return output


def copy(relative: str, destination: str | None = None) -> Path:
    source = WORKSPACE / relative
    target = EVIDENCE_DIR / (destination or relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def main() -> int:
    if not REPORT.is_file():
        raise RuntimeError(f"report missing: {REPORT}")
    if EVIDENCE_DIR.exists() or ZIP_PATH.exists():
        raise RuntimeError("evidence target already exists; refusing silent overwrite")
    EVIDENCE_DIR.mkdir(parents=True)

    copy_items = [
        ("stage3a_gravity_authority.py", None),
        ("stage3a_oblique_gravity_score.py", None),
        ("stage3a_straightness_score.py", None),
        ("tests/test_stage3a_gravity_authority.py", None),
        ("tests/test_stage3a_oblique_gravity_score.py", None),
        ("camera_gravity.stage3a_candidate.v1.schema.json", None),
        ("candidate/camera_gravity.stage3a_candidate.v1.example.json", None),
        ("GRAVITY_AUTHORITY_EXEC_GUIDE.md", None),
        ("diagnostics/oblique_gravity_20260818/GRAVITY_YAW_INVARIANCE_AUDIT.md", "math/GRAVITY_YAW_INVARIANCE_AUDIT.md"),
        ("diagnostics/oblique_gravity_20260818/GRAVITY_FIXED_FIT_DESIGN.md", "math/GRAVITY_FIXED_FIT_DESIGN.md"),
        ("diagnostics/oblique_gravity_20260818/IMU_GRAVITY_FALLBACK.md", "context/IMU_GRAVITY_FALLBACK.md"),
        ("diagnostics/oblique_gravity_20260818/GRAVITY_CANDIDATE_CONCEPT.json", "context/GRAVITY_CANDIDATE_CONCEPT.json"),
        ("protected_hashes_after_fixed_board_fix.json", "hashes/protected_hashes_prior.json"),
    ]
    for relative, destination in copy_items:
        copy(relative, destination)
    shutil.copy2(REPORT, EVIDENCE_DIR / "REPORT.md")

    (EVIDENCE_DIR / "cli").mkdir()
    (EVIDENCE_DIR / "tests").mkdir(exist_ok=True)
    (EVIDENCE_DIR / "cli" / "HELP.txt").write_text(
        run([sys.executable, "stage3a_gravity_authority.py", "--help"]), encoding="utf-8"
    )
    (EVIDENCE_DIR / "cli" / "STATUS.txt").write_text(
        run([sys.executable, "stage3a_gravity_authority.py", "status"]), encoding="utf-8"
    )
    (EVIDENCE_DIR / "cli" / "SUMMARY.txt").write_text(
        run([sys.executable, "stage3a_gravity_authority.py", "summary"]), encoding="utf-8"
    )
    test_output = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"])
    (EVIDENCE_DIR / "tests" / "TEST_RESULTS.txt").write_text(test_output, encoding="utf-8")

    study = json.loads(
        (WORKSPACE / "diagnostics" / "oblique_gravity_20260818" / "CONDITIONING_STUDY.json").read_text(encoding="utf-8")
    )
    focused_math = {
        "gravity_oblique_route_viable": study["gravity_oblique_route_viable"],
        "good_region_is_wide": study["good_region_is_wide"],
        "accepted_internal_yaw_plateau_deg": study["accepted_internal_yaw_plateau_deg"],
        "image_proxy_good_region": study["image_proxy_good_region"],
        "frontal": study["per_internal_yaw"]["0"],
        "first_accepted": study["per_internal_yaw"][str(study["accepted_internal_yaw_plateau_deg"][0])],
        "gravity_fixed_fit_end_to_end": {
            key: value for key, value in study["gravity_fixed_fit_end_to_end"].items() if key != "trials"
        },
        "gravity_yaw_invariance": study["gravity_yaw_invariance"],
    }
    write_json(EVIDENCE_DIR / "math" / "FOCUSED_MATH_EVIDENCE.json", focused_math)

    registry_path = WORKSPACE / "observations" / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8-sig"))
    registry_evidence = {
        "registry_path": str(registry_path),
        "registry_sha256": sha256(registry_path),
        "gravity_authority_registry_exists": (WORKSPACE / "observations" / "gravity_authority_registry.json").exists(),
        "real_gravity_candidate_exists": (WORKSPACE / "candidate" / "camera_gravity.stage3a_candidate.v1.json").exists(),
        "poses": {
            pose: {
                "scan_id": binding["scan_id"],
                "scan_path": binding["scan_path"],
                "source_hash_count": len(binding["source_hashes"]),
                "board_mode": binding["board_mode"],
            }
            for pose, binding in registry["poses"].items()
        },
    }
    write_json(EVIDENCE_DIR / "registry" / "W01_W02_W03_REGISTRY_EVIDENCE.json", registry_evidence)

    protected_expected = {
        Path(r"C:\ec\_calibration_workspaces\stage2_final_bundle_20260818_01\calibration_bundle.stage2_final_candidate.v1.json"): "63c974cd2ff64dd0f135d47d8c92eb170d06658cb315563fb93588862ba8b057",
        WORKSPACE / "stage3a_camera_world.py": "8aa81cb4e6a0d176050232c3acf01fa91c682c96919c4e9a38fd661c3798b2ef",
        WORKSPACE / "stage3a_straightness_score.py": "f5a1af5d8f9db6cb00101ca5a11cef182ac350ef17e66965e3caf14dc15a40af",
        WORKSPACE / "stage3a_oblique_gravity_score.py": "20e4b7113888a05a057f4c3b02a70a707ce5c9ad0e3eb67fd23c664ed9e673cf",
        registry_path: "77219b55b4a1716b6bc51c643a84d5a1b20171e3565f323cb443b2245b9f7050",
        Path(r"C:\calibration\stage7c_world_result_robustfix_20260724_044035\camera_world_pose.v1.json"): "5c730171f850ff3e05ab777eecca9849714aa20376c6d1f001da2b378ad24619",
        Path(r"C:\ec\_deltar_p4_pipeline\src\deltar_p4_pipeline\camera_world_calibration.py"): "6b6726082a97b7a071f3cd994d1ead80c2ccfee9b9f235bea847b1ccdacaadd0",
        Path(r"C:\ec\_deltar_p4_pipeline\src\deltar_p4_pipeline\camera_world_contracts.py"): "e6a7f30b253a39e9d0064064c25dcb8ca4cd92d9455e440e9f2491e2c690299c",
        Path(r"C:\ec\_deltar_p4_pipeline\src\deltar_p4_pipeline\camera_world_outputs.py"): "965a2be2a5b085b51ca9582273f7d1b144ab43da505634f0c4e8a569e0b7b82e",
    }
    checks = []
    for path, expected_sha256 in protected_expected.items():
        actual_sha256 = sha256(path)
        checks.append(
            {
                "path": str(path),
                "expected_sha256": expected_sha256,
                "after_sha256": actual_sha256,
                "exists": path.is_file(),
                "unchanged": actual_sha256 == expected_sha256,
            }
        )
    protected_after = {
        "schema_version": "stage3a_gravity_authority_exec_protected_after.v1",
        "formal_runtime_modified": False,
        "all_expected_unchanged": all(check["unchanged"] for check in checks),
        "checks": checks,
    }
    write_json(EVIDENCE_DIR / "hashes" / "PROTECTED_HASHES_AFTER.json", protected_after)

    scorer_integration = {
        "imports_existing_oblique_scorer": True,
        "oblique_scorer_path": str(WORKSPACE / "stage3a_oblique_gravity_score.py"),
        "oblique_scorer_sha256": sha256(WORKSPACE / "stage3a_oblique_gravity_score.py"),
        "reused_functions": ["aggregate_analysis", "solve_frame", "selected_solution", "gravity_fixed_fit", "validate_height"],
        "duplicate_scorer_algorithm_created": False,
        "preview_good_is_authority_ready": False,
        "scan_good_plus_consensus_required": True,
    }
    write_json(EVIDENCE_DIR / "integration" / "SCORER_INTEGRATION.json", scorer_integration)

    excluded_parts = {"__pycache__", ".git", "build", "runtime_data", "models"}
    tree = []
    for path in sorted(WORKSPACE.rglob("*")):
        relative = path.relative_to(WORKSPACE)
        if any(part in excluded_parts for part in relative.parts):
            continue
        if path.is_file():
            tree.append(str(relative).replace("\\", "/"))
    (EVIDENCE_DIR / "WORKSPACE_TREE.txt").write_text("\n".join(tree) + "\n", encoding="utf-8")

    files = [path for path in EVIDENCE_DIR.rglob("*") if path.is_file()]
    manifest = {
        "schema_version": "stage3a_gravity_authority_exec_evidence_manifest.v1",
        "sanitized_allowlist_bundle": True,
        "raw_rgb565_included": False,
        "raw_tof_included": False,
        "models_included": False,
        "credentials_or_secrets_included": False,
        "real_w04g_registered": False,
        "real_gravity_candidate_exists": False,
        "files": [
            {"path": str(path.relative_to(EVIDENCE_DIR)).replace("\\", "/"), "size": path.stat().st_size, "sha256": sha256(path)}
            for path in sorted(files)
        ],
    }
    write_json(EVIDENCE_DIR / "EVIDENCE_MANIFEST.json", manifest)

    with zipfile.ZipFile(ZIP_PATH, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in EVIDENCE_DIR.rglob("*") if item.is_file()):
            archive.write(path, path.relative_to(EVIDENCE_DIR).as_posix())
    print(json.dumps({"evidence_zip_created": True, "path": str(ZIP_PATH), "sha256": sha256(ZIP_PATH)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
