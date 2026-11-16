from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parent
REPORT_ROOT = Path(r"C:\ec\_codex_reports")
HISTORICAL = [
    ("A", "stage3_formal_geometry_d_cutover_20260818_01", "Formal DELTAR-D cutover"),
    ("B", "scan8030_runtime_architecture_repair_20260818_01", "8030 architecture repair"),
    ("C", "scan_chatgpt_review_bundle_20260819_01", "Review ZIP implementation"),
    ("D", "support_authority_generic_repair_20260819_01", "Support Repair V1"),
    ("E", "support_authority_reconciliation_v2_20260819_01", "Support Reconciliation V2"),
    ("F", "geometry_support_tof_ui_code_reconstruction_audit_20260819_01", "Code reconstruction / architecture audit"),
    ("G", "geometry_support_tof_ui_formal_implementation_20260819_01", "S0 clean rewrite + ToF degraded/UI"),
    ("H", "geometry_numeric_first_and_full_reaudit_20260819_01", "Numeric-First + frozen full re-audit"),
]


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def category(name: str) -> str:
    lower = name.lower()
    if "candidate" in lower:
        return "candidate audit/design"
    if "support" in lower or "surface" in lower:
        return "support authority"
    if "six_scan" in lower or "replay" in lower or "compact" in lower:
        return "replay/compact output"
    if "source" in lower or lower.endswith((".py", ".diff", ".patch")):
        return "source/snippet/diff"
    if "failure" in lower or "null" in lower:
        return "null/failure audit"
    if "visual" in lower or "download" in lower:
        return "visualization"
    if "protected" in lower or "hash" in lower or "freeze" in lower:
        return "freeze/protected hashes"
    if "manifest" in lower:
        return "manifest"
    if "test" in lower or "transcript" in lower or "validation" in lower:
        return "tests/validation"
    if "architecture" in lower or "runtime" in lower:
        return "architecture/runtime"
    return "other"


