"""R3-3 copy-and-verify preparation for scoring assets and legacy archives.

This tool never deletes a source.  It plans or performs byte-preserving copies,
verifies every target with SHA-256, reverifies the R3-2 authority migration, and
writes the manifests required before a separate reviewed deletion step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
SCORE_ROOT = REPO_ROOT / "pc" / "rehab_score_service"
LEGACY_SCORE_ROOT = SCORE_ROOT / "runtime_data"
LEGACY_SCORE_SESSIONS = LEGACY_SCORE_ROOT / "sessions"
LEGACY_DOCTOR_SESSIONS = (
    REPO_ROOT
    / "pc"
    / "rehab_doctor_twin"
    / "backend"
    / "data"
    / "patients"
    / "P-0217"
    / "sessions"
)
LEGACY_CLOUD_ROOT = (
    REPO_ROOT / "pc" / "rehab_doctor_twin" / "backend" / "runtime_data" / "cloud_sync"
)
TRAINING_ROOT = Path(r"C:\ec\_rehab_training_data")
R3_2_MANIFEST_ROOT = Path(
    r"C:\ec\_codex_reports\rehab_training_data_r3_2_authority_cutover_20260820_01\migration_manifests"
)
SESSION_ID_RE = re.compile(r"^[0-9]{8}_TASK[0-9]{3}$")


@dataclass(frozen=True)
class CopySpec:
    category: str
    source: Path
    target: Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return
    if root.is_symlink():
        raise ValueError(f"symlink root is not allowed: {root}")
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
        if path.is_symlink():
            raise ValueError(f"symlink is not allowed: {path}")
        if path.is_file():
            yield path


def add_tree(specs: list[CopySpec], category: str, source: Path, target: Path) -> None:
    for source_file in iter_files(source):
        relative = source_file.relative_to(source) if source.is_dir() else Path(source.name)
        specs.append(CopySpec(category, source_file, target / relative))


def build_specs(assets_root: Path, archive_root: Path) -> tuple[list[CopySpec], list[CopySpec]]:
    assets: list[CopySpec] = []
    archives: list[CopySpec] = []

    add_tree(assets, "derived", LEGACY_SCORE_ROOT / "derived", assets_root / "derived")
    add_tree(assets, "standards", LEGACY_SCORE_ROOT / "标准", assets_root / "standards")
    for source in sorted((SCORE_ROOT / "template").glob("*.json*")):
        if source.is_file():
            assets.append(CopySpec("templates", source, assets_root / "templates" / source.name))

    for session_dir in sorted(LEGACY_SCORE_SESSIONS.iterdir()):
        match = SESSION_ID_RE.fullmatch(session_dir.name)
        if not session_dir.is_dir() or not match:
            continue
        task_number = int(session_dir.name.rsplit("TASK", 1)[1])
        if session_dir.name.startswith("20260807_TASK") and 24 <= task_number <= 58:
            add_tree(
                assets,
                "calibration_sessions",
                session_dir,
                assets_root / "calibration_sessions" / session_dir.name,
            )

    add_tree(
        archives,
        "failed_staging",
        LEGACY_SCORE_ROOT / ".staging",
        archive_root / "score" / "failed_staging",
    )
    for session_dir in sorted(LEGACY_SCORE_SESSIONS.iterdir()):
        if session_dir.is_dir() and not SESSION_ID_RE.fullmatch(session_dir.name):
            add_tree(
                archives,
                "invalid_score_session",
                session_dir,
                archive_root / "score" / "invalid_sessions" / session_dir.name,
            )

    for session_dir in sorted(LEGACY_DOCTOR_SESSIONS.iterdir()):
        if not session_dir.is_dir():
            continue
        if session_dir.name.startswith("SESSION_DEMO_"):
            add_tree(
                archives,
                "doctor_demo_session",
                session_dir,
                archive_root / "doctor" / "demo_sessions" / session_dir.name,
            )
            continue
        history = session_dir / "scoring_history"
        if history.is_dir():
            add_tree(
                archives,
                "doctor_scoring_history",
                history,
                archive_root / "doctor" / "scoring_history" / session_dir.name / "scoring_history",
            )

    add_tree(
        archives,
        "legacy_cloud_sync",
        LEGACY_CLOUD_ROOT,
        archive_root / "cloud" / "cloud_sync",
    )
    return assets, archives


def validate_unique_targets(specs: list[CopySpec]) -> None:
    seen: dict[str, Path] = {}
    for spec in specs:
        key = str(spec.target.resolve()).casefold()
        previous = seen.get(key)
        if previous is not None and previous != spec.source:
            raise ValueError(f"multiple sources target {spec.target}: {previous}, {spec.source}")
        seen[key] = spec.source


def copy_and_verify(specs: list[CopySpec], execute: bool) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for spec in specs:
        source_size = spec.source.stat().st_size
        source_hash = sha256_file(spec.source)
        status = "planned"
        if execute:
            spec.target.parent.mkdir(parents=True, exist_ok=True)
            if spec.target.exists():
                if not spec.target.is_file():
                    raise FileExistsError(f"target is not a regular file: {spec.target}")
                if spec.target.stat().st_size != source_size or sha256_file(spec.target) != source_hash:
                    raise FileExistsError(f"target exists with different content: {spec.target}")
                status = "verified_existing"
            else:
                with spec.source.open("rb") as source_stream, spec.target.open("xb") as target_stream:
                    shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
                shutil.copystat(spec.source, spec.target)
                status = "copied_verified"
            if spec.target.stat().st_size != source_size or sha256_file(spec.target) != source_hash:
                raise OSError(f"copy verification failed: {spec.source} -> {spec.target}")
        rows.append(
            {
                "category": spec.category,
                "source": str(spec.source),
                "target": str(spec.target),
                "size": source_size,
                "sha256": source_hash,
                "status": status,
            }
        )
    return rows


def reverify_r3_2() -> dict[str, object]:
    checked = 0
    total_bytes = 0
    failures: list[dict[str, str]] = []
    for manifest_path in sorted(R3_2_MANIFEST_ROOT.glob("*.json")):
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = payload.get("files")
        if not isinstance(files, list):
            continue
        for entry in files:
            source = Path(entry["source_path"])
            target = TRAINING_ROOT / Path(entry["target_path"])
            expected_size = int(entry["size"])
            expected_hash = str(entry["sha256"]).lower()
            checked += 1
            total_bytes += expected_size
            for role, path in (("source", source), ("target", target)):
                if not path.is_file():
                    failures.append({"role": role, "path": str(path), "reason": "missing"})
                    continue
                if path.stat().st_size != expected_size:
                    failures.append({"role": role, "path": str(path), "reason": "size_mismatch"})
                    continue
                if sha256_file(path) != expected_hash:
                    failures.append({"role": role, "path": str(path), "reason": "sha256_mismatch"})
    if failures:
        raise OSError(f"R3-2 migration revalidation failed for {len(failures)} entries")
    return {
        "schema": "rehab-training-data-r3-2-reverification/v1",
        "checked_files": checked,
        "checked_bytes": total_bytes,
        "source_and_target_hash_match": True,
        "failures": failures,
    }


def deletion_inventory(template_files: Iterable[Path]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    roots = (
        ("legacy_score_runtime", LEGACY_SCORE_ROOT),
        ("legacy_doctor_sessions", LEGACY_DOCTOR_SESSIONS),
        ("legacy_cloud_sync", LEGACY_CLOUD_ROOT),
    )
    for category, root in roots:
        for path in iter_files(root):
            rows.append(
                {
                    "category": category,
                    "path": str(path),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    legacy_root_key = str(LEGACY_SCORE_ROOT.resolve()).casefold()
    for path in template_files:
        if str(path.resolve()).casefold().startswith(legacy_root_key):
            continue
        rows.append(
            {
                "category": "repo_template_asset_source",
                "path": str(path),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return rows


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def manifest_payload(kind: str, rows: list[dict[str, object]], execute: bool) -> dict[str, object]:
    return {
        "schema": "rehab-training-data-r3-3-cleanup-manifest/v1",
        "kind": kind,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "execute" if execute else "dry-run",
        "file_count": len(rows),
        "total_bytes": sum(int(row["size"]) for row in rows),
        "files": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets-root", type=Path, default=Path(r"C:\ec\_rehab_scoring_assets"))
    parser.add_argument(
        "--archive-root",
        type=Path,
        default=Path(r"C:\ec\_rehab_archive\rehab_training_data_r3_3_20260820_01"),
    )
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    assets_root = args.assets_root.resolve()
    archive_root = args.archive_root.resolve()
    if assets_root == TRAINING_ROOT.resolve() or archive_root == TRAINING_ROOT.resolve():
        raise ValueError("asset/archive roots must be separate from training authority")

    asset_specs, archive_specs = build_specs(assets_root, archive_root)
    validate_unique_targets(asset_specs + archive_specs)
    reverification = reverify_r3_2()
    asset_rows = copy_and_verify(asset_specs, args.execute)
    archive_rows = copy_and_verify(archive_specs, args.execute)
    delete_rows = deletion_inventory(spec.source for spec in asset_specs if spec.category == "templates")

    args.report_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.report_dir / "asset_manifest.json", manifest_payload("scoring_assets", asset_rows, args.execute))
    write_json(args.report_dir / "archive_manifest.json", manifest_payload("archive", archive_rows, args.execute))
    write_json(args.report_dir / "delete_manifest.json", manifest_payload("delete_candidates", delete_rows, args.execute))
    write_json(args.report_dir / "r3_2_migration_reverification.json", reverification)
    summary = {
        "mode": "execute" if args.execute else "dry-run",
        "asset_files": len(asset_rows),
        "asset_bytes": sum(int(row["size"]) for row in asset_rows),
        "archive_files": len(archive_rows),
        "archive_bytes": sum(int(row["size"]) for row in archive_rows),
        "delete_candidate_files": len(delete_rows),
        "delete_candidate_bytes": sum(int(row["size"]) for row in delete_rows),
        "r3_2_files_reverified": reverification["checked_files"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
