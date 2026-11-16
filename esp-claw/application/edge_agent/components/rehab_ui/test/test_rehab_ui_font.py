from __future__ import annotations

import importlib.util
import hashlib
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
APP = ROOT / "application" / "edge_agent"
FONTS = APP / "fatfs_image" / "system" / "ui" / "fonts"
TOOLS = APP / "tools" / "rehab_ui_font"
SCRIPTS = TOOLS / "scripts"
GLYPHS = TOOLS / "glyphs"
GENERATED = TOOLS / "generated"
CANONICAL = GENERATED / "rehab_cn_20.bin"
DEPLOYMENT = FONTS / "rehab_cn_20.bin"
RUNTIME_TEXT = (
    APP / "fatfs_image" / "system" / "ui" / "rehab"
    / "rehab_ui_text.lua"
)
REQUIRED = (
    0x2192,
    0x2265,
    0x2713,
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


def load_inspector():
    spec = importlib.util.spec_from_file_location(
        "rehab_binfont_inspect", SCRIPTS / "binfont_inspect.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("binfont inspector unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RehabUiFontTest(unittest.TestCase):
    def test_required_glyphs_exist_in_all_generated_assets(self) -> None:
        glyphs = (GLYPHS / "rehab_ui_glyphs.txt").read_text(
            encoding="utf-8-sig"
        )
        codepoints = (GLYPHS / "rehab_ui_codepoints.txt").read_text(
            encoding="utf-8-sig"
        )
        manifest = json.loads(
            (GLYPHS / "rehab_ui_glyph_manifest.json").read_text(
                encoding="utf-8-sig"
            )
        )
        range_values: set[int] = set()
        for item in manifest["ranges"]:
            parts = item.split("-", 1)
            start = int(parts[0], 16)
            end = int(parts[-1], 16)
            range_values.update(range(start, end + 1))

        for codepoint in REQUIRED:
            self.assertIn(chr(codepoint), glyphs)
            self.assertRegex(codepoints, rf"(?m)^U\+{codepoint:04X}\s")
            self.assertIn(codepoint, range_values)

        for font in (CANONICAL, DEPLOYMENT):
            parsed = load_inspector().parse(font, list(REQUIRED))
            for codepoint in REQUIRED:
                descriptor = parsed["glyphs"][f"U+{codepoint:04X}"]
                self.assertIsNotNone(descriptor)
                self.assertTrue(descriptor["bitmap_data_nonempty"])

    def test_runtime_glyph_count_matches_manifest(self) -> None:
        manifest = json.loads(
            (GLYPHS / "rehab_ui_glyph_manifest.json").read_text(
                encoding="utf-8-sig"
            )
        )
        theme = (
            APP
            / "fatfs_image"
            / "system"
            / "ui"
            / "rehab"
            / "rehab_ui_theme.lua"
        ).read_text(encoding="utf-8-sig")
        match = re.search(r"font_glyph_count\s*=\s*(\d+)", theme)
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), manifest["glyph_count"])
        self.assertEqual(manifest["gb2312_level1_count"], 3755)
        self.assertGreaterEqual(manifest["glyph_count"], 3850)
        self.assertEqual(manifest["font_bpp"], 1)
        self.assertEqual(
            manifest["reasoning_sample_supplement_codepoints"],
            ["U+2192", "U+2265"],
        )
        self.assertEqual(manifest["reasoning_sample_supplement_count"], 2)

    def test_runtime_sanitizer_matches_manifest_and_has_visible_fallback(
        self,
    ) -> None:
        manifest = json.loads(
            (GLYPHS / "rehab_ui_glyph_manifest.json").read_text(
                encoding="utf-8-sig"
            )
        )
        helper = RUNTIME_TEXT.read_text(encoding="utf-8")
        supported = re.search(
            r"local SUPPORTED = \[==\[(.*?)\]==\]", helper, re.DOTALL
        )
        count = re.search(
            r"M\.supported_glyph_count = (\d+)", helper
        )
        self.assertIsNotNone(supported)
        self.assertIsNotNone(count)
        self.assertEqual(
            len({ord(character) for character in supported.group(1)}),
            manifest["glyph_count"],
        )
        self.assertEqual(int(count.group(1)), manifest["glyph_count"])
        self.assertIn('replacement = "？"', helper)

    def test_canonical_and_deployment_are_identical(self) -> None:
        self.assertTrue(CANONICAL.is_file())
        self.assertTrue(DEPLOYMENT.is_file())
        self.assertEqual(CANONICAL.stat().st_size, DEPLOYMENT.stat().st_size)
        self.assertEqual(
            hashlib.sha256(CANONICAL.read_bytes()).hexdigest(),
            hashlib.sha256(DEPLOYMENT.read_bytes()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
