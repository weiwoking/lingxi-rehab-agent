#!/usr/bin/env python3
"""Validate Rehab UI glyph sources, manifests, and generated LVGL binfont."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from binfont_inspect import parse
from production_text import (
    COMMON_PUNCTUATION,
    REQUIRED_FIXED_TEXTS,
    gb2312_level1_codepoints,
    production_codepoints,
    reasoning_sample_supplement_codepoints,
)


REQUIRED_CODEPOINTS = (
    0x4E91,
    0x5883,
    0x62DF,
    0x626B,
    0x63CF,
    0x670D,
    0x73AF,
    0x7AEF,
    0x81F3,
)
WORKSPACE = Path(__file__).resolve().parents[1]
APP = WORKSPACE.parents[1]
REPOSITORY = APP.parents[1]
SOURCE_REFERENCE = WORKSPACE / "source" / "source_font_reference.json"
GLYPHS = WORKSPACE / "glyphs"
CANONICAL_FONT = WORKSPACE / "generated" / "rehab_cn_20.bin"
DEPLOYMENT_FONT = (
    APP / "fatfs_image" / "system" / "ui" / "fonts" / "rehab_cn_20.bin"
)
RUNTIME_TEXT_HELPER = (
    APP / "fatfs_image" / "system" / "ui" / "rehab"
    / "rehab_ui_text.lua"
)


def expand_ranges(items: list[str]) -> set[int]:
    expanded: set[int] = set()
    for item in items:
        values = item.split("-", 1)
        start = int(values[0], 16)
        end = int(values[-1], 16)
        expanded.update(range(start, end + 1))
    return expanded


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_source_reference() -> tuple[Path, str]:
    reference = json.loads(SOURCE_REFERENCE.read_text(encoding="utf-8"))
    expected_path = reference.get("relative_or_absolute_expected_path")
    expected_hash = reference.get("sha256")
    if not isinstance(expected_path, str) or not expected_path:
        raise SystemExit("source reference has no expected font path")
    if not isinstance(expected_hash, str) or not expected_hash:
        raise SystemExit("source reference has no SHA256")
    source = Path(expected_path)
    if not source.is_absolute():
        source = REPOSITORY / source
    source = source.resolve()
    if not source.is_file():
        raise SystemExit(f"referenced source font not found: {source}")
    actual_hash = sha256(source)
    if actual_hash.lower() != expected_hash.lower():
        raise SystemExit(
            "source font SHA256 mismatch: "
            f"expected {expected_hash.lower()}, got {actual_hash}"
        )
    return source, actual_hash


def main() -> None:
    source_font, source_hash = validate_source_reference()
    glyphs = (GLYPHS / "rehab_ui_glyphs.txt").read_text(
        encoding="utf-8-sig"
    ).rstrip("\r\n")
    glyph_codepoints = {ord(character) for character in glyphs}
    codepoint_lines = (GLYPHS / "rehab_ui_codepoints.txt").read_text(
        encoding="utf-8-sig"
    ).splitlines()
    listed_codepoints = {
        int(match.group(1), 16)
        for line in codepoint_lines
        if (match := re.match(r"^U\+([0-9A-Fa-f]+)\s", line))
    }
    range_items = (
        GLYPHS / "rehab_ui_codepoint_ranges.txt"
    ).read_text(encoding="utf-8-sig").strip().split(",")
    ranged_codepoints = expand_ranges(range_items)
    manifest = json.loads(
        (GLYPHS / "rehab_ui_glyph_manifest.json").read_text(
            encoding="utf-8-sig"
        )
    )
    manifest_codepoints = expand_ranges(manifest["ranges"])

    sets = {
        "glyph source": glyph_codepoints,
        "codepoint list": listed_codepoints,
        "range list": ranged_codepoints,
        "manifest": manifest_codepoints,
    }
    for label, values in sets.items():
        missing = set(REQUIRED_CODEPOINTS) - values
        if missing:
            raise SystemExit(
                f"{label} missing "
                + ",".join(f"U+{value:04X}" for value in sorted(missing))
            )
    if len({frozenset(values) for values in sets.values()}) != 1:
        raise SystemExit("font source and manifest codepoint sets differ")
    if manifest["glyph_count"] != len(glyph_codepoints):
        raise SystemExit("manifest glyph_count does not match source")

    gb2312_values = gb2312_level1_codepoints()
    if manifest.get("gb2312_level1_count") != 3755:
        raise SystemExit("manifest GB2312 Level-1 count is not 3755")
    missing_gb2312 = gb2312_values - glyph_codepoints
    if missing_gb2312:
        raise SystemExit(
            "GB2312 Level-1 glyphs missing from font source: "
            + ",".join(
                f"U+{value:04X}" for value in sorted(missing_gb2312)
            )
        )
    production_values, production_records = production_codepoints()
    missing_production = production_values - glyph_codepoints
    if missing_production:
        raise SystemExit(
            "production source glyphs missing from font source: "
            + ",".join(
                f"U+{value:04X}" for value in sorted(missing_production)
            )
        )
    fixed_values = {
        ord(character)
        for text in REQUIRED_FIXED_TEXTS
        for character in text
    }
    missing_fixed = fixed_values - glyph_codepoints
    if missing_fixed:
        raise SystemExit(
            "fixed UI text glyphs missing from font source: "
            + ",".join(f"U+{value:04X}" for value in sorted(missing_fixed))
        )
    missing_punctuation = {
        ord(character) for character in COMMON_PUNCTUATION
    } - glyph_codepoints
    if missing_punctuation:
        raise SystemExit(
            "common punctuation missing from font source: "
            + ",".join(
                f"U+{value:04X}" for value in sorted(missing_punctuation)
            )
        )
    if set(range(0x20, 0x7F)) - glyph_codepoints:
        raise SystemExit("printable ASCII is incomplete")
    if manifest.get("source_files_scanned_count") != len(production_records):
        raise SystemExit("manifest production source scan count is stale")

    reasoning_sample_values = reasoning_sample_supplement_codepoints()
    missing_reasoning_sample = reasoning_sample_values - glyph_codepoints
    if missing_reasoning_sample:
        raise SystemExit(
            "reasoning sample supplement missing from font source: "
            + ",".join(
                f"U+{value:04X}"
                for value in sorted(missing_reasoning_sample)
            )
        )
    if manifest.get("reasoning_sample_supplement_count") != len(
        reasoning_sample_values
    ):
        raise SystemExit("manifest reasoning sample supplement count is stale")
    expected_sample_codepoints = [
        f"U+{value:04X}" for value in sorted(reasoning_sample_values)
    ]
    if manifest.get("reasoning_sample_supplement_codepoints") != \
            expected_sample_codepoints:
        raise SystemExit(
            "manifest reasoning sample supplement codepoints are stale"
        )

    if not RUNTIME_TEXT_HELPER.is_file():
        raise SystemExit("runtime text sanitizer is missing")
    helper = RUNTIME_TEXT_HELPER.read_text(encoding="utf-8")
    supported_match = re.search(
        r"local SUPPORTED = \[==\[(.*?)\]==\]", helper, re.DOTALL
    )
    count_match = re.search(
        r"M\.supported_glyph_count = (\d+)", helper
    )
    if not supported_match or not count_match:
        raise SystemExit("runtime text sanitizer metadata is invalid")
    if {ord(character) for character in supported_match.group(1)} != \
            glyph_codepoints:
        raise SystemExit("runtime text sanitizer coverage differs from manifest")
    if int(count_match.group(1)) != len(glyph_codepoints):
        raise SystemExit("runtime text sanitizer glyph count is stale")
    if 'replacement = "？"' not in helper:
        raise SystemExit("runtime text sanitizer has no visible fallback")

    if not CANONICAL_FONT.is_file() or not DEPLOYMENT_FONT.is_file():
        raise SystemExit("canonical or deployment font is missing")
    if CANONICAL_FONT.stat().st_size != DEPLOYMENT_FONT.stat().st_size:
        raise SystemExit("canonical/deployment font sizes differ")
    canonical_hash = sha256(CANONICAL_FONT)
    deployment_hash = sha256(DEPLOYMENT_FONT)
    if canonical_hash != deployment_hash:
        raise SystemExit("canonical/deployment font SHA256 values differ")

    parsed_fonts = {
        "canonical": parse(CANONICAL_FONT, sorted(glyph_codepoints)),
        "deployment": parse(DEPLOYMENT_FONT, sorted(glyph_codepoints)),
    }
    for label, parsed in parsed_fonts.items():
        missing_binary = [
            codepoint
            for codepoint, descriptor in parsed["glyphs"].items()
            if descriptor is None
        ]
        if missing_binary:
            raise SystemExit(
                f"{label} binary is missing: " + ",".join(missing_binary)
            )
        if parsed["header"]["font_size"] != 20:
            raise SystemExit(f"{label} binary font size is not 20")
        if parsed["header"]["bits_per_pixel"] != 1:
            raise SystemExit(f"{label} binary bpp is not 1")

    print("rehab UI font validation: PASS")
    print(f"source_font={source_font}")
    print(f"source_sha256={source_hash}")
    print(f"glyph_count={len(glyph_codepoints)}")
    print("gb2312_level1_count=3755")
    print(f"production_source_count={len(production_records)}")
    print("production_missing_count=0")
    print(
        "reasoning_sample_supplement="
        + ",".join(
            f"U+{value:04X}"
            for value in sorted(reasoning_sample_values)
        )
    )
    print(
        "required="
        + ",".join(f"U+{value:04X}" for value in REQUIRED_CODEPOINTS)
    )
    print(f"binary_size={CANONICAL_FONT.stat().st_size}")
    print(f"binary_sha256={canonical_hash}")


if __name__ == "__main__":
    main()
