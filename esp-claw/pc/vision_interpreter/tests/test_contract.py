import json
import copy

import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient

from app import main
from app.response_builder import build_error_response, build_success_response
from app.errors import VisionError
from app.config import get_config


SUCCESS_KEYS = {
    "schema_version",
    "capture_id",
    "status",
    "valid",
    "pairing",
    "detections",
    "summary",
    "processing_time_ms",
}
PAIRING_KEYS = {"image_timestamp_ms", "tof_timestamp_ms", "offset_ms", "max_offset_ms"}
SUMMARY_KEYS = {"object_count", "zxl_found", "person_count"}
TIMING_KEYS = {"decode", "yolo", "fusion", "total"}
DETECTION_KEYS = {
    "object_id",
    "class_name",
    "confidence",
    "bbox_px",
    "center_normalized",
    "horizontal_zone",
    "geometry",
}
BBOX_KEYS = {"x1", "y1", "x2", "y2"}
CENTER_KEYS = {"x", "y"}
GEOMETRY_KEYS = {
    "distance_cm",
    "object_height_cm",
    "bottom_height_from_ground_cm",
    "distance_valid",
    "object_height_valid",
    "bottom_height_valid",
    "quality",
    "tof_zone_ids",
}
NO_TOF_GEOMETRY_KEYS = GEOMETRY_KEYS | {"invalid_reason"}
ERROR_KEYS = {"schema_version", "capture_id", "status", "valid", "error"}
ERROR_DETAIL_KEYS = {"code", "message", "retryable"}
FORBIDDEN = {
    "camera_depth_cm",
    "geometry_method",
    "height_invalid_reason",
    "identity_probability",
    "model_a_confidence",
    "yolo_a",
    "identity_b",
    "detail",
    "traceback",
}


def assert_success_contract(body):
    assert set(body.keys()) == SUCCESS_KEYS
    assert set(body["pairing"].keys()) == PAIRING_KEYS
    assert set(body["summary"].keys()) == SUMMARY_KEYS
    assert set(body["processing_time_ms"].keys()) == TIMING_KEYS
    body_text = json.dumps(body)
    for key in FORBIDDEN:
        assert key not in body_text
    for det in body["detections"]:
        assert set(det.keys()) == DETECTION_KEYS
        assert type(det["object_id"]) is int
        assert isinstance(det["class_name"], str)
        assert isinstance(det["confidence"], (int, float)) and not isinstance(
            det["confidence"], bool
        )
        assert isinstance(det["bbox_px"], dict)
        assert set(det["bbox_px"].keys()) == BBOX_KEYS
        assert all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in det["bbox_px"].values()
        )
        assert isinstance(det["center_normalized"], dict)
        assert set(det["center_normalized"].keys()) == CENTER_KEYS
        assert all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in det["center_normalized"].values()
        )
        geom_keys = set(det["geometry"].keys())
        geometry = det["geometry"]
        assert isinstance(geometry["quality"], (int, float)) and not isinstance(
            geometry["quality"], bool
        )
        assert 0.0 <= geometry["quality"] <= 1.0
        assert isinstance(geometry["tof_zone_ids"], list)
        assert all(type(zone_id) is int for zone_id in geometry["tof_zone_ids"])
        for value_key, valid_key in (
            ("distance_cm", "distance_valid"),
            ("object_height_cm", "object_height_valid"),
            ("bottom_height_from_ground_cm", "bottom_height_valid"),
        ):
            assert type(geometry[valid_key]) is bool
            if geometry[value_key] is None:
                assert geometry[valid_key] is False
            else:
                assert isinstance(geometry[value_key], (int, float)) and not isinstance(
                    geometry[value_key], bool
                )
        if det["geometry"].get("distance_valid") is False:
            assert geom_keys == NO_TOF_GEOMETRY_KEYS
            assert det["geometry"]["invalid_reason"] == "no_valid_tof_zone"
        else:
            assert geom_keys == GEOMETRY_KEYS


def test_success_builder_strips_internal_fields():
    body = build_success_response(
        capture_id="c1",
        image_timestamp_ms=1,
        tof_timestamp_ms=2,
        offset_ms=1,
        max_offset_ms=100,
        detections=[
            {
                "object_id": 1,
                "class_name": "zxl",
                "confidence": 0.7,
                "bbox_px": {"x1": 1, "y1": 2, "x2": 3, "y2": 4},
                "center_normalized": {"x": 0.1, "y": 0.2},
                "horizontal_zone": "left",
                "identity_probability": {"zxl": 0.9},
                "geometry": {
                    "distance_cm": 120.0,
                    "object_height_cm": None,
                    "bottom_height_from_ground_cm": None,
                    "distance_valid": True,
                    "object_height_valid": False,
                    "bottom_height_valid": False,
                    "quality": 0.9,
                    "tof_zone_ids": [1],
                    "camera_depth_cm": 119.0,
                    "geometry_method": "internal",
                    "height_invalid_reason": "internal",
                },
            }
        ],
        decode_ms=1,
        yolo_ms=2,
        fusion_ms=3,
        total_ms=6,
    )
    assert_success_contract(body)
    assert body["summary"] == {"object_count": 1, "zxl_found": True, "person_count": 0}


