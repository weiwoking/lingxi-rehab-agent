from __future__ import annotations

import argparse
import json
import zipfile
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {".png", ".npy", ".npz", ".rgb565", ".h5", ".hdf5", ".pyc"}
FORBIDDEN_PARTS = {"__pycache__", ".pytest_cache", "logs", "samples", "datasets_external", "models"}
REQUIRED = [
    "REPORT.md",
    "README.md",
    "pyproject.toml",
    "pytest.ini",
    "schemas/deltar_scan_input.v1.schema.json",
    "schemas/deltar_scan_frame.v1.schema.json",
    "schemas/rehab_tof_raw.v1.schema.json",
    "schemas/INPUT_SCHEMA_MANIFEST.json",
    "outputs_stage6/pc_final_manifest.json",
    "outputs_stage6_1/pc_final_manifest_v1_1.json",
    "outputs_stage6_1/stage6_1_acceptance.json",
    "outputs_stage6_2/source_audit.json",
    "outputs_stage6_2/schema_check_report.json",
    "outputs_stage6_2/schema_runtime_alignment.json",
    "outputs_stage6_2/created_at_validation.json",
    "outputs_stage6_2/fixture_matrix.json",
    "outputs_stage6_2/complete_scan_validation.json",
    "outputs_stage6_2/negative_scan_validation.json",
    "outputs_stage6_2/regression_report.json",
    "outputs_stage6_2/input_schema_manifest.json",
    "outputs_stage6_2/stage6_2_acceptance.json",
    "outputs_stage6_2/pc_final_manifest_stage6_2.json",
]


def hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def next_target(downloads: Path) -> Path:
    first = downloads / "DELTAR_STAGE6_2_HANDOFF.zip"
    if not first.exists():
        return first
    index = 2
    while True:
        candidate = downloads / f"DELTAR_STAGE6_2_HANDOFF_{index:02d}.zip"
        if not candidate.exists():
            return candidate
        index += 1


def allowed(path: Path) -> bool:
    return path.suffix.lower() not in FORBIDDEN_SUFFIXES and not (set(path.parts) & FORBIDDEN_PARTS)


def gather(report: Path) -> dict[str, Path]:
    files: dict[str, Path] = {
        "REPORT.md": report,
        "README.md": ROOT / "README.md",
        "pyproject.toml": ROOT / "pyproject.toml",
        "pytest.ini": ROOT / "pytest.ini",
    }
    for name in (
        "deltar_scan_input.v1.schema.json",
        "deltar_scan_frame.v1.schema.json",
        "rehab_tof_raw.v1.schema.json",
        "INPUT_SCHEMA_MANIFEST.json",
    ):
        files[f"schemas/{name}"] = ROOT / "schemas" / name
    for folder in ("src", "scripts", "tests", "docs"):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and allowed(path.relative_to(ROOT)):
                files[path.relative_to(ROOT).as_posix()] = path
    output_files = {
        "outputs_stage6/pc_final_manifest.json",
        "outputs_stage6_1/pc_final_manifest_v1_1.json",
        "outputs_stage6_1/stage6_1_acceptance.json",
        *{f"outputs_stage6_2/{name}" for name in (
            "source_audit.json", "schema_check_report.json", "schema_runtime_alignment.json",
            "created_at_validation.json", "fixture_matrix.json", "complete_scan_validation.json",
            "negative_scan_validation.json", "regression_report.json", "input_schema_manifest.json",
            "stage6_2_acceptance.json", "pc_final_manifest_stage6_2.json",
        )},
    }
    for relative in sorted(output_files):
        files[relative] = ROOT / relative
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--downloads", type=Path, default=Path.home() / "Downloads")
    args = parser.parse_args()
    files = gather(args.report.resolve())
    missing_source = sorted(name for name, path in files.items() if not path.is_file())
    if missing_source:
        raise SystemExit(f"missing source files: {missing_source}")
    target = next_target(args.downloads.resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, path in sorted(files.items()):
            archive.write(path, name)
    with zipfile.ZipFile(target, "r") as archive:
        names = archive.namelist()
        crc_failure = archive.testzip()
        uncompressed_bytes = sum(info.file_size for info in archive.infolist())
    missing = sorted(set(REQUIRED) - set(names))
    forbidden = sorted(
        name for name in names
        if Path(name).suffix.lower() in FORBIDDEN_SUFFIXES
        or bool(set(Path(name).parts) & FORBIDDEN_PARTS)
        or Path(name).is_absolute()
        or ".." in Path(name).parts
    )
    audit = {
        "schema_version": "deltar_stage6_2_handoff_audit.v1",
        "archive": target.name,
        "entries": len(names),
        "uncompressed_bytes": uncompressed_bytes,
        "archive_bytes": target.stat().st_size,
        "sha256": hash_file(target),
        "missing": missing,
        "missing_count": len(missing),
        "forbidden": forbidden,
        "forbidden_count": len(forbidden),
        "crc": "PASS" if crc_failure is None else f"FAIL:{crc_failure}",
        "absolute_entry_paths": sum(Path(name).is_absolute() for name in names),
        "path_traversal_entries": sum(".." in Path(name).parts for name in names),
    }
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False))
    passed = not missing and not forbidden and crc_failure is None
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
