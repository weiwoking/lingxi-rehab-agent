from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {".pt", ".pth", ".onnx", ".png", ".npy", ".npz", ".h5", ".hdf5", ".pyc"}
TREE_ROOTS = ("src", "scripts", "tests", "docs", "schemas", "profiles", "outputs_scan_service")


def next_output(requested: Path) -> Path:
    if not requested.exists():
        return requested
    index = 2
    while True:
        candidate = requested.with_name(f"{requested.stem}_{index:02d}{requested.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def included_files(report_dir: Path) -> list[tuple[Path, str]]:
    rows = [(report_dir / "REPORT.md", "REPORT.md"),
            (report_dir / "SERVICE_TEST_REPORT.json", "SERVICE_TEST_REPORT.json"),
            (ROOT / "README.md", "README.md"), (ROOT / "pyproject.toml", "pyproject.toml"),
            (ROOT / "pytest.ini", "pytest.ini"),
            (ROOT / "models" / "MODEL_MANIFEST.json", "models/MODEL_MANIFEST.json")]
    for tree in TREE_ROOTS:
        base = ROOT / tree
        for path in sorted(base.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or ".pytest_cache" in path.parts:
                continue
            relative = path.relative_to(ROOT).as_posix()
            if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name.endswith(".log"):
                continue
            rows.append((path, relative))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a minimal no-overwrite scan service handoff")
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path.home() / "Downloads" / "DELTAR_PC_SCAN_SERVICE_HANDOFF.zip")
    args = parser.parse_args()
    output = next_output(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = included_files(args.report_dir)
    missing = [archive for source, archive in rows if not source.is_file()]
    forbidden = [archive for source, archive in rows if source.suffix.lower() in FORBIDDEN_SUFFIXES]
    if missing or forbidden:
        raise SystemExit(json.dumps({"missing": missing, "forbidden": forbidden}))
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, name in rows:
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"unsafe archive entry: {name}")
            archive.write(source, name)
    with zipfile.ZipFile(output, "r") as archive:
        bad_crc = archive.testzip()
        entries = archive.infolist()
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    summary = {"path": str(output), "entries": len(entries), "bytes": output.stat().st_size,
               "sha256": digest, "forbidden": len(forbidden), "missing": len(missing),
               "crc_pass": bad_crc is None}
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if bad_crc is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
