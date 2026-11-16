#!/usr/bin/env python3
"""Production C selected/details binding and stale-file fault scenarios."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
SRC = COMPONENT / "src"
APP = COMPONENT.parents[1]


def selected(tag: str) -> dict:
    return {
        "schema_version": "rehab_selected_task.v2",
        "task_id": f"OT_{tag}", "task_name": f"任务{tag}",
        "reason": f"理由{tag}", "score": 70.0,
        "decision_id": f"decision-{tag}", "decision_generation": 3,
        "source_scan_id": f"scan-{tag}", "source_scene_sha256": tag.lower() * 64,
        "target_object_id": f"object-{tag}",
    }


def details(tag: str) -> dict:
    s = selected(tag)
    return {
        "schema_version": "rehab_selected_task_details.v1",
        "decision_id": s["decision_id"], "generation": s["decision_generation"],
        "source_scan_id": s["source_scan_id"],
        "source_scene_sha256": s["source_scene_sha256"],
        "selected_task": {
            "task_id": s["task_id"], "task_name": s["task_name"],
            "reason": s["reason"], "score": s["score"],
        },
        "target_details": {"object_id": s["target_object_id"]},
    }


class SelectedDetailsBindingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="details_binding_")
        cls.exe = Path(cls.temp.name) / "details_binding.exe"
        cjson = Path(r"C:\esp\v5.5.4\frameworks\esp-idf-v5.5.4\components\json\cJSON")
        env = os.environ.copy()
        env["PATH"] = r"C:\msys64\ucrt64\bin;" + env["PATH"]
        subprocess.run([
            r"C:\msys64\ucrt64\bin\gcc.exe", "-std=gnu11", "-Wall",
            "-Wextra", "-Werror", "-I", str(SRC), "-I", str(cjson),
            str(COMPONENT / "test/stage4_details_binding_host.c"),
            str(SRC / "rehab_task_decision_details_binding.c"),
            str(cjson / "cJSON.c"), "-lm", "-o", str(cls.exe),
        ], check=True, capture_output=True, text=True, env=env)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def check(self, d: dict, s: dict) -> subprocess.CompletedProcess[str]:
        dp = Path(self.temp.name) / "details.json"
        sp = Path(self.temp.name) / "selected.json"
        dp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        sp.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
        return subprocess.run([str(self.exe), str(dp), str(sp)],
                              capture_output=True, text=True, encoding="utf-8")

    def test_matching_authoritative_pair_is_valid(self) -> None:
        self.assertEqual(self.check(details("A"), selected("A")).returncode, 0)

    def test_a_to_b_details_write_failure_never_exposes_a(self) -> None:
        result = self.check(details("A"), selected("B"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("INVALID", result.stdout)

    def test_corruption_and_missing_authority_fail_closed(self) -> None:
        corrupt_details = details("A")
        corrupt_details["selected_task"].pop("score")
        self.assertNotEqual(self.check(corrupt_details, selected("A")).returncode, 0)
        corrupt_selected = selected("A")
        corrupt_selected.pop("decision_id")
        self.assertNotEqual(self.check(details("A"), corrupt_selected).returncode, 0)

    def test_failed_new_decision_preserves_old_pair(self) -> None:
        self.assertEqual(self.check(details("A"), selected("A")).returncode, 0)

    def test_actual_loader_and_both_ui_entries_bind_current_selected(self) -> None:
        loader = (SRC / "rehab_task_decision_details.c").read_text(encoding="utf-8")
        app = (APP / "fatfs_image/system/ui/rehab/esp_claw_rehab_ui.lua").read_text(encoding="utf-8")
        bridge = (APP / "components/rehab_ui/src/rehab_ui_task_decision_lua.c").read_text(encoding="utf-8")
        self.assertIn("rehab_task_decision_selected_get", loader)
        self.assertIn("rehab_task_decision_details_binding_matches", loader)
        self.assertIn("app.state.selected_decision_id", app)
        self.assertIn("task_decision_status or {}).decision_id", app)
        self.assertIn("rehab_task_decision_details_render", bridge)


if __name__ == "__main__":
    unittest.main()
