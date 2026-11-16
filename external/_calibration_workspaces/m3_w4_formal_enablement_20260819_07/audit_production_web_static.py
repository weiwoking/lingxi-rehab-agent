from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parent
ROOT = Path(r"C:\Users\Weiwo\Downloads\geometry_m3_w4_final_review_20260819_01")
SCANS = ("G001", "G002", "SCAN_161718", "SCAN_165011", "SCAN_070604", "SCAN_085223")


class AuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, {key: value or "" for key, value in attrs}))


def has_class(attrs: dict[str, str], name: str) -> bool:
    return name in attrs.get("class", "").split()


def parse(path: Path) -> tuple[AuditParser, str]:
    text = path.read_text(encoding="utf-8")
    parser = AuditParser()
    parser.feed(text)
    return parser, text


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    index_parser, index_text = parse(ROOT / "index.html")
    index_cards = sum(has_class(attrs, "scan-card") for _, attrs in index_parser.tags)
    index_links = [attrs["href"] for tag, attrs in index_parser.tags if tag == "a" and attrs.get("href", "").endswith(".html")]
    rows = []
    for scan in SCANS:
        path = ROOT / f"{scan}.html"
        parser, text = parse(path)
        frames = sum(has_class(attrs, "frame") for _, attrs in parser.tags)
        images = [attrs["src"] for tag, attrs in parser.tags if tag == "img"]
        missing = [source for source in images if not (ROOT / source).is_file()]
        objects = sum(has_class(attrs, "object") for _, attrs in parser.tags)
        focus_ids = {
            attrs.get("id") for _, attrs in parser.tags if has_class(attrs, "frame")
        }
        row = {
            "scan": scan,
            "frame_card_count": frames,
            "image_reference_count": len(images),
            "missing_asset_count": len(missing),
            "object_card_count": objects,
            "contains_mode_summary": "M3 modes" in text,
            "contains_w4_formula": "W4 Σ(p²·center)/Σp²" in text,
            "contains_research_source_path": "m3_peak_weighting_audit" in text
            or "single_frame_candidate_information_audit" in text,
            "085_focus_anchor_pass": scan != "SCAN_085223"
            or all(f"frame-{index}" in focus_ids for index in (0, 1, 2, 7, 9, 10)),
        }
        row["pass"] = (
            frames == 11
            and len(images) == 11
            and not missing
            and objects > 0
            and row["contains_mode_summary"]
            and row["contains_w4_formula"]
            and not row["contains_research_source_path"]
            and row["085_focus_anchor_pass"]
        )
        rows.append(row)
    passed = (
        index_cards == 6
        and len(index_links) == 6
        and all((ROOT / link).is_file() for link in index_links)
        and all(row["pass"] for row in rows)
    )
    audit = {
        "schema_version": "geometry_m3_w4_web_static_audit.v1",
        "browser_automation_attempted": True,
        "browser_automation_completed": False,
        "browser_block_reason": "in-app browser security policy rejects local file URL navigation",
        "fallback_is_static_read_only_audit": True,
        "index_scan_card_count": index_cards,
        "index_scan_link_count": len(index_links),
        "scan_count": len(rows),
        "frame_count": sum(row["frame_card_count"] for row in rows),
        "image_reference_count": sum(row["image_reference_count"] for row in rows),
        "missing_asset_count": sum(row["missing_asset_count"] for row in rows),
        "web_static_audit_pass": passed,
        "scans": rows,
    }
    write_json(WORKSPACE / "DOWNLOAD_VISUALIZATION_QA.json", audit)
    (WORKSPACE / "DOWNLOAD_VISUALIZATION_QA.md").write_text(
        "# Downloads production Web QA\n\n"
        "The in-app browser rejected local `file://` navigation under its URL security policy. "
        "No alternate browser or localhost workaround was used. A read-only HTML/resource audit "
        "then verified the generated deliverable.\n\n"
        f"- Index scan cards/links: {index_cards}/{len(index_links)}\n"
        f"- Scan pages: {len(rows)}\n"
        f"- Frame cards/images: {audit['frame_count']}/{audit['image_reference_count']}\n"
        f"- Missing assets: {audit['missing_asset_count']}\n"
        f"- Static Web QA: **{passed}**\n",
        encoding="utf-8",
    )
    print(json.dumps({"pass": passed, "frames": audit["frame_count"], "images": audit["image_reference_count"], "missing": audit["missing_asset_count"]}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
