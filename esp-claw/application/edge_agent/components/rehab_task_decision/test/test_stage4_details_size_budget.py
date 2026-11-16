#!/usr/bin/env python3
"""Worst-case details JSON size budget remains below the frozen 48 KiB cap."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
FIXTURE = COMPONENT / "test/fixtures/stage4b/decision_details_success.json"
HEADER = COMPONENT / "include/rehab_task_decision.h"


def compact_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8"))


def test_maximum_reasonable_fixture_fits_details_limit() -> None:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    value["patient_condition"] = {
        "source": "user_condition.txt", "status": "ok",
        "raw_text": "x" * 4096, "source_bytes": 65536,
        "sent_bytes": 4096, "truncated": True,
    }
    value["recent_performance"] = {
        "record_count": 3,
        "records": [{"session_id": f"session-{i}",
                     "task_name": "water-cup-task", "average_score": 99.999}
                    for i in range(3)],
    }
    value["item_frequency"] = {
        "mapped_record_count": 3, "cup_count": 3, "ball_count": 0,
        "records": [{"session_id": f"session-{i}",
                     "task_name": "water-cup-task", "mapped_group": "cup"}
                    for i in range(3)],
    }
    for candidate in value["candidates"]:
        candidate["reason"] = "理由" + "x" * 470
    winner = value["winner_ref"]
    value["selected_task"] = copy.deepcopy(value["candidates"][winner])
    value["winner"] = copy.deepcopy(value["candidates"][winner])
    value["deepseek_summary"] = "总结" + "x" * 470
    value["scoring_policy"] = {
        "policy_source": "rehab_task_decision_policy registry",
        "formula": "clamp(base+difficulty+advice+compensation+frequency+height+perception,0,100)",
        "parameters": {f"parameter_{i}": i + 0.123 for i in range(20)},
        "rules": {f"rule_{i}": "x" * 160 for i in range(8)},
    }
    header = HEADER.read_text(encoding="utf-8")
    match = re.search(
        r"REHAB_TASK_DECISION_DETAILS_JSON_MAX\s+\((\d+)U \* 1024U\)",
        header,
    )
    assert match is not None
    maximum = int(match.group(1)) * 1024
    serialized = compact_size(value)
    assert serialized < maximum, (serialized, maximum)
    assert maximum - serialized > 16 * 1024
