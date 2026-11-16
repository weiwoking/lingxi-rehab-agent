import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .errors import VisionError


ALIASES = {
    "dining table": "table",
    "cell phone": "phone",
}


@dataclass(frozen=True)
class PredictionOutput:
    detections: list[dict[str, Any]]
    speed_ms: dict[str, float]


class YoloModel:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.model = None
        self.names: dict[int, str] = {}
        self.load_error: str | None = None
        self.lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self.model is not None

    def load(self) -> None:
        try:
            from ultralytics import YOLO

            path = Path(self.config["model"]["path"])
            if not path.is_absolute():
                path = Path(__file__).resolve().parents[1] / path
            if not path.exists():
                raise FileNotFoundError(f"Model not found: {path}")

            self.model = YOLO(str(path))
            raw_names = getattr(self.model, "names", {})
            self.names = {int(k): str(v) for k, v in raw_names.items()}
            self.config["model"]["resolved_path"] = str(path)
            self.load_error = None
        except Exception as exc:
            self.model = None
            self.names = {}
            self.load_error = str(exc)

    def predict(self, image_bgr: np.ndarray, capture_id: str) -> list[dict[str, Any]]:
        return self.predict_detailed(image_bgr, capture_id).detections

    def predict_detailed(
        self,
        image_bgr: np.ndarray,
        capture_id: str,
        *,
        classes: list[int] | None = None,
        confidence_threshold: float | None = None,
        image_size: int | None = None,
        device: str | None = None,
        half: bool | None = None,
    ) -> PredictionOutput:
        if not self.ready:
            raise VisionError(
                code="MODEL_UNAVAILABLE",
                message=self.load_error or "YOLO model unavailable.",
                http_status=503,
                retryable=True,
                capture_id=capture_id,
            )

        cfg = self.config["model"]
        allowed = set(cfg.get("allowed_classes", []))
        selected_device = str(device if device is not None else cfg.get("device", "cpu"))
        selected_half = bool(half) if half is not None else False
        if selected_device.lower() == "cpu" and selected_half:
            raise VisionError(
                code="INFERENCE_FAILED",
                message="YOLO half precision is not supported on CPU.",
                http_status=500,
                retryable=False,
                capture_id=capture_id,
            )

        predict_kwargs: dict[str, Any] = {
            "source": image_bgr,
            "conf": float(confidence_threshold if confidence_threshold is not None else cfg.get("confidence_threshold", 0.35)),
            "imgsz": int(image_size if image_size is not None else cfg.get("image_size", 640)),
            "device": selected_device,
            "verbose": False,
        }
        if classes is not None:
            predict_kwargs["classes"] = list(classes)
        if selected_device.lower() != "cpu":
            predict_kwargs["half"] = selected_half

        try:
            with self.lock:
                results = self.model.predict(**predict_kwargs)
        except Exception as exc:
            raise VisionError(
                code="INFERENCE_FAILED",
                message=f"YOLO inference failed: {exc}",
                http_status=500,
                retryable=True,
                capture_id=capture_id,
            ) from exc

        if not results or getattr(results[0], "boxes", None) is None:
            return PredictionOutput(detections=[], speed_ms={})

        output: list[dict[str, Any]] = []
        for box in results[0].boxes:
            class_id = int(box.cls.item())
            raw_name = self.names.get(class_id, str(class_id))
            class_name = ALIASES.get(raw_name, raw_name)
            if allowed and class_name not in allowed:
                continue

            x1, y1, x2, y2 = box.xyxy[0].tolist()
            output.append(
                {
                    "class_name": class_name,
                    "confidence": float(box.conf.item()),
                    "bbox_px": {
                        "x1": int(round(x1)),
                        "y1": int(round(y1)),
                        "x2": int(round(x2)),
                        "y2": int(round(y2)),
                    },
                }
            )
        raw_speed = getattr(results[0], "speed", {}) or {}
        speed_ms = {
            key: float(raw_speed.get(key, 0.0))
            for key in ("preprocess", "inference", "postprocess")
        }
        return PredictionOutput(detections=output, speed_ms=speed_ms)
