import math
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


FRAME_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
TARGET_CLASS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
REQUEST_SCHEMA_VERSION = "person_follow_request.v1"
SUCCESS_SCHEMA_VERSION = "person_follow.v1"
ERROR_SCHEMA_VERSION = "person_follow_error.v1"
CLOCK_DOMAIN = "esp_timer_monotonic"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PersonFollowMetadata(StrictModel):
    schema_version: str
    frame_id: str
    frame_generation: int = Field(ge=0, le=4_294_967_295)
    capture_timestamp_us: int = Field(ge=0, le=9_223_372_036_854_775_807)
    capture_clock_domain: str
    target_class: str = "person"

    @field_validator("frame_generation", "capture_timestamp_us", mode="before")
    @classmethod
    def validate_integer_fields(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("value must be an integer")
        return value

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != REQUEST_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {REQUEST_SCHEMA_VERSION}")
        return value

    @field_validator("frame_id")
    @classmethod
    def validate_frame_id(cls, value: str) -> str:
        if not FRAME_ID_PATTERN.fullmatch(value):
            raise ValueError("frame_id contains unsupported characters or length")
        return value

    @field_validator("capture_clock_domain")
    @classmethod
    def validate_clock_domain(cls, value: str) -> str:
        if value != CLOCK_DOMAIN:
            raise ValueError(f"capture_clock_domain must be {CLOCK_DOMAIN}")
        return value

    @field_validator("target_class")
    @classmethod
    def validate_target_class(cls, value: str) -> str:
        if not TARGET_CLASS_PATTERN.fullmatch(value):
            raise ValueError("target_class contains unsupported characters or length")
        return value


class BoundingBox(StrictModel):
    x1: float = Field(allow_inf_nan=False)
    y1: float = Field(allow_inf_nan=False)
    x2: float = Field(allow_inf_nan=False)
    y2: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_order(self) -> "BoundingBox":
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("bbox coordinates must have positive width and height")
        return self


class CenterNormalized(StrictModel):
    x: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    y: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class PersonFollowDetection(StrictModel):
    class_name: str
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    bbox_px: BoundingBox
    center_normalized: CenterNormalized
    area_normalized: float = Field(gt=0.0, le=1.0, allow_inf_nan=False)


class PersonFollowModelInfo(StrictModel):
    logical_slot: str
    model_id: str
    device: str
    half: bool
    imgsz: int = Field(gt=0)
    confidence_threshold: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class PersonFollowTiming(StrictModel):
    decode: float = Field(ge=0.0, allow_inf_nan=False)
    predict_wall: float = Field(ge=0.0, allow_inf_nan=False)
    preprocess: float = Field(ge=0.0, allow_inf_nan=False)
    inference: float = Field(ge=0.0, allow_inf_nan=False)
    postprocess: float = Field(ge=0.0, allow_inf_nan=False)
    normalize: float = Field(ge=0.0, allow_inf_nan=False)
    total: float = Field(ge=0.0, allow_inf_nan=False)


class PersonFollowSuccess(StrictModel):
    schema_version: str
    frame_id: str
    frame_generation: int
    capture_timestamp_us: int
    capture_clock_domain: str
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    target_class: str
    target_found: bool
    detections: list[PersonFollowDetection]
    model: PersonFollowModelInfo
    processing_time_ms: PersonFollowTiming

    @model_validator(mode="after")
    def validate_contract(self) -> "PersonFollowSuccess":
        if self.schema_version != SUCCESS_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SUCCESS_SCHEMA_VERSION}")
        if self.target_found != bool(self.detections):
            raise ValueError("target_found must equal bool(detections)")
        for detection in self.detections:
            if detection.class_name != self.target_class:
                raise ValueError("each detection must match target_class")
            box = detection.bbox_px
            if box.x1 < 0 or box.y1 < 0 or box.x2 > self.image_width or box.y2 > self.image_height:
                raise ValueError("bbox must stay inside image bounds")
            expected_x = (box.x1 + box.x2) / (2.0 * self.image_width)
            expected_y = (box.y1 + box.y2) / (2.0 * self.image_height)
            expected_area = ((box.x2 - box.x1) * (box.y2 - box.y1)) / (self.image_width * self.image_height)
            if not math.isclose(detection.center_normalized.x, expected_x, abs_tol=1e-6):
                raise ValueError("center_normalized.x is inconsistent with bbox")
            if not math.isclose(detection.center_normalized.y, expected_y, abs_tol=1e-6):
                raise ValueError("center_normalized.y is inconsistent with bbox")
            if not math.isclose(detection.area_normalized, expected_area, abs_tol=1e-6):
                raise ValueError("area_normalized is inconsistent with bbox")
        return self


class PersonFollowErrorDetail(StrictModel):
    code: str
    message: str


class PersonFollowErrorBody(StrictModel):
    schema_version: str = ERROR_SCHEMA_VERSION
    error: PersonFollowErrorDetail
