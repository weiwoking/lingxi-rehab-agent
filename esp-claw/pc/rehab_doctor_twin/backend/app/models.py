from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MessageType = Literal[
    "rehab.state",
    "rehab.telemetry",
    "rehab.emg",
    "rehab.score",
    "rehab.alert",
    "rehab.event",
    "rehab.report_ready",
    "device.status",
]


class RealtimeMessage(BaseModel):
    schema_version: str = "1.0"
    type: MessageType
    device_id: str
    patient_id: str
    session_id: str
    seq: int = Field(ge=1)
    timestamp_ms: int
    source_mode: Literal["demo", "live", "hybrid", "presentation", "device", "pc_local"] = "demo"
    payload: dict[str, Any]


class StartDemoRequest(BaseModel):
    scenario_id: str = "drink_cup"
    patient_id: str = "P-0217"
    speed: float = Field(default=1.0, ge=0.25, le=40.0)


class ScenarioSummary(BaseModel):
    scenario_id: str
    name: str
    duration_sec: int
    target_reps: int
    version: str
