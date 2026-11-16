import logging
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
import torch.nn as nn

logger = logging.getLogger("vision-interpreter.identity")


class _LogitWrapper(nn.Module):
    def __init__(self, model: nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(self, x):
        out = self.model(x)
        return out[1] if isinstance(out, tuple) else out


class IdentityClassifier:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.identity_config = config.get("identity", {})
        self.model = None
        self.names: dict[int, str] = {}
        self.load_error: str | None = None
        self.last_error: str | None = None
        self.lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.identity_config.get("enabled", False))

    @property
    def ready(self) -> bool:
        return (not self.enabled) or self.model is not None

    def load(self) -> None:
        if not self.enabled:
            self.model = None
            self.names = {}
            self.load_error = None
            return

        try:
            from ultralytics import YOLO

            path = Path(str(self.identity_config["model_path"]))
            if not path.exists():
                raise FileNotFoundError(f"Identity model not found: {path}")

            ckpt = torch.load(str(path), map_location="cpu", weights_only=False)
            raw_names = ckpt.get("class_names", {0: "person", 1: "zxl"})
            if isinstance(raw_names, list):
                names = {idx: str(name) for idx, name in enumerate(raw_names)}
            else:
                names = {int(k): str(v) for k, v in raw_names.items()}
            expected = {
                int(self.identity_config.get("person_class_index", 0)): "person",
                int(self.identity_config.get("zxl_class_index", 1)): "zxl",
            }
            if names.get(0) != expected[0] or names.get(1) != expected[1]:
                raise ValueError(f"Unexpected identity class order: {names}")
            if len(names) != 2:
                raise ValueError(f"Identity model must expose 2 classes, got {len(names)}")
            if int(self.identity_config.get("input_size", 320)) != 320:
                raise ValueError("Identity input_size must be 320")

            base_weights = ckpt.get("base_weights", "yolo11n-cls.pt")
            base_path = path.parent / str(base_weights)
            yolo = YOLO(str(base_path if base_path.exists() else base_weights))
            model = yolo.model
            in_features = model.model[-1].linear.in_features
            model.model[-1].linear = nn.Linear(in_features, 2)
            wrapped = _LogitWrapper(model)
            wrapped.load_state_dict(ckpt["state_dict"])
            wrapped.to("cpu").eval()
            with torch.no_grad():
                logits = wrapped(torch.zeros((1, 3, 320, 320), dtype=torch.float32))
            if tuple(logits.shape) != (1, 2):
                raise ValueError(f"Identity output shape must be (1, 2), got {tuple(logits.shape)}")

            self.model = wrapped
            self.names = names
            self.load_error = None
            self.last_error = None
        except Exception as exc:
            self.model = None
            self.names = {}
            self.load_error = str(exc)
            raise

    def classify_person_detections(
        self,
        image_bgr: np.ndarray,
        detections: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        if not self.enabled:
            return detections, 0
        if self.model is None:
            logger.error("Identity model unavailable at request time: %s", self.load_error)
            return detections, 0

        start = time.perf_counter()
        person_name = self.config["model"].get("normal_person_class_name", "person")
        candidates: list[tuple[int, np.ndarray]] = []

        for idx, detection in enumerate(detections):
            if detection.get("class_name") != person_name:
                continue
            crop = self._crop_person(image_bgr, detection)
            if crop is not None:
                candidates.append((idx, crop))

        if not candidates:
            return detections, int((time.perf_counter() - start) * 1000)

        try:
            with self.lock:
                batch = torch.stack([self._preprocess(crop) for _, crop in candidates])
                with torch.no_grad():
                    probabilities = torch.softmax(self.model(batch), dim=1).cpu().numpy()
        except Exception as exc:
            self.last_error = str(exc)
            logger.exception("Identity batch inference failed; failing open as person")
            return detections, int((time.perf_counter() - start) * 1000)

        zxl_idx = int(self.identity_config.get("zxl_class_index", 1))
        person_idx = int(self.identity_config.get("person_class_index", 0))
        threshold = float(self.identity_config.get("zxl_threshold", 0.59))
        zxl_candidates: list[tuple[int, float]] = []

        for (det_idx, _), values in zip(candidates, probabilities):
            try:
                if len(values) != 2:
                    raise ValueError(f"expected 2 probabilities, got {len(values)}")
                person_probability = float(values[person_idx])
                zxl_probability = float(values[zxl_idx])
                detections[det_idx]["identity_probability"] = {
                    "person": person_probability,
                    "zxl": zxl_probability,
                }
                detections[det_idx]["model_a_confidence"] = float(
                    detections[det_idx].get("confidence", 0.0)
                )
                if zxl_probability >= threshold:
                    zxl_candidates.append((det_idx, zxl_probability))
            except Exception as exc:
                logger.warning("Identity crop classification failed; keeping person: %s", exc)

        if zxl_candidates:
            zxl_candidates.sort(key=lambda item: item[1], reverse=True)
            keep_idx, keep_prob = zxl_candidates[0]
            if len(zxl_candidates) > 1:
                logger.info("Multiple zxl candidates found; keeping highest probability only")
            model_a_conf = float(detections[keep_idx].get("confidence", 0.0))
            detections[keep_idx]["class_name"] = self.config["model"].get(
                "zxl_class_name", "zxl"
            )
            detections[keep_idx]["confidence"] = max(0.0, min(1.0, min(model_a_conf, keep_prob)))

        return detections, int((time.perf_counter() - start) * 1000)

    def _crop_person(self, image_bgr: np.ndarray, detection: dict[str, Any]) -> np.ndarray | None:
        bbox = detection.get("bbox_px", {})
        height, width = image_bgr.shape[:2]
        x1 = int(bbox.get("x1", 0))
        y1 = int(bbox.get("y1", 0))
        x2 = int(bbox.get("x2", 0))
        y2 = int(bbox.get("y2", 0))
        box_w = max(0, x2 - x1)
        box_h = max(0, y2 - y1)
        minimum = int(self.identity_config.get("minimum_crop_side_px", 32))
        if min(box_w, box_h) < minimum:
            logger.warning("Identity crop skipped because bbox is too small")
            return None

        ratio = float(self.identity_config.get("bbox_expand_ratio", 0.05))
        pad_x = round(box_w * ratio)
        pad_y = round(box_h * ratio)
        crop_x1 = max(0, x1 - pad_x)
        crop_y1 = max(0, y1 - pad_y)
        crop_x2 = min(width, x2 + pad_x)
        crop_y2 = min(height, y2 + pad_y)
        if crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
            logger.warning("Identity crop skipped because expanded crop is empty")
            return None
        return image_bgr[crop_y1:crop_y2, crop_x1:crop_x2].copy()

    def _preprocess(self, crop_bgr: np.ndarray) -> torch.Tensor:
        size = int(self.identity_config.get("input_size", 320))
        rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (size, size), interpolation=cv2.INTER_LINEAR)
        array = resized.astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        array = (array - mean) / std
        chw = np.transpose(array, (2, 0, 1))
        return torch.from_numpy(chw).float()
