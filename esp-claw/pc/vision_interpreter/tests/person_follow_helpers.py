import json
from typing import Any

import cv2
import numpy as np

from app.model_adapter import PredictionOutput
from app.person_follow_schemas import PersonFollowMetadata
from app.person_follow_service import PersonFollowService


def metadata_dict(**overrides: Any) -> dict[str, Any]:
    value = {
        "schema_version": "person_follow_request.v1",
        "frame_id": "PF-000001",
        "frame_generation": 17,
        "capture_timestamp_us": 123456789,
        "capture_clock_domain": "esp_timer_monotonic",
        "target_class": "person",
    }
    value.update(overrides)
    return value


def metadata(**overrides: Any) -> PersonFollowMetadata:
    return PersonFollowMetadata.model_validate(metadata_dict(**overrides))


def jpeg(width: int = 640, height: int = 360) -> bytes:
    ok, encoded = cv2.imencode(".jpg", np.zeros((height, width, 3), dtype=np.uint8))
    assert ok
    return encoded.tobytes()


def detection(
    confidence: float = 0.86,
    bbox: tuple[float, float, float, float] = (120.0, 40.0, 360.0, 340.0),
    class_name: str = "person",
) -> dict[str, Any]:
    return {
        "class_name": class_name,
        "confidence": confidence,
        "bbox_px": dict(zip(("x1", "y1", "x2", "y2"), bbox)),
    }


class FakeDetector:
    def __init__(self, detections: list[dict[str, Any]] | None = None) -> None:
        self.ready = True
        self.names = {0: "person", 1: "cup"}
        self.detections = detections or []
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def predict_detailed(self, _image, capture_id: str, **kwargs: Any) -> PredictionOutput:
        self.calls.append({"capture_id": capture_id, **kwargs})
        if self.error is not None:
            raise self.error
        return PredictionOutput(
            detections=self.detections,
            speed_ms={"preprocess": 1.0, "inference": 36.0, "postprocess": 1.0},
        )


def service(detector: FakeDetector | None = None, **overrides: Any) -> PersonFollowService:
    config = {
        "enabled": True,
        "logical_model_slot": "person_follow",
        "model_id": "coco-yolo11n",
        "target_class": "person",
        "confidence_threshold": 0.35,
        "image_size": 640,
        "device": "cpu",
        "half": False,
        "max_image_bytes": 131072,
        "warmup": True,
    }
    config.update(overrides)
    instance = PersonFollowService(detector or FakeDetector(), config)
    instance.set_ready(True)
    return instance


def metadata_part(**overrides: Any) -> tuple[None, str, str]:
    return (None, json.dumps(metadata_dict(**overrides)), "application/json")
