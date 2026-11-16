#!/usr/bin/env python3
"""Real C ability-line parser and full raw-text preservation tests."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
SRC = COMPONENT / "src"


class UserConditionContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="ability_parser_")
        cls.exe = Path(cls.temp.name) / "ability_parser.exe"
        env = os.environ.copy()
        env["PATH"] = r"C:\msys64\ucrt64\bin;" + env["PATH"]
        subprocess.run([
            r"C:\msys64\ucrt64\bin\gcc.exe", "-std=gnu11", "-Wall",
            "-Wextra", "-Werror", "-I", str(SRC),
            str(COMPONENT / "test/stage4_ability_parser_host.c"),
            str(SRC / "rehab_task_decision_policy.c"), "-lm", "-o",
            str(cls.exe),
        ], check=True, capture_output=True, text=True, env=env)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def parse(self, text: str, bom: bool = False) -> str:
        path = Path(self.temp.name) / "user_condition.txt"
        payload = text.encode("utf-8")
        path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + payload)
        result = subprocess.run([str(self.exe), str(path)], capture_output=True,
                                text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_exact_levels_lf_crlf_bom_and_spaces(self) -> None:
        cases = [
            ("当前任务能力等级：一级\n建议训练", 1, False),
            ("当前任务能力等级 : 二级；\r\n建议训练", 2, False),
            ("\t 当前任务能力等级：三级。　\n建议训练", 3, True),
        ]
        for text, level, bom in cases:
            with self.subTest(level=level, bom=bom):
                output = self.parse(text, bom)
                self.assertIn(f"level={level}\nstatus=parsed", output)
                self.assertIn("raw-begin", output)
                self.assertIn("建议训练", output)

    def test_quote_and_same_line_tail_do_not_change_ability(self) -> None:
        quoted = "医生引用“当前任务能力等级：三级”，但仅为说明。"
        output = self.parse(quoted)
        self.assertIn("level=1\nstatus=missing", output)
        self.assertIn(quoted, output)
        tailed = "当前任务能力等级：二级 额外尾文"
        output = self.parse(tailed)
        self.assertIn("level=1\nstatus=invalid", output)
        self.assertIn(tailed, output)

    def test_duplicate_missing_and_all_other_lines_preserved(self) -> None:
        duplicate = (
            "前置自由文本\n当前任务能力等级：二级\n中间医嘱\n"
            "当前任务能力等级：三级\n后置自由文本"
        )
        output = self.parse(duplicate)
        self.assertIn("level=1\nstatus=duplicate", output)
        self.assertIn(duplicate, output)
        missing = self.parse("完整普通医嘱")
        self.assertIn("level=1\nstatus=missing", missing)
        self.assertIn("完整普通医嘱", missing)

    def test_long_free_text_is_not_replaced_with_no_advice(self) -> None:
        advice = "长期康复建议。" * 500
        output = self.parse("当前任务能力等级：一级\n" + advice)
        self.assertIn("level=1\nstatus=parsed", output)
        self.assertIn(advice, output)
        context = (SRC / "rehab_task_decision_context.c").read_text(encoding="utf-8")
        self.assertIn("utf8_prefix_length", context)
        self.assertIn('snapshot->status = "ok"', context)
        self.assertIn("prescription.parse_text", context)

    def test_final_real_fixture_parses_level_two_without_first_line_rule(self) -> None:
        fixture = (
            "患者基本情况：\n\n"
            "本患者为康复任务决策系统的演示患者。\n\n"
            "当前任务能力等级：二级。\n\n"
            "患者可进行一级和二级康复训练任务。\n"
            "当前无其他专项训练医嘱。"
        )
        output = self.parse(fixture)
        self.assertIn("level=2\nstatus=parsed", output)
        self.assertIn(fixture, output)


if __name__ == "__main__":
    unittest.main()
