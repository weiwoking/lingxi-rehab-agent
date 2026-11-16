from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import DEMO_DIR
from .models import ScenarioSummary


@lru_cache(maxsize=4)
def load_scenario(scenario_id: str = "drink_cup") -> dict[str, Any]:
    if scenario_id != "drink_cup":
        raise ValueError(f"Unknown scenario_id: {scenario_id}")
    path = DEMO_DIR / "drink_cup_scenario.json"
    return json.loads(path.read_text(encoding="utf-8"))


def list_scenarios() -> list[ScenarioSummary]:
    items: list[ScenarioSummary] = []
    for path in sorted(Path(DEMO_DIR).glob("*_scenario.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        items.append(
            ScenarioSummary(
                scenario_id=data["scenario_id"],
                name=data["name"],
                duration_sec=int(data["duration_sec"]),
                target_reps=int(data["target_reps"]),
                version=data["version"],
            )
        )
    return items

