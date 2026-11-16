import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.person_follow_router import create_person_follow_router
from tests.person_follow_helpers import FakeDetector, detection, jpeg, metadata_dict, metadata_part, service


def client(detector=None, **config):
    instance = service(detector or FakeDetector(), **config)
    app = FastAPI()
    app.include_router(create_person_follow_router(instance))
    return TestClient(app)


def post(client_instance, image=jpeg(), image_type="image/jpeg", metadata_value=None):
    return client_instance.post(
        "/api/v1/person-follow/detect",
        files={
            "image": ("frame.jpg", image, image_type),
            "metadata": metadata_value or metadata_part(),
        },
    )


def assert_error(response, code):
    body = response.json()
    assert set(body) == {"schema_version", "error"}
    assert body["schema_version"] == "person_follow_error.v1"
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code


def test_normal_multipart_response_contract():
    response = post(client(FakeDetector([detection()])))
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "person_follow.v1"
    assert body["frame_id"] == "PF-000001"
    assert body["target_found"] is True
    assert set(body) == {
        "schema_version", "frame_id", "frame_generation", "capture_timestamp_us",
        "capture_clock_domain", "image_width", "image_height", "target_class",
        "target_found", "detections", "model", "processing_time_ms"
    }


@pytest.mark.parametrize("missing", ["metadata", "image"])
def test_missing_parts_are_bad_multipart(missing):
    files = {
        "image": ("frame.jpg", jpeg(), "image/jpeg"),
        "metadata": metadata_part(),
    }
    files.pop(missing)
    response = client().post("/api/v1/person-follow/detect", files=files)
    assert response.status_code == 400
    assert_error(response, "BAD_MULTIPART")


def test_non_multipart_body():
    response = client().post("/api/v1/person-follow/detect", json=metadata_dict())
    assert response.status_code == 400
    assert_error(response, "BAD_MULTIPART")


def test_wrong_image_mime():
    response = post(client(), image_type="application/octet-stream")
    assert response.status_code == 415
    assert_error(response, "UNSUPPORTED_MEDIA_TYPE")


def test_image_too_large():
    response = post(client(max_image_bytes=10), image=b"x" * 11)
    assert response.status_code == 413
    assert_error(response, "IMAGE_TOO_LARGE")


@pytest.mark.parametrize(
    ("image", "code", "status"),
    [(b"broken", "BAD_JPEG", 400), (jpeg(320, 240), "IMAGE_SIZE_MISMATCH", 422)],
)
def test_bad_jpeg_and_wrong_size(image, code, status):
    response = post(client(), image=image)
    assert response.status_code == status
    assert_error(response, code)


def test_bad_metadata_json_and_request_schema_error():
    response = post(client(), metadata_value=(None, "{", "application/json"))
    assert response.status_code == 400
    assert_error(response, "BAD_METADATA_JSON")
    response = post(client(), metadata_value=(None, json.dumps(metadata_dict(schema_version="bad")), "application/json"))
    assert response.status_code == 422
    assert_error(response, "REQUEST_SCHEMA_ERROR")


def test_target_missing_is_technical_non_2xx():
    response = post(client(), metadata_value=metadata_part(target_class="zxl"))
    assert response.status_code == 422
    assert_error(response, "TARGET_CLASS_MISSING")


def test_no_target_is_http_200():
    response = post(client(FakeDetector()))
    assert response.status_code == 200
    assert response.json()["target_found"] is False
    assert response.json()["detections"] == []


def test_predict_error_is_non_2xx_not_no_target():
    detector = FakeDetector()
    detector.error = RuntimeError("boom")
    response = post(client(detector))
    assert response.status_code == 500
    assert_error(response, "MODEL_INFERENCE_ERROR")


def test_model_not_ready_and_internal_error_are_stable_non_2xx():
    instance = service(FakeDetector())
    instance.set_ready(False)
    app = FastAPI()
    app.include_router(create_person_follow_router(instance))
    response = post(TestClient(app))
    assert response.status_code == 503
    assert_error(response, "MODEL_NOT_READY")

    instance.set_ready(True)
    instance.detect = lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyError("unexpected"))
    response = post(TestClient(app))
    assert response.status_code == 500
    assert_error(response, "INTERNAL_ERROR")


def test_disabled_configuration_keeps_route_registered_but_not_ready():
    instance = service(FakeDetector(), enabled=False)
    instance.set_ready(bool(instance.config["enabled"]))
    app = FastAPI()
    app.include_router(create_person_follow_router(instance))
    response = post(TestClient(app))
    assert response.status_code == 503
    assert_error(response, "MODEL_NOT_READY")
