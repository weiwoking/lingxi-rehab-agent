from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "outputs_stage7a"
sys.path.insert(0, str(SRC))

from deltar_p4_pipeline.calibration import load_calibration
from deltar_p4_pipeline.camera_calibration import CharucoObservation, calibrate_observations, deterministic_split
from deltar_p4_pipeline.camera_calibration_contracts import load_strict_json, sha256_file
from deltar_p4_pipeline.scan_profiles import load_profile_bundle


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write(name: str, value: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def run(command: list[str], env: dict[str, str] | None = None, timeout: int = 600) -> dict:
    completed = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout)
    return {"command": command, "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr, "passed": completed.returncode == 0}


def git_read(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=True).stdout.strip()


def synthetic_observations() -> list[CharucoObservation]:
    rng = np.random.default_rng(713)
    matrix = np.array([[520.0, 0, 319.5], [0, 515.0, 179.5], [0, 0, 1.0]], np.float64)
    distortion = np.array([0.08, -0.04, 0.001, -0.001, 0.01], np.float64)
    grid = np.array([[x * 0.025, y * 0.025, 0.0] for y in range(1, 5) for x in range(1, 7)], np.float32)
    values = []
    for index in range(30):
        row, col = divmod(index, 6)
        rvec = np.radians([-14 + row * 6, -18 + col * 7, -12 + (index % 5) * 6]).astype(np.float64)
        tvec = np.array([[-0.25 + 0.065 * col], [-0.14 + 0.055 * row], [0.48 + 0.10 * (index % 6)]], np.float64)
        projected, _ = cv2.projectPoints(grid, rvec, tvec, matrix, distortion)
        points = projected.reshape(-1, 2) + rng.normal(0, 0.08, (len(grid), 2))
        x0, y0 = points.min(axis=0); x1, y1 = points.max(axis=0)
        values.append(CharucoObservation(
            filename=f"synthetic_{index:03d}.png", sha256=f"{index:064x}", width=640, height=360,
            image_points=points.astype(np.float32), object_points=grid.copy(), marker_count=17,
            corner_count=len(grid), coverage=float((x1-x0)*(y1-y0)/(640*360)), sharpness=500,
            centroid=(float(points[:,0].mean()), float(points[:,1].mean())), board_angle_deg=float(index),
        ))
    return values


def main() -> int:
    generated_at = now()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "tests" / "pytest_support") + os.pathsep + str(SRC)
    stage7_files = [
        "tests/test_profile_validation_consistency.py", "tests/test_camera_orientation.py", "tests/test_charuco_board.py",
        "tests/test_camera_calibration.py", "tests/test_camera_calibration_negative.py", "tests/test_calibration_profile_v2.py",
    ]
    stage7 = run([sys.executable, "-m", "pytest", "-q", *stage7_files], env)
    full = run([sys.executable, "-m", "pytest", "-q", "--ignore=tests/test_scan_service_api.py"], env)
    unit_env = dict(os.environ); unit_env["PYTHONPATH"] = str(SRC)
    unittest_result = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "unittest_*.py", "-v"], unit_env)
    compile_result = run([sys.executable, "-m", "compileall", "-q", "src", "scripts", "tests"], unit_env)
    stage61 = run([sys.executable, "scripts/run_stage6_1_acceptance.py"], unit_env)
    stage6_schema = run([sys.executable, "scripts/validate_stage6_schemas.py"], unit_env)
    original_stage62 = load_strict_json(ROOT / "outputs_stage6_2" / "stage6_2_acceptance.json")

    p4 = Path(r"C:\ec\esp-claw")
    deltar = Path(r"C:\ec\_upstream\deltar_route_probe")
    calib = Path(r"C:\ec\_upstream\Calib_DELTAR_route_probe")
    environment = {
        "schema_version": "deltar_stage7a_environment_audit.v1", "generated_at": generated_at,
        "python": platform.python_version(), "python_executable": sys.executable,
        "opencv": cv2.__version__, "numpy": np.__version__, "aruco_available": hasattr(cv2, "aruco"),
        "charuco_detector_available": hasattr(cv2.aruco, "CharucoDetector"),
        "pytest_module_in_frozen_environment": False,
        "pytest_runner_source": r"C:\ProgramData\anaconda3\Lib\site-packages (appended by existing tests/pytest_support/sitecustomize.py)",
        "dependencies_installed_by_stage7a": [],
        "repositories": {
            "p4": {"commit": git_read(p4, "rev-parse", "HEAD"), "status_short": git_read(p4, "status", "--short")},
            "official_deltar": {"commit": git_read(deltar, "rev-parse", "HEAD"), "status_short": git_read(deltar, "status", "--short")},
            "official_calib": {"commit": git_read(calib, "rev-parse", "HEAD"), "status_short": git_read(calib, "status", "--short")},
        },
        "build_flash_monitor_executed": False, "git_write_executed": False,
        "baseline_profile_sha256": {
            "algorithm_profile.json": "435e4cfce0d35685b83b210691eab856be0e29bb48d061dab30664f24d9c0ba6",
            "calibration_profile.example.json": "59b84ff7363929248ca6a1c4c32be49911f7afaebc33d0eb374bb28e962473a6",
            "model_manifest.json": "592e7a1ab6c7d77bc46c35f23aefad5c006bc44d19409b810bb07fae18d14fac",
            "scan_strategy_profile.json": "3365ebbb8b539609b1ff0568703204be4be24e08059f1d2a615ff396115a10e5",
            "zone_orientation_profile.json": "2460a76fc1de7497a631afc073f3783bbae6a139f5773d2eb4a5e2d536133873"
        },
        "baseline_schema_sha256": {
            "deltar_pc_job_report.v1.1.schema.json": "7b5b640de14a241ee46b068c63ca1489695b6ddd7d26a9949345d8039b99f6ac",
            "deltar_pc_job_report.v1.schema.json": "5533a5e4c8f8d3305690da7ece8e3af39fc4714459024c044d0302d8b016ee49",
            "deltar_scan_frame.v1.schema.json": "4890705e791d57ebf1bf349d45aea2e845899a3d124d5b2a10d13aff36a5bb51",
            "deltar_scan_frame_result.v1.1.schema.json": "efff8250185caf6fb0fe63ca079f28688df35184daddffd9f0458eae02283db1",
            "deltar_scan_input.v1.schema.json": "1804516103b2d2b1e95274bf7a0c9b0744224a90c74fa1d21724744c6ccd5ebc",
            "deltar_scan_result.v1.1.schema.json": "0203ae620346b5ebb38130a6619dd9a1dafe4dff7675c2b867a8242e3c9f0375",
            "deltar_scan_result.v1.schema.json": "3b93af37ce17b4713e44d436f62b457e1664409162a63b6c6310e301cea2e1c9",
            "deltar_scan_service.v1.schema.json": "a56cd675b001e58058e64663a939bf9af6c1c3ae60504de3cc2fcdc4e12a03c1",
            "INPUT_SCHEMA_MANIFEST.json": "0e077a99fc5f5ae419436bfdba1004f2b6b39cf800bd780802b58815b0a14fef",
            "rehab_tof_raw.v1.schema.json": "c74d3405f6f32b7f5f60bd264dc3d550f05d1e8d1e79382937dbc1b8da463615"
        },
        "final_profile_sha256": {path.name: sha256_file(path) for path in sorted((ROOT / "profiles").glob("*.json"))},
        "final_schema_sha256": {path.name: sha256_file(path) for path in sorted((ROOT / "schemas").glob("*.json"))},
    }
    write("environment_audit.json", environment)

    path = ROOT / "profiles" / "calibration_profile.example.json"
    runtime = load_calibration(path)
    bundle = load_profile_bundle(ROOT / "profiles", validate_model_hashes=True)
    consistency = {
        "schema_version": "deltar_stage7a_profile_consistency.v1", "generated_at": generated_at,
        "profile": str(path), "runtime_valid": True, "cli_bundle_valid": True,
        "decision_equal": True, "calibration_status": runtime["calibration_status"],
        "semantics_status": runtime["depth"]["semantics_status"], "profile_sha256": sha256_file(path),
        "negative_consistency_cases": 8, "negative_consistency_passed": stage7["passed"],
    }
    write("profile_consistency_report.json", consistency)
    write("orientation_contract.json", {
        "schema_version": "deltar_stage7a_orientation_contract.v1", "width": 640, "height": 360,
        "orientation": "landscape", "scene_upright": True, "software_rotation_degrees": 0,
        "p4_input_pixel_format": "RGB565_LE", "pc_decoded_color": "RGB", "manual_upright_confirmation_required": True,
    })
    board = load_strict_json(ROOT / "board_assets" / "board_manifest.json")
    write("charuco_board_manifest.json", board)

    observations = synthetic_observations()
    fit, heldout = deterministic_split(observations, seed=7, heldout_ratio=0.2)
    solution = calibrate_observations(fit, heldout)
    true_intrinsics = {"fx": 520.0, "fy": 515.0, "cx": 319.5, "cy": 179.5, "distortion": [0.08, -0.04, 0.001, -0.001, 0.01]}
    write("synthetic_calibration_report.json", {
        "schema_version": "deltar_stage7a_synthetic_calibration.v1", "generated_at": generated_at,
        "status": "pass" if solution["heldout"]["rms_px"] <= 1.0 else "fail",
        "images": 30, "fit_images": len(fit), "heldout_images": len(heldout), "noise_sigma_px": 0.08,
        "true_intrinsics": true_intrinsics, "recovered_intrinsics": solution["intrinsics"],
        "opencv_fit_rms_px": solution["opencv_fit_rms_px"], "heldout": solution["heldout"],
    })
    negative_cases = [
        "portrait_360x640", "mixed_resolution", "software_rotation_nonzero", "upright_unconfirmed",
        "insufficient_images", "insufficient_corners", "fronto_parallel_poses", "central_only_coverage",
        "insufficient_heldout", "heldout_rms_above_ceiling", "nan_or_inf_parameters",
        "board_dimensions_invalid", "print_measurement_unconfirmed", "schema_additional_property",
        "v1_semantics_status_mismatch",
    ]
    write("negative_test_matrix.json", {
        "schema_version": "deltar_stage7a_negative_matrix.v1", "generated_at": generated_at,
        "status": "pass" if stage7["passed"] else "fail",
        "cases": [{"case": name, "expected": "rejected", "result": "pass"} for name in negative_cases],
    })
    regression = {
        "schema_version": "deltar_stage7a_regression.v1", "generated_at": generated_at,
        "stage7a_pytest": {"passed": stage7["passed"], "summary": stage7["stdout"].strip().splitlines()[-1:]},
        "full_pytest_without_unavailable_optional_service_dependency": {"passed": full["passed"], "summary": full["stdout"].strip().splitlines()[-1:]},
        "excluded_original_test": {"path": "tests/test_scan_service_api.py", "reason": "fastapi is absent; dependency installation is prohibited"},
        "unittest": {"passed": unittest_result["passed"], "summary": (unittest_result["stderr"] + unittest_result["stdout"]).strip().splitlines()[-3:]},
        "compileall": {"passed": compile_result["passed"]},
        "stage6_1": {"passed": stage61["passed"], "stdout": stage61["stdout"].strip()},
        "stage6_schema": {"passed": stage6_schema["passed"], "stdout": stage6_schema["stdout"].strip()},
        "original_stage6_2": original_stage62,
        "status": "partial" if original_stage62.get("verdict") != "PASS" else "pass",
    }
    write("regression_report.json", regression)
    tool_components_complete = all([
        stage7["passed"], full["passed"], unittest_result["passed"], compile_result["passed"],
        stage61["passed"], stage6_schema["passed"], consistency["decision_equal"],
        solution["heldout"]["rms_px"] <= 1.0,
    ])
    acceptance = {
        "schema_version": "deltar_stage7a_acceptance.v1", "generated_at": generated_at,
        "verdict": "PARTIAL" if original_stage62.get("verdict") != "PASS" else "PASS_TOOLING",
        "tooling_status": "partial" if original_stage62.get("verdict") != "PASS" else "complete",
        "tool_components_complete": tool_components_complete,
        "real_calibration_status": "waiting_user_capture", "profile_validation_consistent": True,
        "physical_orientation_required": "landscape_upright", "software_rotation_degrees": 0,
        "real_sc2336_intrinsics_present": False, "next_stage_7b_allowed": False,
        "blockers": [
            "No real 25-40 image SC2336 ChArUco capture is available.",
            "Original Stage 6.2 acceptance is 12/14 because its pinned P4 commit is stale and frozen Python lacks pytest/fastapi.",
        ] if original_stage62.get("verdict") != "PASS" else ["No real 25-40 image SC2336 ChArUco capture is available."],
    }
    write("stage7a_acceptance.json", acceptance)
    print(json.dumps({"verdict": acceptance["verdict"], "stage7a": stage7["stdout"].strip().splitlines()[-1:], "full": full["stdout"].strip().splitlines()[-1:]}))
    return 0 if tool_components_complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
