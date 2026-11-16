import copy

import pytest
from pydantic import ValidationError

from app.person_follow_schemas import PersonFollowErrorBody, PersonFollowMetadata, PersonFollowSuccess
from tests.person_follow_helpers import metadata_dict


def valid_success(**overrides):
    body = {
        "schema_version": "person_follow.v1",
        "frame_id": "PF-000001",
        "frame_generation": 17,
        "capture_timestamp_us": 123456789,
        "capture_clock_domain": "esp_timer_monotonic",
        "image_width": 640,
        "image_height": 360,
        "target_class": "person",
        "target_found": True,
        "detections": [
            {
                "class_name": "person",
                "confidence": 0.86,
                "bbox_px": {"x1": 120.0, "y1": 40.0, "x2": 360.0, "y2": 340.0},
                "center_normalized": {"x": 0.375, "y": 0.5277777777777778},
                "area_normalized": 0.3125,
            }
        ],
        "model": {
            "logical_slot": "person_follow",
            "model_id": "coco-yolo11n",
            "device": "cpu",
            "half": False,
            "imgsz": 640,
            "confidence_threshold": 0.35,
        },
        "processing_time_ms": {
            "decode": 1.0,
            "predict_wall": 38.0,
            "preprocess": 1.0,
            "inference": 36.0,
            "postprocess": 1.0,
            "normalize": 0.2,
            "total": 39.2,
        },
    }
    body.update(overrides)
    return body


def test_valid_metadata_and_default_target_class():
    value = metadata_dict()
    value.pop("target_class")
    parsed = PersonFollowMetadata.model_validate(value)
    assert parsed.target_class == "person"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "person_follow_request.v2"),
        ("frame_id", ""),
        ("frame_id", "x" * 65),
        ("frame_id", "bad id"),
        ("frame_generation", -1),
        ("frame_generation", 4_294_967_296),
        ("capture_timestamp_us", -1),
        ("capture_clock_domain", "utc"),
        ("target_class", ""),
        ("target_class", "bad class"),
        ("target_class", "x" * 65),
    ],
)
def test_invalid_metadata_is_rejected(field, value):
    with pytest.raises(ValidationError):
        PersonFollowMetadata.model_validate(metadata_dict(**{field: value}))


def test_success_and_no_target_consistency():
    assert PersonFollowSuccess.model_validate(valid_success()).target_found is True
    no_target = valid_success(target_found=False, detections=[])
    assert PersonFollowSuccess.model_validate(no_target).detections == []
    with pytest.raises(ValidationError):
        PersonFollowSuccess.model_validate(valid_success(target_found=False))


@pytest.mark.parametrize(
    "mutator",
    [
        lambda body: body["detections"][0].update(class_name="cup"),
        lambda body: body["detections"][0].update(confidence=1.1),
        lambda body: body["detections"][0]["bbox_px"].update(x2=700.0),
        lambda body: body["detections"][0]["bbox_px"].update(x2=100.0),
        lambda body: body["detections"][0]["center_normalized"].update(x=0.5),
        lambda body: body["detections"][0].update(area_normalized=0.1),
    ],
)
def test_detection_geometry_constraints(mutator):
    body = copy.deepcopy(valid_success())
    mutator(body)
    with pytest.raises(ValidationError):
        PersonFollowSuccess.model_validate(body)


def test_error_schema_is_stable_and_strict():
    value = PersonFollowErrorBody(error={"code": "BAD_JPEG", "message": "bad"}).model_dump()
    assert value == {
        "schema_version": "person_follow_error.v1",
        "error": {"code": "BAD_JPEG", "message": "bad"},
    }
    with pytest.raises(ValidationError):
        PersonFollowErrorBody.model_validate({**value, "retryable": True})
