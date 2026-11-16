from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from deltar_p4_pipeline.scan_contracts import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = Path.home() / "Downloads"
REQUIRED_FILES = (
    "README.md", "pyproject.toml", "pytest.ini", "geometry_profile.json", "models/MODEL_MANIFEST.json",
    "outputs_stage5_2b/scan_distance_recommendation.json", "outputs_stage5_2b/partial_object_contract.json",
    "outputs_stage5_2b/core_class_contract.json", "outputs_stage5_2b/stage6_entry_contract.json",
    "outputs_stage6/schema_validation.json", "outputs_stage6/test_scan_manifest.json",
    "outputs_stage6/single_frame_result.json", "outputs_stage6/multi_frame_result.json",
    "outputs_stage6/partial_result.json", "outputs_stage6/invalid_input_matrix.json",
    "outputs_stage6/determinism_report.json", "outputs_stage6/performance_report.json",
    "outputs_stage6/acceptance_matrix.json", "outputs_stage6/stage6_acceptance.json",
    "outputs_stage6/pc_final_manifest.json",
)
INCLUDE_DIRS = ("profiles", "src", "workers", "scripts", "tests", "docs", "schemas")
FORBIDDEN_SUFFIXES = {".pt", ".pth", ".onnx", ".png", ".npy", ".npz", ".rgb565", ".h5", ".hdf5", ".pyc"}
FORBIDDEN_PARTS = {"__pycache__", ".pytest_cache", "logs", "samples", "datasets_external", "test_scans"}


def target_path() -> Path:
    base = DOWNLOADS / "DELTAR_STAGE6_HANDOFF.zip"
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = DOWNLOADS / f"DELTAR_STAGE6_HANDOFF_{index:02d}.zip"
        if not candidate.exists():
            return candidate
        index += 1


def allowed(relative: Path) -> bool:
    return relative.suffix.lower() not in FORBIDDEN_SUFFIXES and not any(part in FORBIDDEN_PARTS for part in relative.parts)


def collect() -> list[Path]:
    files = {ROOT / name for name in REQUIRED_FILES}
    for directory in INCLUDE_DIRS:
        files.update(path for path in (ROOT / directory).rglob("*") if path.is_file())
    missing = sorted(str(path.relative_to(ROOT)).replace("\\", "/") for path in files if not path.is_file())
    if missing:
        raise RuntimeError(f"missing required/package files: {missing}")
    selected = sorted((path for path in files if allowed(path.relative_to(ROOT))), key=lambda path: str(path.relative_to(ROOT)).lower())
    required_missing = sorted(name for name in REQUIRED_FILES if ROOT / name not in selected)
    if required_missing:
        raise RuntimeError(f"required files excluded: {required_missing}")
    return selected


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    files = collect()
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    target = target_path()
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, str(path.relative_to(ROOT)).replace("\\", "/"))
    with zipfile.ZipFile(target, "r") as archive:
        crc_failure = archive.testzip()
        names = archive.namelist()
    forbidden = [name for name in names if not allowed(Path(name))]
    missing = [name for name in REQUIRED_FILES if name not in names]
    payload = {
        "schema_version": "deltar_stage6_handoff_manifest.v1",
        "path": str(target), "entries": len(names), "zip_bytes": target.stat().st_size, "sha256": sha256(target),
        "forbidden": len(forbidden), "forbidden_entries": forbidden, "missing": len(missing), "missing_entries": missing,
        "crc": "PASS" if crc_failure is None else f"FAIL:{crc_failure}",
    }
    atomic_write_json(ROOT / "outputs_stage6" / "handoff_manifest.json", payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    raise SystemExit(0 if payload["forbidden"] == payload["missing"] == 0 and payload["crc"] == "PASS" else 1)


if __name__ == "__main__":
    main()

