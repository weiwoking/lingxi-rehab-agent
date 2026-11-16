from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parent
PIPELINE = Path(r"C:\ec\_deltar_p4_pipeline")
ESP_CLAW = Path(r"C:\ec\esp-claw")
ROUND1_WORKSPACE = Path(r"C:\ec\_calibration_workspaces\single_frame_candidate_distribution_fusion_20260819_01")
AUTHORITIES = [
    Path(r"C:\ec\_codex_reports\geometry_numeric_first_and_full_reaudit_20260819_01"),
    Path(r"C:\ec\_codex_reports\single_frame_candidate_distribution_fusion_20260819_01"),
]
LINEAGE = Path(r"C:\ec\_codex_reports\geometry_support_tof_ui_code_reconstruction_audit_20260819_01")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command(args: list[str], cwd: Path, binary: bool = False):
    completed = subprocess.run(args, cwd=cwd, capture_output=True, check=True)
    return completed.stdout if binary else completed.stdout.decode("utf-8", errors="replace")


def git_state(repo: Path) -> dict[str, Any]:
    diff = command(["git", "diff", "--binary"], repo, binary=True)
    return {
        "repo": str(repo),
        "head": command(["git", "rev-parse", "HEAD"], repo).strip(),
        "status": command(["git", "status", "--short", "--branch"], repo),
        "diff_sha256": sha256_bytes(diff),
        "diff_bytes": len(diff),
    }


def main() -> int:
    document: dict[str, Any] = {
        "schema_version": "candidate_information_round2_intake.v1",
        "git_pre": {"pipeline": git_state(PIPELINE), "esp_claw": git_state(ESP_CLAW)},
        "authority_archives": [],
        "round1_workspace": [],
        "lineage_files": [],
    }
    for directory in AUTHORITIES:
        report = directory / "REPORT.md"
        report_bytes = report.read_bytes()
        report_bytes.decode("utf-8")
        zip_paths = list(directory.glob("*.zip"))
        if len(zip_paths) != 1:
            raise RuntimeError(f"expected one evidence ZIP in {directory}")
        archive_path = zip_paths[0]
        members = []
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None:
                raise RuntimeError(f"CRC failure: {archive_path}")
            for info in archive.infolist():
                if info.is_dir():
                    continue
                data = archive.read(info.filename)
                members.append({"name": info.filename, "size": len(data), "sha256": sha256_bytes(data), "read_to_eof": len(data) == info.file_size})
        document["authority_archives"].append({
            "directory": str(directory),
            "report_sha256": sha256_bytes(report_bytes),
            "report_read_to_eof": True,
            "zip_path": str(archive_path),
            "zip_sha256": sha256_file(archive_path),
            "zip_member_count": len(members),
            "zip_uncompressed_bytes": sum(item["size"] for item in members),
            "all_members_read_to_eof": all(item["read_to_eof"] for item in members),
            "members": members,
        })
    for path in sorted(ROUND1_WORKSPACE.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            data = path.read_bytes()
            # Read all analysis text/JSON to EOF; PNG/other files are hashed.
            if path.suffix.lower() in {".md", ".json", ".py", ".html", ".js"}:
                data.decode("utf-8")
            document["round1_workspace"].append({"path": str(path), "size": len(data), "sha256": sha256_bytes(data), "read_to_eof": True})
    for path in sorted(LINEAGE.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".md", ".json", ".py", ".patch", ".diff"}:
            data = path.read_bytes()
            try:
                data.decode("utf-8")
                readable = True
            except UnicodeDecodeError:
                readable = False
            document["lineage_files"].append({"path": str(path), "size": len(data), "sha256": sha256_bytes(data), "utf8_read": readable})
    protected_paths = {
        "stage2": PIPELINE / "profiles/formal_geometry_v2/stage2_calibration.json",
        "stage3a": PIPELINE / "profiles/formal_geometry_v2/stage3a_gravity.json",
        "deltar_d": PIPELINE / "src/deltar_p4_pipeline/formal_deltar_d.py",
        "stage2_projection": PIPELINE / "src/deltar_p4_pipeline/stage2_projection.py",
        "support_selector": PIPELINE / "src/deltar_p4_pipeline/geometry_v2/support_selector.py",
        "generic_support": PIPELINE / "src/deltar_p4_pipeline/geometry_v2/generic_support.py",
        "environment_surfaces": PIPELINE / "src/deltar_p4_pipeline/geometry_v2/environment_surfaces.py",
        "support_config": PIPELINE / "profiles/formal_geometry_v2/all_object_geometry_v2.json",
        "checkpoint": Path(r"C:\ec\_deltar_formal\assets\nyu.pt"),
    }
    document["protected_hashes_pre"] = {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in protected_paths.items()}
    (WORKSPACE / "intake_freeze.json").write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "authority_zip_members": sum(item["zip_member_count"] for item in document["authority_archives"]),
        "authority_uncompressed_bytes": sum(item["zip_uncompressed_bytes"] for item in document["authority_archives"]),
        "round1_files_read": len(document["round1_workspace"]),
        "lineage_files_read": len(document["lineage_files"]),
        "pipeline_git": document["git_pre"]["pipeline"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
