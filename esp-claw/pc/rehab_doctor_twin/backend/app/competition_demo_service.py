from __future__ import annotations
from typing import Literal
from pydantic import BaseModel
from .demo_engine import engine
class CompetitionDemoRequest(BaseModel):
    patient_id: str = "P-0217"
    speed_mode: Literal["standard", "fast"] = "standard"
def speed_for_mode(mode: str) -> float:
    return 2.6 if mode == "fast" else 1.18
async def start_competition_demo(request: CompetitionDemoRequest) -> dict:
    speed = speed_for_mode(request.speed_mode)
    result = await engine.start("drink_cup", speed=speed, patient_id=request.patient_id)
    return {**result, "competition_demo": True, "speed": speed, "speed_mode": request.speed_mode}
