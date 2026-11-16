from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOL = ROOT / "stage2b_camera_tof.py"
OUT = ROOT / "evidence"
OUT.mkdir(exist_ok=True)


def run(name: str, args: list[str]) -> None:
    completed = subprocess.run([sys.executable, str(TOOL), *args], text=True, encoding="utf-8", errors="replace", capture_output=True)
    text = f"COMMAND: {sys.executable} {TOOL} {' '.join(args)}\nEXIT_CODE: {completed.returncode}\n\nSTDOUT\n{completed.stdout}\nSTDERR\n{completed.stderr}"
    (OUT / name).write_text(text, encoding="utf-8", newline="\n")
    if completed.returncode != 0:
        raise SystemExit(f"transcript command failed: {args}")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


run("tool_help.txt", ["--help"])
run("status_empty_registry.txt", ["status"])
run("inspect_latest_readonly.txt", ["inspect", "--latest"])
run("register_latest_dry_run.txt", ["register", "--pose", "EVIDENCE_DRY_RUN", "--latest", "--dry-run"])

tests = subprocess.run([sys.executable, "-m", "unittest", "-v", str(ROOT / "test_stage2b_toolchain.py")], text=True, encoding="utf-8", errors="replace", capture_output=True)
(OUT / "unit_tests.txt").write_text(f"EXIT_CODE: {tests.returncode}\n\nSTDOUT\n{tests.stdout}\nSTDERR\n{tests.stderr}", encoding="utf-8", newline="\n")
if tests.returncode != 0:
    raise SystemExit("unit tests failed while generating evidence")

files = [p for p in ROOT.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name != "focused_hash_manifest.json"]
manifest = {str(p.relative_to(ROOT)).replace("\\", "/"): {"bytes": p.stat().st_size, "sha256": digest(p)} for p in sorted(files)}
(OUT / "focused_hash_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
print(f"generated {OUT}")
