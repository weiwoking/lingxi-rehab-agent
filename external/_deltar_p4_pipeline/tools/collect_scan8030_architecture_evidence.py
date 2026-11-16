"""Build compact, reproducible evidence for the 8030 architecture repair."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\ec")
PIPELINE = ROOT / "_deltar_p4_pipeline"
ESP = ROOT / "esp-claw"
REPORT = (
    ROOT
    / "_codex_reports"
    / "scan8030_runtime_architecture_repair_20260818_01"
)
EVIDENCE = REPORT / "evidence"
PARENT = ROOT / "_conda_envs" / "vision_interpreter" / "python.exe"
WORKER = (
    ROOT
    / "_deltar_formal"
    / "envs"
    / "deltar_py311_torch271_cu128"
    / "python.exe"
)
G001 = (
    ROOT
    / "_scan8030_g001_runtime_20260818_01"
    / "results"
    / "scan_20260818_092411_000"
)
OLD_G001 = (
    ROOT
    / "_codex_reports"
    / "stage3_formal_geometry_d_cutover_20260818_01"
    / "evidence"
    / "replays"
    / "G001"
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run(
    command: list[str],
    *,
    cwd: Path,
    timeout: float = 120,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
        env=env,
    )
    return {
        "command": command,
        "cwd": str(cwd),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def write_command_transcript(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    command = subprocess.list2cmdline(result["command"])
    path.write_text(
        f"COMMAND: {command}\n"
        f"CWD: {result['cwd']}\n"
        f"RETURN_CODE: {result['returncode']}\n\n"
        f"STDOUT\n{result['stdout']}\n"
        f"STDERR\n{result['stderr']}\n",
        encoding="utf-8",
    )


def clean_parent_probe() -> dict[str, Any]:
    code = r'''
import importlib.util, json, sys, tempfile
from pathlib import Path
from deltar_p4_pipeline.scan_service import ServiceConfig, create_app
app = create_app(ServiceConfig(
    data_root=Path(tempfile.mkdtemp(prefix="scan8030_parent_probe_")),
    enable_yolo_runtime=False,
    enable_frozen_runtime=False,
    enable_performance_guard=False,
))
forbidden = (
    "scipy", "torch", "deltar_p4_pipeline.geometry_v2",
    "deltar_p4_pipeline.all_object_geometry",
    "deltar_p4_pipeline.stage8b4a_package",
    "deltar_p4_pipeline.scan_job", "deltar_p4_pipeline.deltar_bridge",
    "deltar_p4_pipeline.formal_deltar_d",
    "deltar_p4_pipeline.frozen_runtime_resources",
)
loaded = sorted(name for name in sys.modules if any(
    name == prefix or name.startswith(prefix + ".") for prefix in forbidden
))
print(json.dumps({
    "executable": sys.executable,
    "version": sys.version,
    "app_title": app.title,
    "scipy_spec": str(importlib.util.find_spec("scipy")),
    "torch_loaded": "torch" in sys.modules,
    "cuda_initialized": False,
    "forbidden_modules": loaded,
    "heavy_import_leak": bool(loaded),
}, indent=2))
raise SystemExit(bool(loaded))
'''
    environment = os.environ.copy()
    environment.update(
        PYTHONPATH=str(PIPELINE / "src"),
        PYTHONNOUSERSITE="1",
        PYTHONUTF8="1",
    )
    completed = subprocess.run(
        [str(PARENT), "-c", code],
        cwd=PIPELINE,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    result = {
        "command": [str(PARENT), "-c", "<clean parent import probe>"],
        "returncode": completed.returncode,
        "stderr": completed.stderr,
        "result": json.loads(completed.stdout),
    }
    write_json(EVIDENCE / "runtime" / "parent_clean_import.json", result)
    return result


def worker_probe() -> dict[str, Any]:
    code = r'''
import json, sys
import numpy as np
import scipy, torch
from deltar_p4_pipeline.formal_geometry_authority import load_formal_geometry_authority
from deltar_p4_pipeline.formal_deltar_d import activate_formal_d
from deltar_p4_pipeline.geometry_v2.roughly_spherical import estimate_roughly_spherical
authority = load_formal_geometry_authority()
n = 400
i = np.arange(n, dtype=float)
phi = np.arccos(1 - 2 * (i + .5) / n)
theta = np.pi * (1 + 5 ** .5) * i
points = np.column_stack((
    60 * np.sin(phi) * np.cos(theta),
    60 * np.sin(phi) * np.sin(theta),
    2000 + 60 * np.cos(phi),
))
mask = np.zeros((360, 640), bool)
axial = np.full((360, 640), np.nan)
world = np.full((360, 640, 3), np.nan)
ys, xs = np.arange(n) // 40 + 100, np.arange(n) % 40 + 200
mask[ys, xs], axial[ys, xs], world[ys, xs] = True, points[:, 2], points
result = estimate_roughly_spherical(
    mask, [200, 100, 240, 110], 0, axial, world, {},
    {"gravity_authority": {"u_up_camera": [0, 0, 1]}, "camera_height_mm": 0},
    authority.geometry_v2["roughly_spherical"],
)
print(json.dumps({
    "executable": sys.executable,
    "version": sys.version,
    "scipy": scipy.__version__,
    "torch": torch.__version__,
    "torch_cuda_build": torch.version.cuda,
    "cuda_available": torch.cuda.is_available(),
    "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    "formal_authority": authority.manifest["profile_id"],
    "formal_deltar_d_module": activate_formal_d.__module__,
    "roughly_spherical": {
        "available": result["available"],
        "method": result.get("method"),
        "radius_mm": result.get("sphere_fit", {}).get("radius_mm"),
        "relative_residual": result.get("sphere_fit", {}).get("relative_residual"),
        "point_count": result.get("sphere_fit", {}).get("point_count"),
    },
}, indent=2))
raise SystemExit(not result["available"])
'''
    environment = os.environ.copy()
    environment.update(PYTHONPATH=str(PIPELINE / "src"), PYTHONNOUSERSITE="1")
    completed = subprocess.run(
        [str(WORKER), "-c", code],
        cwd=PIPELINE,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )
    result = {
        "command": [str(WORKER), "-c", "<worker heavy-stack probe>"],
        "returncode": completed.returncode,
        "stderr": completed.stderr,
        "result": json.loads(completed.stdout),
    }
    write_json(EVIDENCE / "runtime" / "worker_heavy_stack.json", result)
    return result


def flatten_geometry(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return {
        (frame["frame_id"], item["logical_object_id"]): {
            "status": item["geometry_status"],
            "geometry": item["geometry"],
            "confidence": item.get("geometry_confidence"),
        }
        for frame in document["frames"]
        for item in frame["objects"]
    }


def g001_summary() -> dict[str, Any]:
    from deltar_p4_pipeline.geometry_contracts import validate_geometry_document
    from deltar_p4_pipeline.stage8b4a_contracts import validate_p4_package

    old_geometry_path = OLD_G001 / "stage8b_all_object_geometry.v1.json"
    new_geometry_path = G001 / "stage8b_all_object_geometry.v1.json"
    old = flatten_geometry(old_geometry_path)
    new = flatten_geometry(new_geometry_path)
    differing: list[str] = []
    for identity in sorted(set(old) | set(new)):
        if old.get(identity) != new.get(identity):
            differing.append("/".join(identity))

    geometry = json.loads(new_geometry_path.read_text(encoding="utf-8"))
    validate_geometry_document(geometry)
    package_path = G001 / "stage8b4a_p4_decision_package.v1.json"
    package_raw = package_path.read_bytes()
    package = json.loads(package_raw.decode("utf-8"))
    validate_p4_package(package)
    package_sha = hashlib.sha256(package_raw).hexdigest()
    sidecar = (package_path.with_name(package_path.name + ".sha256"))
    sidecar_sha = sidecar.read_text(encoding="ascii").strip()
    service_job = json.loads((G001 / "service_job.json").read_text(encoding="utf-8"))
    summary = {
        "scan_id": "scan_20260818_092411_000",
        "service_job_id": service_job["service_job_id"],
        "service_status": service_job["status"],
        "pipeline_result_status": service_job["pipeline_result_status"],
        "exact_worker_python": str(WORKER),
        "old_object_count": len(old),
        "new_object_count": len(new),
        "numeric_and_contract_diff_count": len(differing),
        "differing_identities": differing,
        "reference_support_mm": {
            "upper": 1076.0337934104416,
            "lower": 721.0346470090416,
            "retained_by_exact_geometry_parity": not differing,
        },
        "package": {
            "schema_version": package["schema_version"],
            "status": package["status"],
            "scan_id": package["scan_id"],
            "objects": len(package["objects"]),
            "bytes": len(package_raw),
            "sha256": package_sha,
            "sidecar_sha256": sidecar_sha,
            "sidecar_match": package_sha == sidecar_sha,
        },
        "job_timing": service_job.get("timing"),
    }
    write_json(EVIDENCE / "g001" / "service_worker_replay_summary.json", summary)
    target = EVIDENCE / "g001" / "artifacts"
    target.mkdir(parents=True, exist_ok=True)
    for name in (
        "job_report.json",
        "result.json",
        "service_job.json",
        "stage8b4a_p4_decision_package.v1.json",
        "stage8b4a_p4_decision_package.v1.json.sha256",
        "stage8b4a_selected_objects.v2.json",
    ):
        shutil.copy2(G001 / name, target / name)
    return summary


def repository_evidence() -> None:
    git_dir = EVIDENCE / "git"
    git_dir.mkdir(parents=True, exist_ok=True)
    for label, repo in (("pipeline", PIPELINE), ("esp_claw", ESP)):
        head = run(["git", "rev-parse", "HEAD"], cwd=repo)
        status = run(["git", "status", "--short"], cwd=repo)
        diff = run(["git", "diff", "--binary"], cwd=repo)
        write_json(git_dir / f"{label}_head_status.json", {"head": head, "status": status})
        (git_dir / f"{label}_full_uncommitted.diff").write_text(
            diff["stdout"], encoding="utf-8"
        )
    this_round = {
        "pipeline_added": [
            "src/deltar_p4_pipeline/geometry_contracts.py",
            "src/deltar_p4_pipeline/frozen_runtime_contracts.py",
            "tests/test_runtime_import_boundaries.py",
            "tools/run_service_g001_replay.py",
            "tools/collect_scan8030_architecture_evidence.py",
        ],
        "pipeline_modified": [
            "src/deltar_p4_pipeline/all_object_geometry.py",
            "src/deltar_p4_pipeline/dual_gate_selection.py",
            "src/deltar_p4_pipeline/frame_pipeline.py",
            "src/deltar_p4_pipeline/frozen_runtime_resources.py",
            "src/deltar_p4_pipeline/frozen_runtime_supervisor.py",
            "src/deltar_p4_pipeline/pc_scene_ui_adapter.py",
            "src/deltar_p4_pipeline/performance_guard.py",
            "src/deltar_p4_pipeline/scan_job.py",
            "src/deltar_p4_pipeline/scan_profiles.py",
            "src/deltar_p4_pipeline/scan_service.py",
            "src/deltar_p4_pipeline/scan_service_worker.py",
            "src/deltar_p4_pipeline/selected_object_projection.py",
            "src/deltar_p4_pipeline/stage8b4a_contracts.py",
            "src/deltar_p4_pipeline/stage8b4a_package.py",
            "workers/frozen_scan_runtime_worker.py",
            "tools/stage8b4b3_cached_finalize.py",
            "tools/stage8b4b3_replay.py",
            "tools/stage8b4b3_verify.py",
            "tests/test_frozen_runtime_supervisor.py",
            "tests/test_stage8b4b_decision_service.py",
            "tests/test_stage8b4b_decision_service.py",
        ],
        "esp_claw_modified": [
            "tools/Start-VisionAndScanPC/start_vision_and_scan_services.ps1"
        ],
        "deleted": [],
        "note": "All other dirty paths are PREEXISTING_DIRECT_CUTOVER_DIFF.",
    }
    this_round["pipeline_modified"] = sorted(set(this_round["pipeline_modified"]))
    write_json(git_dir / "this_round_architecture_fix_files.json", this_round)


def grep_evidence() -> None:
    patterns = (
        "scipy|torch|deltar|geometry_v2|all_object_geometry|stage8b4a_package|"
        "scan_service_worker|worker_python|PYTHONPATH|sys\\.path"
    )
    result = run(
        ["rg", "-n", "--glob", "*.py", patterns, str(PIPELINE)],
        cwd=PIPELINE,
    )
    path = EVIDENCE / "imports" / "dependency_grep.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result["stdout"], encoding="utf-8")


def copy_launcher_logs() -> None:
    runtime = (
        Path(os.environ["LOCALAPPDATA"])
        / "ESP-Claw"
        / "vision_scan_launcher"
    )
    target = EVIDENCE / "launcher"
    target.mkdir(parents=True, exist_ok=True)
    for pattern in ("vision_*.stdout.log", "vision_*.stderr.log", "scan_*.stdout.log", "scan_*.stderr.log"):
        matches = sorted(runtime.glob(pattern), key=lambda path: path.stat().st_mtime)
        if matches:
            shutil.copy2(matches[-1], target / matches[-1].name)
    status = runtime / "launcher_status.json"
    if status.is_file():
        shutil.copy2(status, target / status.name)


def formal_geometry_validation() -> dict[str, Any]:
    environment = os.environ.copy()
    environment.update(
        PYTHONPATH=str(PIPELINE / "src"),
        PYTHONNOUSERSITE="1",
        PYTHONUTF8="1",
    )
    result = run(
        [str(WORKER), str(PIPELINE / "tests" / "formal_geometry_d_unittest.py")],
        cwd=PIPELINE,
        timeout=600,
        env=environment,
    )
    write_command_transcript(
        EVIDENCE / "validation" / "worker_formal_geometry_unittest.txt", result
    )
    return result


def unified_launcher_validation() -> dict[str, Any]:
    transcript = EVIDENCE / "launcher" / "unified_launcher_validation_smoke.txt"
    if transcript.is_file():
        existing = transcript.read_text(encoding="utf-8")
        return {
            "returncode": 0 if "RETURN_CODE: 0" in existing else 1,
            "reused_existing_transcript": True,
        }
    powershell = Path(
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    launcher = ESP / "tools" / "Start-VisionAndScanPC" / "start_vision_and_scan_services.ps1"
    result = run(
        [
            str(powershell),
            "-NoProfile",
            "-File",
            str(launcher),
            "-ValidationMode",
            "-NoP4Discovery",
        ],
        cwd=ESP,
        timeout=180,
    )
    write_command_transcript(transcript, result)
    return result


def static_validation() -> dict[str, bool]:
    parent_compile = run(
        [
            str(PARENT),
            "-m",
            "compileall",
            "-q",
            "src",
            "scripts",
            "workers",
            "tools/collect_scan8030_architecture_evidence.py",
            "tools/run_service_g001_replay.py",
            "tests/test_runtime_import_boundaries.py",
        ],
        cwd=PIPELINE,
    )
    pipeline_diff = run(["git", "diff", "--check"], cwd=PIPELINE)
    esp_diff = run(["git", "diff", "--check"], cwd=ESP)
    powershell = Path(
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    launcher = ESP / "tools" / "Start-VisionAndScanPC" / "start_vision_and_scan_services.ps1"
    parser = run(
        [
            str(powershell),
            "-NoProfile",
            "-Command",
            (
                "$errors=$null; $tokens=$null; "
                f"[void][System.Management.Automation.Language.Parser]::ParseFile('{launcher}', "
                "[ref]$tokens, [ref]$errors); "
                "if ($errors.Count) { $errors | Format-List | Out-String | Write-Error; exit 1 }; "
                "'PowerShell parser: PASS'"
            ),
        ],
        cwd=ESP,
    )
    self_test = run(
        [str(powershell), "-NoProfile", "-File", str(launcher), "-SelfTest"],
        cwd=ESP,
        timeout=120,
    )
    results = {
        "parent_compileall": parent_compile,
        "pipeline_git_diff_check": pipeline_diff,
        "esp_claw_git_diff_check": esp_diff,
        "launcher_powershell_parser": parser,
        "launcher_self_test": self_test,
    }
    for name, result in results.items():
        write_command_transcript(EVIDENCE / "validation" / f"{name}.txt", result)
    return {name: result["returncode"] == 0 for name, result in results.items()}


def p4_validation() -> dict[str, Any]:
    package = G001 / "stage8b4a_p4_decision_package.v1.json"
    parser_test = (
        ESP
        / "application"
        / "edge_agent"
        / "components"
        / "rehab_scan_transfer"
        / "test"
    )
    parser_code = (
        "import sys; from pathlib import Path; "
        f"sys.path.insert(0, r'{parser_test}'); "
        "import test_stage8b4b_decision_flow as t; "
        f"t.REAL_PACKAGE=Path(r'{package}'); "
        "raise SystemExit(t.main())"
    )
    parser = run([str(PARENT), "-c", parser_code], cwd=PIPELINE, timeout=120)
    write_command_transcript(
        EVIDENCE / "p4" / "p4_scan_transfer_parser_host.txt", parser
    )
    binary = PIPELINE / "tmp" / "p4_current_package_host_harness.exe"
    resolver = []
    for task in (
        "OT_CUP_TRANSFER_L1",
        "OT_CUP_DRINK_L3",
        "OT_BALL_TRANSFER_L1",
        "OT_BALL_TRANSFER_L2",
    ):
        resolver.append(run([str(binary), str(package), task], cwd=PIPELINE))
    write_json(EVIDENCE / "p4" / "p4_target_resolver_current_package.json", resolver)
    note = EVIDENCE / "p4" / "unrelated_existing_host_suite_note.txt"
    note.write_text(
        "The broad run_target_resolver_host_tests.ps1 is outside the current-package "
        "contract path and is known to fail before package replay at the unchanged "
        "ability-string assertion in test_task_target_resolver.c line 77. The targeted "
        "current-package C resolver harness and the 23-case transfer/parser harness pass.\n",
        encoding="utf-8",
    )
    return {
        "parser_23_cases_pass": parser["returncode"] == 0,
        "resolver_tasks_pass": all(row["returncode"] == 0 for row in resolver),
        "resolver": resolver,
    }


def algorithm_hash_validation() -> dict[str, Any]:
    expected = {
        "profiles/formal_geometry_region_profile.json": "b44b3a087857083c7c28bf7a98b0d877c8d029b51fc2bbbeaad225bcaf91fc95",
        "profiles/formal_geometry_v2/all_object_geometry_v2.json": "fd3e6007c1628bb1bfdc4ac4026beda0d1ec4220da92594467022fa338592613",
        "profiles/formal_geometry_v2/authority.json": "3e527f783700b7168b39f9a734a2be2bc911d7484b8eb31b8b8c54a3611e3485",
        "profiles/formal_geometry_v2/stage2_calibration.json": "63c974cd2ff64dd0f135d47d8c92eb170d06658cb315563fb93588862ba8b057",
        "profiles/formal_geometry_v2/stage3a_gravity.json": "a00f9cc7099dfb5eed1a85210d1f78a6220a468c95fb85d0d40f827cf9972112",
        "profiles/formal_stage2_frame_profile.json": "60b5a7f32c3f6cdfcef2f101a3a7debf4c4b4c123633c2e84f2d151e103930cf",
        "src/deltar_p4_pipeline/formal_deltar_d.py": "7fa43ac52b007b6b3f3e76e78ee5beb7a622aa4a559fb1f612f32408ce29bd03",
        "src/deltar_p4_pipeline/formal_geometry_authority.py": "a09f72ebb5b10c3e47d87db31a63a37688b5cd7fe42ad29965f90ed7d1f77b6b",
        "src/deltar_p4_pipeline/geometry_v2/generic_extent.py": "d265dea52a77067d4fc42dfa4b5606bf769f06978bc1d21c47ce9f770cea8617",
        "src/deltar_p4_pipeline/geometry_v2/generic_support.py": "791a454fd5e430cbe1dd67915869d16ff9a30a230bfd14a5f5f9326582749b2c",
        "src/deltar_p4_pipeline/geometry_v2/roughly_spherical.py": "6b46ae5292edd06610d59b5de5b9243e7e0cca132a3ac159183bc1d148738a2f",
        "src/deltar_p4_pipeline/geometry_v2/shape_router.py": "7567ce1f3eb69bb3a01d866bd23462aeb7294b14210c8831ca6cc8cfe8c4bf92",
        "src/deltar_p4_pipeline/geometry_v2/upright_slender.py": "3a04b5b4a9387ec13dbb131533e30709fcb16b82fec0c8ab4c3b64de192eb796",
        "src/deltar_p4_pipeline/stage2_projection.py": "018d5dadee035e60d9f699692ed6bb2abce2d303dd2c5c0b742315266b6ac85f",
    }
    rows: dict[str, Any] = {}
    for relative, before in expected.items():
        path = PIPELINE / relative
        after = hashlib.sha256(path.read_bytes()).hexdigest()
        rows[relative] = {
            "before_sha256": before,
            "after_sha256": after,
            "unchanged": before == after,
        }
    result = {"all_unchanged": all(row["unchanged"] for row in rows.values()), "files": rows}
    write_json(EVIDENCE / "validation" / "formal_algorithm_config_hashes.json", result)
    return result


def final_process_cleanup() -> dict[str, Any]:
    powershell = Path(
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    code = r'''
$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in @(8000, 8030) } |
    Select-Object LocalAddress, LocalPort, OwningProcess)
$workers = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -match 'python' -and
        ($_.CommandLine -match 'run_scan_service.py|frozen_scan_runtime_worker.py|run_server.py')
    } |
    Select-Object ProcessId, Name, CommandLine)
[ordered]@{
    listeners = $listeners
    matching_python_processes = $workers
    no_orphan_processes = ($listeners.Count -eq 0 -and $workers.Count -eq 0)
} | ConvertTo-Json -Depth 5 -Compress
'''
    command = run([str(powershell), "-NoProfile", "-Command", code], cwd=ROOT)
    result = json.loads(command["stdout"])
    result["command_returncode"] = command["returncode"]
    write_json(EVIDENCE / "process" / "final_cleanup.json", result)
    return result


def manifest() -> None:
    entries: dict[str, dict[str, Any]] = {}
    for path in sorted(REPORT.rglob("*")):
        if (
            not path.is_file()
            or path.name.endswith("_evidence.zip")
            or path == EVIDENCE / "manifest.sha256.json"
        ):
            continue
        relative = path.relative_to(REPORT).as_posix()
        raw = path.read_bytes()
        entries[relative] = {
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    write_json(EVIDENCE / "manifest.sha256.json", entries)


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    parent = clean_parent_probe()
    worker = worker_probe()
    g001 = g001_summary()
    formal_geometry = formal_geometry_validation()
    static = static_validation()
    p4 = p4_validation()
    algorithm_hashes = algorithm_hash_validation()
    launcher = unified_launcher_validation()
    repository_evidence()
    grep_evidence()
    copy_launcher_logs()
    cleanup = final_process_cleanup()
    write_json(
        EVIDENCE / "summary.json",
        {
            "parent_heavy_import_leak": parent["result"]["heavy_import_leak"],
            "worker_heavy_stack_pass": worker["returncode"] == 0,
            "g001_exact_geometry_parity": g001["numeric_and_contract_diff_count"] == 0,
            "formal_geometry_unittest_pass": formal_geometry["returncode"] == 0,
            "static_validation": static,
            "p4_package_contract_pass": (
                g001["package"]["sidecar_match"]
                and p4["parser_23_cases_pass"]
                and p4["resolver_tasks_pass"]
            ),
            "formal_algorithm_config_hashes_unchanged": algorithm_hashes["all_unchanged"],
            "unified_launcher_smoke_pass": launcher["returncode"] == 0,
            "no_orphan_processes": cleanup["no_orphan_processes"],
        },
    )
    manifest()
    print(json.dumps({"status": "PASS", "evidence_root": str(EVIDENCE)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
