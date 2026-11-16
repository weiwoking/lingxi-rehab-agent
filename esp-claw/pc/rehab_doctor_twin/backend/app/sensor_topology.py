from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import DATA_DIR


@lru_cache(maxsize=1)
def load_sensor_topology() -> dict[str, Any]:
    path = DATA_DIR / "sensor_topology.json"
    return json.loads(path.read_text(encoding="utf-8"))


def imu_nodes() -> list[dict[str, Any]]:
    topology = load_sensor_topology()
    return list(topology.get("imu_nodes") or topology.get("imu") or [])


def semg_channels() -> list[dict[str, Any]]:
    topology = load_sensor_topology()
    channels = []
    if "semg_channels" in topology:
        return list(topology["semg_channels"])
    for channel in topology["semg"]["channels"]:
        channels.append(
            {
                **channel,
                "hub_id": topology["semg"]["hub_id"],
                "hub_name": topology["semg"]["hub_name"],
                "raw_sampling_hz": topology["semg"]["raw_sampling_hz"],
                "visualization_hz": topology["semg"]["visualization_hz"],
                "topology_mode": topology["topology_mode"],
            }
        )
    return channels


def system_services() -> list[dict[str, Any]]:
    return list(load_sensor_topology()["system_services"])
