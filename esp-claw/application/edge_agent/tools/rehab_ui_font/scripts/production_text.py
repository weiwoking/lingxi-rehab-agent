"""Authoritative production-text and GB2312 Level-1 font inputs."""

from __future__ import annotations

import ast
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
APP = WORKSPACE.parents[1]
REPOSITORY = APP.parents[1]
REASONING_SAMPLE_SUPPLEMENT = (
    WORKSPACE / "source" / "reasoning_sample_supplement.txt"
)

COMMON_PUNCTUATION = (
    "，。！？：；、（）《》【】“”‘’…—·￥％＋－×÷＝＜＞"
    "〔〕［］｛｝～｀｜＂＇＃＆＠＊＿"
)
FIXED_DRINK_REASON = (
    "该杯子高度适中，属于一级任务，与患者能力匹配，"
    "其余杯子高度过高，超出安全阈值"
)
REQUIRED_FIXED_TEXTS = (
    FIXED_DRINK_REASON,
    "水杯横向取放训练（一级）",
    "水杯拿取与饮水训练（二级）",
    "水杯拿取与饮水训练（三级）",
    "球类横向取放训练（一级）",
    "球类横向取放训练（二级）",
    "目标物体高度",
    "支撑面离地高度",
    "物体顶部离地高度",
    "高度不可用",
    "距离不可用",
    "未检测到完整喝水动作周期",
    "动作完整性得分已降低",
)


def gb2312_level1_codepoints() -> set[int]:
    """Return the standard 3,755 first-level GB2312 Han characters."""
    values: set[int] = set()
    for lead in range(0xB0, 0xD8):
        last_trail = 0xF9 if lead == 0xD7 else 0xFE
        for trail in range(0xA1, last_trail + 1):
            character = bytes((lead, trail)).decode("gb2312")
            values.add(ord(character))
    if len(values) != 3755:
        raise RuntimeError(
            f"GB2312 Level-1 count mismatch: expected 3755, got {len(values)}"
        )
    return values


def reasoning_sample_supplement_codepoints() -> set[int]:
    """Return missing glyphs observed in the reviewed reasoning sample."""
    text = REASONING_SAMPLE_SUPPLEMENT.read_text(encoding="utf-8-sig")
    values = {
        ord(character)
        for character in text
        if not character.isspace()
    }
    if not values:
        raise RuntimeError("reasoning sample supplement is empty")
    return values


def production_source_files() -> list[Path]:
    ui = APP / "fatfs_image" / "system" / "ui" / "rehab"
    files = [
        path for path in sorted(ui.glob("*.lua"))
        if path.name != "rehab_ui_text.lua"
    ]
    for component in ("rehab_ui", "rehab_task_decision", "rehab_integration"):
        source = APP / "components" / component / "src"
        files.extend(sorted(source.glob("*.c")))
        files.extend(sorted(source.glob("*.h")))
    score_template = (
        REPOSITORY / "pc" / "rehab_score_service" / "cloud_interface"
        / "session_processor.py"
    )
    if score_template.is_file():
        files.append(score_template)
    return files


def _quoted_literals(text: str, language: str) -> list[str]:
    """Extract C/Lua quoted literals while excluding comments."""
    literals: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        if language == "c" and text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if language == "c" and text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        if language == "lua" and text.startswith("--[[", index):
            end = text.find("]]", index + 4)
            index = length if end < 0 else end + 2
            continue
        if language == "lua" and text.startswith("--", index):
            newline = text.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if language == "lua" and text.startswith("[[", index):
            end = text.find("]]", index + 2)
            if end < 0:
                break
            literals.append(text[index + 2:end])
            index = end + 2
            continue
        quote = text[index]
        if quote not in ('"', "'"):
            index += 1
            continue
        index += 1
        value: list[str] = []
        while index < length:
            character = text[index]
            if character == "\\" and index + 1 < length:
                value.extend((character, text[index + 1]))
                index += 2
                continue
            if character == quote:
                index += 1
                break
            value.append(character)
            index += 1
        literals.append("".join(value))
    return literals


def source_literals(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix == ".py":
        tree = ast.parse(text, filename=str(path))
        return [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
    return _quoted_literals(text, "lua" if path.suffix == ".lua" else "c")


def production_codepoints() -> tuple[set[int], list[dict[str, object]]]:
    codepoints: set[int] = set()
    records: list[dict[str, object]] = []
    for path in production_source_files():
        values = {
            ord(character)
            for literal in source_literals(path)
            for character in literal
            if ord(character) > 0x7F
        }
        codepoints.update(values)
        records.append(
            {
                "path": path.relative_to(REPOSITORY).as_posix(),
                "non_ascii_codepoint_count": len(values),
            }
        )
    return codepoints, records