def test_no_valid_tof_geometry_has_invalid_reason_only_extra():
    body = build_success_response(
        capture_id="c1",
        image_timestamp_ms=1,
        tof_timestamp_ms=2,
        offset_ms=1,
        max_offset_ms=100,
        detections=[
            {
                "object_id": 1,
                "class_name": "person",
                "confidence": 0.7,
                "bbox_px": {"x1": 1, "y1": 2, "x2": 3, "y2": 4},
                "center_normalized": {"x": 0.1, "y": 0.2},
                "horizontal_zone": "center",
                "geometry": {
                    "distance_cm": None,
                    "object_height_cm": None,
                    "bottom_height_from_ground_cm": None,
                    "distance_valid": False,
                    "object_height_valid": False,
                    "bottom_height_valid": False,
                    "quality": 0.0,
                    "tof_zone_ids": [],
                    "invalid_reason": "anything",
                    "geometry_method": "internal",
                },
            }
        ],
        decode_ms=1,
        yolo_ms=2,
        fusion_ms=3,
        total_ms=6,
    )
    assert_success_contract(body)
    assert body["summary"] == {"object_count": 1, "zxl_found": False, "person_count": 1}


def test_error_builder_strict_keys():
    body = build_error_response(
        VisionError("INVALID_TOF_DATA", "bad", http_status=400, capture_id="c1")
    )
    assert set(body.keys()) == ERROR_KEYS
    assert set(body["error"].keys()) == ERROR_DETAIL_KEYS
    assert type(body["error"]["retryable"]) is bool


def test_error_builder_preserves_retryable():
    body = build_error_response(
        VisionError(
            "PAIR_TIME_EXCEEDED",
            "Image and ToF timestamps exceed the allowed offset.",
            http_status=422,
            retryable=True,
            capture_id="c1",
        )
    )
    assert body == {
        "schema_version": "vision_fusion.v1",
        "capture_id": "c1",
        "status": "error",
        "valid": False,
        "error": {
            "code": "PAIR_TIME_EXCEEDED",
            "message": "Image and ToF timestamps exceed the allowed offset.",
            "retryable": True,
        },
    }


def test_identity_classifier_keeps_bbox_and_one_zxl(monkeypatch):
    cfg = {
        "model": {"normal_person_class_name": "person", "zxl_class_name": "zxl"},
        "identity": {
            "enabled": True,
            "input_size": 320,
            "bbox_expand_ratio": 0.05,
            "zxl_threshold": 0.59,
            "minimum_crop_side_px": 32,
            "person_class_index": 0,
            "zxl_class_index": 1,
        },
    }
    from app.identity_classifier import IdentityClassifier

    classifier = IdentityClassifier(cfg)
    class FakeModel:
        def __call__(self, _batch):
            return torch.tensor([[0.2, 0.9], [0.3, 0.8]], dtype=torch.float32)

    classifier.model = FakeModel()
    classifier.names = {0: "person", 1: "zxl"}
    image = np.zeros((360, 640, 3), dtype=np.uint8)
    detections = [
        {"class_name": "person", "confidence": 0.7, "bbox_px": {"x1": 10, "y1": 10, "x2": 110, "y2": 210}},
        {"class_name": "person", "confidence": 0.6, "bbox_px": {"x1": 200, "y1": 20, "x2": 320, "y2": 220}},
        {"class_name": "cup", "confidence": 0.9, "bbox_px": {"x1": 1, "y1": 1, "x2": 40, "y2": 40}},
    ]
    original_bboxes = [dict(item["bbox_px"]) for item in detections]
    updated, identity_ms = classifier.classify_person_detections(image, detections)
    assert identity_ms >= 0
    assert sum(1 for item in updated if item["class_name"] == "zxl") == 1
    assert updated[0]["class_name"] == "zxl"
    assert updated[0]["confidence"] == pytest.approx(float(torch.softmax(torch.tensor([0.2, 0.9]), dim=0)[1]))
    assert updated[1]["class_name"] == "person"
    assert updated[2]["class_name"] == "cup"
    assert [item["bbox_px"] for item in updated] == original_bboxes


