from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parent
PYTHON = Path(r"C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe")
TEST_PYTHON = Path(r"C:\ProgramData\anaconda3\python.exe")
PIPELINE = Path(r"C:\ec\_deltar_p4_pipeline")


def run(name: str, command: list[str], cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True)
    return {
        "name": name,
        "command": command,
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "pass": completed.returncode == 0,
    }


def main() -> int:
    scripts = [
        WORKSPACE / "audit_candidate_fusion.py",
        WORKSPACE / "evaluate_frozen_fusion.py",
        WORKSPACE / "build_historical_evidence_index.py",
        WORKSPACE / "test_single_frame_independence.py",
        WORKSPACE / "run_validation.py",
    ]
    results = [
        run("audit_script_compile", [str(PYTHON), "-m", "py_compile", *map(str, scripts)], WORKSPACE),
        run("single_frame_independence", [str(PYTHON), str(WORKSPACE / "test_single_frame_independence.py")], WORKSPACE),
        run(
            "worker_pytest_ownership_probe",
            [str(PYTHON), "-c", "import importlib.util; print('worker_pytest_available=', bool(importlib.util.find_spec('pytest'))); print('Tests use the repository test interpreter; the frozen CUDA worker remains dependency-minimal.')"],
            WORKSPACE,
        ),
        run(
            "focused_production_regression",
            [
                str(TEST_PYTHON), "-m", "pytest", "-q",
                "tests/test_support_authority_generic.py",
                "tests/test_tof_degraded_mode.py",
                "tests/test_runtime_import_boundaries.py",
                "tests/test_chatgpt_review_bundle.py",
            ],
            PIPELINE,
        ),
        run("pipeline_diff_check", ["git", "diff", "--check"], PIPELINE),
    ]
    document = {
        "schema_version": "candidate_distribution_fusion_validation.v1",
        "device_write_operations_run": False,
        "idf_commands_run": False,
        "results": results,
        "pass": all(item["pass"] for item in results),
    }
    (WORKSPACE / "test_results.json").write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# Test Transcript", ""]
    for item in results:
        lines += [
            f"## {item['name']}", "",
            f"Exit code: `{item['exit_code']}`", "",
            "```text", item["stdout"].rstrip(), item["stderr"].rstrip(), "```", "",
        ]
    lines += [f"Overall: `{'PASS' if document['pass'] else 'FAIL'}`", "", "No device write, `idf.py`, bmgr, build, flash, monitor, reconfigure, clean, or fullclean operation was run.", ""]
    (WORKSPACE / "test_transcript.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"pass": document["pass"], "results": [{"name": item["name"], "exit_code": item["exit_code"]} for item in results]}, indent=2))
    return 0 if document["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
