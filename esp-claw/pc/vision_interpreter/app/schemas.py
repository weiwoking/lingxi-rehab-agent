from pydantic import BaseModel, Field, field_validator


class TofFrame(BaseModel):
    capture_id: str = Field(min_length=1, max_length=128)
    image_timestamp_ms: int = Field(ge=0)
    tof_timestamp_ms: int = Field(ge=0)
    resolution: str
    distance_mm: list[int]
    status_code: list[int]

    @field_validator("capture_id")
    @classmethod
    def normalize_capture_id(cls, value: str) -> str:
        return value.strip()
