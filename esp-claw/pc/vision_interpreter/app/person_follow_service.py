import math
import logging
import time
from typing import Any, Callable

import cv2
import numpy as np

from .errors import VisionError
from .person_follow_response import PersonFollowError, build_success
from .person_follow_schemas import PersonFollowMetadata


IMAGE_WIDTH = 640
IMAGE_HEIGHT = 360
logger = logging.getLogger("vision-interpreter.person-follow")


class PersonFollowService:
    def __init__(
        self,
        detector: Any,
        config: dict[str, Any],
        latest_frame_sink: Callable[[bytes, np.ndarray, dict[str, Any]], None] | None = None,
    ) -> None:
        self.detector = detector
        self.config = config
        self.latest_frame_sink = latest_frame_sink
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready and bool(getattr(self.detector, "ready", False))

    def set_ready(self, ready: bool) -> None:
        self._ready = ready

    def _resolve_target_id(self, target_class: str) -> int:
        matches = [int(class_id) for class_id, name in self.detector.names.items() if str(name) == target_class]
        if len(matches) != 1:
            raise PersonFollowError(
                "TARGET_CLASS_MISSING",
                f"Target class {target_class!r} is not uniquely present in model names.",
                422,
            )
        return matches[0]

    def detect(self, jpeg: bytes, metadata: PersonFollowMetadata) -> dict[str, Any]:
        total_start = time.perf_counter()
        if not self.ready:
            raise PersonFollowError("MODEL_NOT_READY", "Person-follow model is not ready.", 503)

        decode_start = time.perf_counter()
        try:
            image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception as exc:
            raise PersonFollowError("BAD_JPEG", f"JPEG decode failed: {exc}", 400) from exc
        decode_ms = (time.perf_counter() - decode_start) * 1000.0
        if image is None:
            raise PersonFollowError("BAD_JPEG", "JPEG decode returned no image.", 400)
        if image.shape != (IMAGE_HEIGHT, IMAGE_WIDTH, 3):
            actual = "x".join(str(value) for value in image.shape)
            raise PersonFollowError(
                "IMAGE_SIZE_MISMATCH",
                f"Decoded image must be 640x360x3 BGR; received shape {actual}.",
                422,
            )

        target_class = metadata.target_class
        target_id = self._resolve_target_id(target_class)
        predict_start = time.perf_counter()
        try:
            prediction = self.detector.predict_detailed(
                image,
                metadata.frame_id,
                classes=[target_id],
                confidence_threshold=float(self.config["confidence_threshold"]),
                image_size=int(self.config["image_size"]),
                device=str(self.config["device"]),
                half=bool(self.config["half"]),
            )
        except VisionError as exc:
            raise PersonFollowError("MODEL_INFERENCE_ERROR", exc.message, 500) from exc
        except Exception as exc:
            raise PersonFollowError("MODEL_INFERENCE_ERROR", f"Model inference failed: {exc}", 500) from exc
        predict_wall_ms = (time.perf_counter() - predict_start) * 1000.0

        normalize_start = time.perf_counter()
        normalized: list[dict[str, Any]] = []
        for item in prediction.detections:
            if item.get("class_name") != target_class:
                continue
            try:
                confidence = float(item["confidence"])
                source_box = item["bbox_px"]
                values = [float(source_box[key]) for key in ("x1", "y1", "x2", "y2")]
            except (KeyError, TypeError, ValueError) as exc:
                raise PersonFollowError("MODEL_INFERENCE_ERROR", f"Malformed model detection: {exc}", 500) from exc
            if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0 or not all(math.isfinite(value) for value in values):
                raise PersonFollowError("MODEL_INFERENCE_ERROR", "Model returned non-finite or out-of-range values.", 500)
            x1 = min(float(IMAGE_WIDTH), max(0.0, values[0]))
            y1 = min(float(IMAGE_HEIGHT), max(0.0, values[1]))
            x2 = min(float(IMAGE_WIDTH), max(0.0, values[2]))
            y2 = min(float(IMAGE_HEIGHT), max(0.0, values[3]))
            if x2 <= x1 or y2 <= y1:
                raise PersonFollowError("MODEL_INFERENCE_ERROR", "Model returned an invalid bounding box.", 500)
            area = ((x2 - x1) * (y2 - y1)) / (IMAGE_WIDTH * IMAGE_HEIGHT)
            normalized.append(
                {
                    "class_name": target_class,
                    "confidence": confidence,
                    "bbox_px": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                    "center_normalized": {
                        "x": (x1 + x2) / (2.0 * IMAGE_WIDTH),
                        "y": (y1 + y2) / (2.0 * IMAGE_HEIGHT),
                    },
                    "area_normalized": area,
                }
            )
        normalized.sort(
            key=lambda item: (
                -item["confidence"],
                -item["area_normalized"],
                item["bbox_px"]["x1"],
                item["bbox_px"]["y1"],
            )
        )
        normalize_ms = (time.perf_counter() - normalize_start) * 1000.0
        speed = prediction.speed_ms
        timing = {
            "decode": round(decode_ms, 3),
            "predict_wall": round(predict_wall_ms, 3),
            "preprocess": round(float(speed.get("preprocess", 0.0)), 3),
            "inference": round(float(speed.get("inference", 0.0)), 3),
            "postprocess": round(float(speed.get("postprocess", 0.0)), 3),
            "normalize": round(normalize_ms, 3),
            "total": round((time.perf_counter() - total_start) * 1000.0, 3),
        }
        response = build_success(
            metadata=metadata,
            target_class=target_class,
            detections=normalized,
            model={
                "logical_slot": str(self.config["logical_model_slot"]),
                "model_id": str(self.config["model_id"]),
                "device": str(self.config["device"]),
                "half": bool(self.config["half"]),
                "imgsz": int(self.config["image_size"]),
                "confidence_threshold": float(self.config["confidence_threshold"]),
            },
            timing=timing,
        )
        if self.latest_frame_sink is not None:
            try:
                self.latest_frame_sink(jpeg, image, response)
            except Exception:
                logger.exception("Failed to update latest person-follow viewer frame")
        return response
