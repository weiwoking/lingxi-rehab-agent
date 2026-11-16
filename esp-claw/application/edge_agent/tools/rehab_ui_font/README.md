# Rehab UI font workspace

This directory is the canonical engineering workspace for the P4 Rehab UI
font. Do not manually edit either `rehab_cn_20.bin`.

## Layout and ownership

- `source/source_font_reference.json` records the expected vendor-managed
  Source Han Sans SC input, its SHA256, its name, and its license location.
  The 16 MiB OTF remains under `managed_components/lvgl__lvgl`; copying it
  here would duplicate a managed dependency and waste repository space.
- `glyphs/` contains the generated glyph source, codepoint list, compact
  ranges, and manifest.
- `generated/rehab_cn_20.bin` is the canonical generated artifact.
- `scripts/` contains the generator, validator, and binfont inspector.
- `../../fatfs_image/system/ui/fonts/rehab_cn_20.bin` is the runtime
  deployment copy read by the P4. It is the only workspace font asset that
  enters SYSTEM.

The tests remain in `components/rehab_ui/test/` and refer to this layout.
No glyph source, manifest, source reference, script, README, or test belongs
in SYSTEM.

## Generate

Use an already available `lv_font_conv` 1.5.x checkout; do not install a
dependency as part of firmware packaging:

```powershell
python scripts/generate_rehab_ui_font.py `
  --converter C:\path\to\lv_font_conv.js
```

The generator:

1. reads `source/source_font_reference.json` and verifies the vendor OTF
   SHA256;
2. merges the retained source, printable ASCII, common punctuation, all
   3,755 GB2312 first-level Han characters, production Rehab Lua/C string
   literals, the scoring-report template, fixed task text, and the reviewed
   `source/reasoning_sample_supplement.txt` glyphs (never reasoning text or
   user data);
3. generates and parses a temporary 20 px, 1 bpp LVGL binfont so the full
   common-character set stays below the runtime 256 KiB font limit;
4. generates the runtime text sanitizer, which replaces unsupported dynamic
   characters with the visible full-width question mark `？`;
5. verifies the established Rehab UI glyphs plus the cloud wording glyphs
   `云` (`U+4E91`), `服` (`U+670D`), `端` (`U+7AEF`), and `至` (`U+81F3`);
6. publishes the canonical binary, then atomically synchronizes the same
   bytes to the runtime deployment path.

Conversion or validation failure does not overwrite the current runtime
deployment copy.

## Validate

```powershell
python scripts/validate_rehab_ui_font.py
python ../../components/rehab_ui/test/test_rehab_ui_font.py
```

Validation checks the source reference hash; all four glyph metadata
representations; production Lua/C and scoring-template strings; the 3,755
GB2312 first-level characters; the reasoning-sample supplement; common punctuation; runtime sanitizer
coverage; both binary files; identical size and SHA256; the 1 bpp LVGL
header; and required glyphs.