def main() -> int:
    result: dict[str, Any] = {
        "schema_version": "candidate_fusion_historical_evidence_index.v1",
        "all_report_files_read": True,
        "all_evidence_zip_members_read": True,
        "archives": [],
    }
    lines = [
        "# Candidate Fusion Evidence Index", "",
        "All eight required `REPORT.md` files and every member of every required Evidence ZIP were read to EOF. Each ZIP member was CRC-checked and SHA-256 hashed from decompressed bytes; JSON members were parsed where applicable. The table below is an index, not a substitute for the archived evidence.", "",
        "## Historical lineage", "",
        "| ID | Round | Report SHA-256 | Evidence ZIP | Members | Uncompressed bytes | CRC/read pass |", "|---|---|---|---|---:|---:|---|",
    ]
    for identifier, directory_name, title in HISTORICAL:
        directory = REPORT_ROOT / directory_name
        report_path = directory / "REPORT.md"
        zip_paths = sorted(directory.glob("*.zip"))
        if len(zip_paths) != 1:
            raise RuntimeError(f"expected exactly one Evidence ZIP in {directory}, found {len(zip_paths)}")
        zip_path = zip_paths[0]
        report_bytes = report_path.read_bytes()
        # Explicit UTF-8 decode proves the complete report is readable.
        report_text = report_bytes.decode("utf-8")
        archive: dict[str, Any] = {
            "id": identifier,
            "title": title,
            "directory": str(directory),
            "report": {
                "path": str(report_path),
                "size": len(report_bytes),
                "sha256": digest_bytes(report_bytes),
                "line_count": len(report_text.splitlines()),
                "read_to_eof": True,
            },
            "evidence_zip": {
                "path": str(zip_path),
                "size": zip_path.stat().st_size,
                "sha256": digest_file(zip_path),
                "members": [],
            },
        }
        total_uncompressed = 0
        with zipfile.ZipFile(zip_path, "r") as archive_file:
            bad = archive_file.testzip()
            if bad is not None:
                raise RuntimeError(f"CRC failure in {zip_path}: {bad}")
            for info in archive_file.infolist():
                if info.is_dir():
                    continue
                data = archive_file.read(info.filename)
                total_uncompressed += len(data)
                member: dict[str, Any] = {
                    "name": info.filename,
                    "size": len(data),
                    "compressed_size": info.compress_size,
                    "crc32": f"{info.CRC:08x}",
                    "sha256": digest_bytes(data),
                    "category": category(info.filename),
                    "read_to_eof": len(data) == info.file_size,
                }
                suffix = Path(info.filename).suffix.lower()
                if suffix in {".md", ".txt", ".json", ".jsonl", ".py", ".ps1", ".diff", ".patch", ".csv", ".sha256"}:
                    try:
                        text = data.decode("utf-8")
                        member["utf8_decode"] = True
                        member["line_count"] = len(text.splitlines())
                        if suffix == ".json":
                            json.loads(text)
                            member["json_parse"] = True
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        member["utf8_or_json_error"] = f"{type(exc).__name__}: {exc}"
                archive["evidence_zip"]["members"].append(member)
        archive["evidence_zip"].update({
            "member_count": len(archive["evidence_zip"]["members"]),
            "uncompressed_bytes": total_uncompressed,
            "crc_pass": True,
            "all_members_read_to_eof": all(item["read_to_eof"] for item in archive["evidence_zip"]["members"]),
        })
        result["archives"].append(archive)
        lines.append(f"| {identifier} | {title} | `{archive['report']['sha256']}` | `{zip_path.name}` | {archive['evidence_zip']['member_count']} | {total_uncompressed} | PASS |")

    lines += ["", "## Priority evidence routing", ""]
    for archive in result["archives"]:
        lines.append(f"### {archive['id']}. {archive['title']}")
        lines.append("")
        grouped: dict[str, list[dict[str, Any]]] = {}
        for member in archive["evidence_zip"]["members"]:
            grouped.setdefault(member["category"], []).append(member)
        priority = ["candidate audit/design", "support authority", "replay/compact output", "source/snippet/diff", "null/failure audit", "architecture/runtime", "visualization", "freeze/protected hashes", "manifest", "tests/validation"]
        for group_name in priority:
            members = grouped.get(group_name, [])
            if not members:
                continue
            lines.append(f"- {group_name}: " + "; ".join(f"`{item['name']}` ({item['size']} B, `{item['sha256'][:12]}…`)" for item in members))
        lines.append("")

    lines += [
        "## Lineage conclusions used by this round", "",
        "- Formal DELTAR-D, Stage2 projection semantics, Stage3A gravity/world-Z, the 8030 lightweight-parent/heavy-worker boundary, Review ZIP, Numeric-First, ToF degraded handling, RGB/UI survival, P4 object presence, and shape-as-extent-only are retained protected boundaries.",
        "- S0 showed maximum-height/shape-override high bias; V1 showed object-bottom-circular low bias; V2/S3 reduced known median errors but retained discrete selector-path variance; the clean rewrite shortened the rule stack; Numeric-First exposed the previously hidden all-numeric distribution.",
        "- The present audit therefore evaluates candidate generation, provenance-normalized evidence, support-context hypotheses, and soft single-frame fusion without changing the protected upstream mathematics.", "",
    ]
    (WORKSPACE / "CANDIDATE_FUSION_EVIDENCE_INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    (WORKSPACE / "historical_evidence_index.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "archives": len(result["archives"]),
        "members": sum(item["evidence_zip"]["member_count"] for item in result["archives"]),
        "uncompressed_bytes": sum(item["evidence_zip"]["uncompressed_bytes"] for item in result["archives"]),
        "all_pass": all(item["evidence_zip"]["crc_pass"] and item["evidence_zip"]["all_members_read_to_eof"] for item in result["archives"]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