def test_provisional_identity_threshold_config_and_model_b_gate():
    cfg = copy.deepcopy(get_config())
    assert cfg["identity"]["zxl_threshold"] == pytest.approx(0.95)
    assert cfg["identity"]["zxl_threshold_note"] == (
        "provisional prototype threshold; not final product threshold"
    )

    from app.identity_classifier import IdentityClassifier

    classifier = IdentityClassifier(cfg)

    class FakeModel:
        def __call__(self, _batch):
            # Model B probabilities: first crop is below 0.95, second is above.
            return torch.log(torch.tensor([[0.06, 0.94], [0.04, 0.96]], dtype=torch.float32))

    classifier.model = FakeModel()
    classifier.names = {0: "person", 1: "zxl"}
    image = np.zeros((360, 640, 3), dtype=np.uint8)
    detections = [
        {"class_name": "person", "confidence": 0.99, "bbox_px": {"x1": 10, "y1": 10, "x2": 110, "y2": 210}},
        {"class_name": "person", "confidence": 0.98, "bbox_px": {"x1": 200, "y1": 20, "x2": 320, "y2": 220}},
    ]

    updated, _ = classifier.classify_person_detections(image, detections)
    assert updated[0]["class_name"] == "person"
    assert updated[1]["class_name"] == "zxl"
    # Output confidence remains the conservative min(Model A, Model B), so P4
    # must not use confidence >= 0.95 as a replacement identity gate.
    assert updated[1]["confidence"] == pytest.approx(0.96, abs=1e-5)


def test_identity_batch_failure_fail_open():
    cfg = {
        "model": {"normal_person_class_name": "person", "zxl_class_name": "zxl"},
        "identity": {
            "enabled": True,
            "input_size": 320,
            "bbox_expand_ratio": 0.05,
            "zxl_threshold": 0.59,
            "minimum_crop_side_px": 32,
        },
    }
    from app.identity_classifier import IdentityClassifier

    classifier = IdentityClassifier(cfg)
    class BrokenModel:
        def __call__(self, _batch):
            raise RuntimeError("boom")

    classifier.model = BrokenModel()
    image = np.zeros((360, 640, 3), dtype=np.uint8)
    detections = [
        {"class_name": "person", "confidence": 0.7, "bbox_px": {"x1": 10, "y1": 10, "x2": 110, "y2": 210}}
    ]
    updated, _ = classifier.classify_person_detections(image, detections)
    assert updated[0]["class_name"] == "person"


def rgb565_bytes(width=640, height=360):
    return b"\x00\x00" * width * height


def tof_payload(**overrides):
    base = {
        "capture_id": "contract",
        "image_timestamp_ms": 1000,
        "tof_timestamp_ms": 1030,
        "resolution": "8x8",
        "distance_mm": [1200] * 64,
        "status_code": [0] * 64,
    }
    base.update(overrides)
    return base


def test_fuse_no_targets_success(monkeypatch):
    monkeypatch.setattr(main.model, "predict", lambda *_: [])
    monkeypatch.setattr(main.identity_model, "classify_person_detections", lambda _img, dets: (dets, 0))
    client = TestClient(main.app)
    response = client.post(
        "/api/v1/vision/fuse",
        files={"image": ("image.raw", rgb565_bytes(), "application/octet-stream")},
        data={"tof": json.dumps(tof_payload())},
    )
    assert response.status_code == 200
    body = response.json()
    assert_success_contract(body)
    assert body["detections"] == []


def test_fuse_invalid_image_size():
    client = TestClient(main.app)
    response = client.post(
        "/api/v1/vision/fuse",
        files={"image": ("image.raw", b"bad", "application/octet-stream")},
        data={"tof": json.dumps(tof_payload())},
    )
    assert response.status_code == 400
    body = response.json()
    assert set(body.keys()) == ERROR_KEYS
    assert set(body["error"].keys()) == ERROR_DETAIL_KEYS
    assert body["error"]["code"] == "INVALID_IMAGE_SIZE"


def test_fuse_invalid_tof_data():
    client = TestClient(main.app)
    response = client.post(
        "/api/v1/vision/fuse",
        files={"image": ("image.raw", rgb565_bytes(), "application/octet-stream")},
        data={"tof": json.dumps(tof_payload(distance_mm=[1]))},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_TOF_DATA"


def test_fuse_pair_time_exceeded():
    client = TestClient(main.app)
    response = client.post(
        "/api/v1/vision/fuse",
        files={"image": ("image.raw", rgb565_bytes(), "application/octet-stream")},
        data={"tof": json.dumps(tof_payload(tof_timestamp_ms=2000))},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PAIR_TIME_EXCEEDED"


def test_fuse_no_valid_tof_frame():
    client = TestClient(main.app)
    response = client.post(
        "/api/v1/vision/fuse",
        files={"image": ("image.raw", rgb565_bytes(), "application/octet-stream")},
        data={"tof": json.dumps(tof_payload(distance_mm=[0] * 64, status_code=[255] * 64))},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NO_VALID_TOF_FRAME"


def test_health_field_set_unchanged():
    body = main.health()
    assert set(body.keys()) == {
        "status",
        "model_ready",
        "model_path",
        "model_error",
        "model_classes",
        "geometry",
        "image",
        "tof",
    }
