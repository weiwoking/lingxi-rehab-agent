import copy
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .config import get_config
from .detector_registry import DetectorRegistry, WarmupSettings
from .errors import VisionError
from .fusion import interpret
from .identity_classifier import IdentityClassifier
from .model_adapter import YoloModel
from .person_follow_router import create_person_follow_router
from .person_follow_service import PersonFollowService
from .person_follow_viewer import LatestPersonFollowFrameStore, create_person_follow_viewer_router
from .response_builder import build_error_response, build_success_response
from .rgb565 import decode_rgb565
from .schemas import TofFrame


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("vision-interpreter")

config = get_config()
model = YoloModel(config)
identity_model = IdentityClassifier(config)
detector_registry = DetectorRegistry()

fusion_warmup = WarmupSettings(
    enabled=True,
    image_size=int(config["model"].get("image_size", 640)),
    device=str(config["model"].get("device", "cpu")),
    half=False,
)
detector_registry.register(
    "fusion",
    config["model"]["path"],
    lambda: model,
    fusion_warmup,
)

person_follow_config = config["person_follow"]


def _new_person_follow_detector() -> YoloModel:
    detector_config = copy.deepcopy(config)
    detector_config["model"]["path"] = person_follow_config["model_path"]
    detector_config["model"]["device"] = person_follow_config["device"]
    detector_config["model"]["image_size"] = person_follow_config["image_size"]
    detector_config["model"]["confidence_threshold"] = person_follow_config["confidence_threshold"]
    detector_config["model"]["allowed_classes"] = []
    return YoloModel(detector_config)


