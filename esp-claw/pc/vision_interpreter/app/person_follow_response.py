from dataclasses import dataclass
from typing import Any

from .person_follow_schemas import (
    ERROR_SCHEMA_VERSION,
    SUCCESS_SCHEMA_VERSION,
    PersonFollowErrorBody,
    PersonFollowMetadata,
    PersonFollowSuccess,
)


@dataclass
class PersonFollowError(Exception):
    code: str
    message: str
    http_status: int


def build_error(error: PersonFollowError) -> dict[str, Any]:
    return PersonFollowErrorBody(
        schema_version=ERROR_SCHEMA_VERSION,
        error={"code": error.code, "message": error.message},
    ).model_dump()


def build_success(
    *,
    metadata: PersonFollowMetadata,
    target_class: str,
    detections: list[dict[str, Any]],
    model: dict[str, Any],
    timing: dict[str, float],
) -> dict[str, Any]:
    return PersonFollowSuccess(
        schema_version=SUCCESS_SCHEMA_VERSION,
        frame_id=metadata.frame_id,
        frame_generation=metadata.frame_generation,
        capture_timestamp_us=metadata.capture_timestamp_us,
        capture_clock_domain=metadata.capture_clock_domain,
        image_width=640,
        image_height=360,
        target_class=target_class,
        target_found=bool(detections),
        detections=detections,
        model=model,
        processing_time_ms=timing,
    ).model_dump()
