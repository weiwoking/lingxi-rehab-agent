import math

import pytest

from app.person_follow_response import PersonFollowError
from tests.person_follow_helpers import FakeDetector, detection, jpeg, metadata, service


def test_single_person_and_exact_model_parameters():
    detector = FakeDetector([detection()])
    body = service(detector).detect(jpeg(), metadata())
    assert body["target_found"] is True
    assert len(body["detections"]) == 1
    assert detector.calls[0]["classes"] == [0]
    assert detector.calls[0]["confidence_threshold"] == 0.35
    assert detector.calls[0]["image_size"] == 640
    assert detector.calls[0]["device"] == "cpu"
    assert detector.calls[0]["half"] is False


def test_multiple_people_are_all_returned_in_deterministic_order():
    detector = FakeDetector(
        [
            detection(0.8, (300, 20, 500, 300)),
            detection(0.9, (200, 20, 300, 200)),
            detection(0.9, (100, 20, 300, 220)),
            detection(0.9, (50, 20, 250, 220)),
        ]
    )
    boxes = [item["bbox_px"] for item in service(detector).detect(jpeg(), metadata())["detections"]]
    assert [box["x1"] for box in boxes] == [50.0, 100.0, 200.0, 300.0]


def test_no_person_is_normal_success():
    body = service(FakeDetector()).detect(jpeg(), metadata())
    assert body["target_found"] is False
    assert body["detections"] == []


def test_non_target_class_is_filtered_even_if_detector_returns_it():
    body = service(FakeDetector([detection(class_name="cup"), detection()])).detect(jpeg(), metadata())
    assert len(body["detections"]) == 1
    assert body["detections"][0]["class_name"] == "person"


def test_target_class_missing_has_no_class_zero_fallback():
    detector = FakeDetector()
    with pytest.raises(PersonFollowError, match="not uniquely present") as raised:
        service(detector).detect(jpeg(), metadata(target_class="zxl"))
    assert raised.value.code == "TARGET_CLASS_MISSING"
    assert not detector.calls


@pytest.mark.parametrize(
    ("payload", "code"),
    [(b"not-a-jpeg", "BAD_JPEG"), (jpeg(320, 240), "IMAGE_SIZE_MISMATCH")],
)
def test_bad_jpeg_and_wrong_dimensions(payload, code):
    with pytest.raises(PersonFollowError) as raised:
        service(FakeDetector()).detect(payload, metadata())
    assert raised.value.code == code


def test_predict_exception_is_technical_error():
    detector = FakeDetector()
    detector.error = RuntimeError("boom")
    with pytest.raises(PersonFollowError) as raised:
        service(detector).detect(jpeg(), metadata())
    assert raised.value.code == "MODEL_INFERENCE_ERROR"


def test_timing_and_normalized_geometry_are_present_and_finite():
    body = service(FakeDetector([detection()])).detect(jpeg(), metadata())
    assert set(body["processing_time_ms"]) == {
        "decode", "predict_wall", "preprocess", "inference", "postprocess", "normalize", "total"
    }
    assert all(math.isfinite(value) and value >= 0 for value in body["processing_time_ms"].values())
    item = body["detections"][0]
    assert item["center_normalized"] == pytest.approx({"x": 0.375, "y": 0.5277777777777778})
    assert item["area_normalized"] == pytest.approx(0.3125)


def test_model_not_ready_is_not_no_target():
    instance = service(FakeDetector())
    instance.set_ready(False)
    with pytest.raises(PersonFollowError) as raised:
        instance.detect(jpeg(), metadata())
    assert raised.value.code == "MODEL_NOT_READY"


def test_viewer_sink_failure_does_not_change_success_response():
    instance = service(FakeDetector([detection()]))
    instance.latest_frame_sink = lambda *_args: (_ for _ in ()).throw(RuntimeError("viewer failed"))
    body = instance.detect(jpeg(), metadata())
    assert body["schema_version"] == "person_follow.v1"
    assert body["target_found"] is True