person_follow_detector = detector_registry.register(
    str(person_follow_config["logical_model_slot"]),
    person_follow_config["model_path"],
    _new_person_follow_detector,
    WarmupSettings(
        enabled=bool(person_follow_config["warmup"]),
        image_size=int(person_follow_config["image_size"]),
        device=str(person_follow_config["device"]),
        half=bool(person_follow_config["half"]),
    ),
)
person_follow_frame_store = LatestPersonFollowFrameStore()
person_follow_service = PersonFollowService(
    person_follow_detector,
    person_follow_config,
    person_follow_frame_store.update,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        detector_registry.load_all()
        if not model.ready:
            logger.error("YOLO model unavailable: %s", model.load_error)
            raise RuntimeError(model.load_error or "YOLO model unavailable")
        logger.info("YOLO model loaded")
        identity_model.load()
        if identity_model.enabled:
            logger.info("Identity model loaded")
        detector_registry.warmup_all()
        person_follow_service.set_ready(
            bool(person_follow_config["enabled"])
            and detector_registry.is_warmed(str(person_follow_config["logical_model_slot"]))
        )
        logger.info(
            "Person-follow ready=%s shared_with_fusion=%s",
            person_follow_service.ready,
            detector_registry.aliases_same_instance(
                "fusion", str(person_follow_config["logical_model_slot"])
            ),
        )
        yield
    finally:
        person_follow_service.set_ready(False)
        detector_registry.close_all()


app = FastAPI(
    title="Aliyun Vision Interpreter",
    version="2.0.0",
    lifespan=lifespan,
)
app.include_router(create_person_follow_router(person_follow_service))
app.include_router(create_person_follow_viewer_router(person_follow_frame_store))


def error_body(error: VisionError) -> dict[str, Any]:
    return build_error_response(error)


@app.exception_handler(VisionError)
async def handle_vision_error(_, error: VisionError):
    return JSONResponse(
        status_code=error.http_status,
        content=error_body(error),
    )


@app.exception_handler(Exception)
async def handle_unexpected(_, error: Exception):
    logger.exception("Unexpected error")
    wrapped = VisionError(
        code="INTERNAL_ERROR",
        message="Unexpected server error.",
        http_status=500,
        retryable=True,
    )
    return JSONResponse(status_code=500, content=error_body(wrapped))


@app.get("/api/v1/vision/health")
def health() -> dict[str, Any]:
    model_ready = model.ready and identity_model.ready
    model_error = model.load_error or identity_model.load_error or identity_model.last_error
    return {
        "status": "ok" if model_error is None else "degraded",
        "model_ready": model_ready,
        "model_path": config["model"].get(
            "resolved_path",
            config["model"]["path"],
        ),
        "model_error": model_error,
        "model_classes": list(model.names.values()),
        "geometry": {
            "algorithm": config["geometry"]["algorithm"],
            "camera_calibration_ready": config["camera"]["calibration_ready"],
            "mount_calibration_ready": config["camera_mount"]["calibration_ready"],
            "distance_definition": config["geometry"]["distance_definition"],
        },
        "image": config["image"],
        "tof": {
            "resolution": config["tof"]["resolution"],
            "rows": config["tof"]["rows"],
            "cols": config["tof"]["cols"],
        },
    }


@app.post("/api/v1/vision/fuse")
async def fuse(
    image: UploadFile = File(...),
    tof: str = Form(...),
):
    total_start = time.perf_counter()

    try:
        payload = TofFrame.model_validate(json.loads(tof))
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise VisionError(
            code="INVALID_TOF_DATA",
            message=f"Invalid ToF JSON: {exc}",
            http_status=400,
        ) from exc

    expected_count = int(config["tof"]["rows"]) * int(config["tof"]["cols"])
    if payload.resolution != config["tof"]["resolution"]:
        raise VisionError(
            code="INVALID_TOF_DATA",
            message="Unexpected ToF resolution.",
            http_status=400,
            capture_id=payload.capture_id,
        )
    if len(payload.distance_mm) != expected_count:
        raise VisionError(
            code="INVALID_TOF_DATA",
            message=f"distance_mm must have {expected_count} values.",
            http_status=400,
            capture_id=payload.capture_id,
        )
    if len(payload.status_code) != expected_count:
        raise VisionError(
            code="INVALID_TOF_DATA",
            message=f"status_code must have {expected_count} values.",
            http_status=400,
            capture_id=payload.capture_id,
        )
    invalid_status = set(int(v) for v in config["tof"].get("invalid_status_codes", [255]))
    min_mm = int(config["tof"].get("min_distance_mm", 100))
    max_mm = int(config["tof"].get("max_distance_mm", 4000))
    has_valid_tof = any(
        int(status) not in invalid_status and min_mm <= int(distance) <= max_mm
        for distance, status in zip(payload.distance_mm, payload.status_code)
    )
    diagnostic_valid_pairs = [
        (index, int(distance), int(status))
        for index, (distance, status) in enumerate(
            zip(payload.distance_mm, payload.status_code)
        )
        if int(status) not in invalid_status
        and min_mm <= int(distance) <= max_mm
    ]

    diagnostic_status_counts: dict[int, int] = {}
    for status in payload.status_code:
        status_value = int(status)
        diagnostic_status_counts[status_value] = (
            diagnostic_status_counts.get(status_value, 0) + 1
        )

    print(
        "TOF_INPUT_DIAG "
        f"capture_id={payload.capture_id} "
        f"valid_count={len(diagnostic_valid_pairs)} "
        f"status_counts={diagnostic_status_counts} "
        f"distance_min={min(int(v) for v in payload.distance_mm)} "
        f"distance_max={max(int(v) for v in payload.distance_mm)} "
        f"valid_pairs={diagnostic_valid_pairs[:16]}",
        flush=True,
    )
    if not has_valid_tof:
        raise VisionError(
            code="NO_VALID_TOF_FRAME",
            message="No valid ToF zone in frame.",
            http_status=422,
            retryable=True,
            capture_id=payload.capture_id,
        )

    offset = abs(payload.image_timestamp_ms - payload.tof_timestamp_ms)
    max_offset = int(config["tof"]["max_pair_offset_ms"])
    if offset > max_offset:
        raise VisionError(
            code="PAIR_TIME_EXCEEDED",
            message=f"Image/ToF offset {offset} ms exceeds {max_offset} ms.",
            http_status=422,
            retryable=True,
            capture_id=payload.capture_id,
        )

    raw = await image.read()

    t0 = time.perf_counter()
    image_bgr = decode_rgb565(
        raw,
        int(config["image"]["width"]),
        int(config["image"]["height"]),
        str(config["image"]["byte_order"]),
        payload.capture_id,
    )
    decode_ms = int((time.perf_counter() - t0) * 1000)

    t1 = time.perf_counter()
    detections = model.predict(image_bgr, payload.capture_id)
    model_a_ms = int((time.perf_counter() - t1) * 1000)

    detections, identity_ms = identity_model.classify_person_detections(
        image_bgr,
        detections,
    )
    yolo_ms = model_a_ms + identity_ms

    t2 = time.perf_counter()
    try:
        interpreted = interpret(detections, payload, config, offset)
    except VisionError:
        raise
    except Exception as exc:
        raise VisionError(
            code="FUSION_FAILED",
            message=f"Image/ToF fusion failed: {exc}",
            http_status=500,
            retryable=True,
            capture_id=payload.capture_id,
        ) from exc
    fusion_ms = int((time.perf_counter() - t2) * 1000)

    return build_success_response(
        capture_id=payload.capture_id,
        image_timestamp_ms=payload.image_timestamp_ms,
        tof_timestamp_ms=payload.tof_timestamp_ms,
        offset_ms=offset,
        max_offset_ms=max_offset,
        detections=interpreted,
        decode_ms=decode_ms,
        yolo_ms=yolo_ms,
        fusion_ms=fusion_ms,
        total_ms=int((time.perf_counter() - total_start) * 1000),
        person_name=config["model"].get("normal_person_class_name", "person"),
        zxl_name=config["model"].get("zxl_class_name", "zxl"),
    )
