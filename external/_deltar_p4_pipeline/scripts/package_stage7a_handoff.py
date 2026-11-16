from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_FILES = ("README.md", "pyproject.toml")
REPO_DIRS = ("src", "scripts", "tests", "docs", "schemas", "profiles", "outputs_stage7a", "board_assets")
REPORT_FILES = ("REPORT.md", "TEST_REPORT.json", "USER_CAPTURE_GUIDE.md")
FORBIDDEN_PARTS = {".git", "__pycache__", ".pytest_cache", "build", "cache", "service_data", "credentials", "secrets"}
FORBIDDEN_SUFFIXES = {".pyc", ".pt", ".pth", ".onnx", ".engine", ".bin", ".rgb565"}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def allowed(path: Path) -> bool:
    lowered = {part.lower() for part in path.parts}
    return not (lowered & FORBIDDEN_PARTS) and path.suffix.lower() not in FORBIDDEN_SUFFIXES and path.name.lower() != "sdkconfig"


def choose_output(downloads: Path) -> Path:
    base = downloads / "DELTAR_STAGE7A_CAMERA_INTRINSICS_HANDOFF.zip"
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = downloads / f"DELTAR_STAGE7A_CAMERA_INTRINSICS_HANDOFF_{index:02d}.zip"
        if not candidate.exists():
            return candidate
        index += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--downloads", type=Path, default=Path.home() / "Downloads")
    args = parser.parse_args()
    files: list[tuple[Path, str]] = []
    for name in REPORT_FILES:
        path = args.report_dir / name
        if not path.is_file():
            raise RuntimeError(f"missing report artifact: {path}")
        files.append((path, name))
    for name in REPO_FILES:
        files.append((ROOT / name, name))
    for folder in REPO_DIRS:
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and allowed(path.relative_to(ROOT)):
                files.append((path, path.relative_to(ROOT).as_posix()))
    names = [name for _, name in files]
    if len(names) != len(set(names)):
        raise RuntimeError("duplicate archive entry")
    entries = [{"path": name, "bytes": path.stat().st_size, "sha256": digest(path)} for path, name in files]
    forbidden = [name for name in names if not allowed(Path(name))]
    required = [*REPORT_FILES, *REPO_FILES]
    missing = [name for name in required if name not in names]
    for folder in REPO_DIRS:
        if not any(name.startswith(folder + "/") for name in names):
            missing.append(folder + "/")
    manifest = {
        "schema_version": "deltar_stage7a_handoff_manifest.v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "entries": len(entries), "bytes": sum(item["bytes"] for item in entries),
        "forbidden": forbidden, "missing": missing, "files": entries,
    }
    if forbidden or missing:
        raise RuntimeError(f"handoff preflight failed: forbidden={forbidden}, missing={missing}")
    output = choose_output(args.downloads)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, name in files:
            archive.write(path, name)
        archive.writestr("HANDOFF_MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    with zipfile.ZipFile(output, "r") as archive:
        bad_crc = archive.testzip()
        archive_names = archive.namelist()
    verification = {
        "zip": str(output), "entries": len(archive_names), "bytes": output.stat().st_size,
        "sha256": digest(output), "forbidden": len(forbidden), "missing": len(missing),
        "crc": "PASS" if bad_crc is None else f"FAIL:{bad_crc}",
    }
    (args.report_dir / "zip_verification.json").write_text(json.dumps(verification, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(verification, ensure_ascii=False))
    return 0 if bad_crc is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
