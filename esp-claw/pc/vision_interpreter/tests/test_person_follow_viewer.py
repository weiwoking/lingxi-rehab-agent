from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.person_follow_router import create_person_follow_router
from app.person_follow_viewer import (
    LatestPersonFollowFrameStore,
    boundary_pixel_positions,
    create_person_follow_viewer_router,
    zone_for_center,
)
from tests.person_follow_helpers import FakeDetector, detection, jpeg, metadata_part, service


TIMING = {
    "decode": 1.0,
    "predict_wall": 2.0,
    "preprocess": 0.2,
    "inference": 1.4,
    "postprocess": 0.4,
    "normalize": 0.1,
    "total": 3.1,
}


def display_detection(center_x: float, center_y: float = 0.5) -> dict:
    return {
        "class_name": "person",
        "confidence": 0.86,
        "bbox_px": {"x1": 100.0, "y1": 50.0, "x2": 300.0, "y2": 310.0},
        "center_normalized": {"x": center_x, "y": center_y},
        "area_normalized": 0.2,
    }


def success(frame_id: str = "PF-1", detections: list[dict] | None = None) -> dict:
    items = detections or []
    return {
        "frame_id": frame_id,
        "target_found": bool(items),
        "detections": items,
        "processing_time_ms": dict(TIMING),
    }


def viewer_client(store: LatestPersonFollowFrameStore) -> TestClient:
    app = FastAPI()
    app.include_router(create_person_follow_viewer_router(store))
    return TestClient(app)


def test_main_openapi_contains_old_and_person_follow_routes():
    paths = set(main.app.openapi()["paths"])
    assert {
        "/api/v1/vision/health",
        "/api/v1/vision/fuse",
        "/api/v1/person-follow/detect",
        "/person-follow-view",
        "/api/v1/person-follow/latest/original.jpg",
        "/api/v1/person-follow/latest/annotated.jpg",
        "/api/v1/person-follow/latest.json",
    } <= paths


def test_empty_store_returns_404_but_view_page_is_available():
    client = viewer_client(LatestPersonFollowFrameStore())
    view = client.get("/person-follow-view")
    assert view.status_code == 200
    assert "setInterval(refresh, 400)" in view.text
    assert "/api/v1/person-follow/latest.json" in view.text
    assert "/api/v1/person-follow/latest/original.jpg" in view.text
    assert "/api/v1/person-follow/latest/annotated.jpg" in view.text
    for path in (
        "/api/v1/person-follow/latest/original.jpg",
        "/api/v1/person-follow/latest/annotated.jpg",
        "/api/v1/person-follow/latest.json",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert "No person-follow frame" in response.json()["detail"]


def test_single_frame_original_annotated_json_zone_and_timing():
    store = LatestPersonFollowFrameStore()
    original = jpeg()
    image = cv2.imdecode(np.frombuffer(original, np.uint8), cv2.IMREAD_COLOR)
    store.update(original, image, success("PF-single", [display_detection(0.2)]))
    client = viewer_client(store)

    original_response = client.get("/api/v1/person-follow/latest/original.jpg")
    annotated_response = client.get("/api/v1/person-follow/latest/annotated.jpg")
    metadata = client.get("/api/v1/person-follow/latest.json").json()
    assert original_response.status_code == annotated_response.status_code == 200
    assert original_response.headers["content-type"] == "image/jpeg"
    assert original_response.content == original
    annotated = cv2.imdecode(np.frombuffer(annotated_response.content, np.uint8), cv2.IMREAD_COLOR)
    assert annotated.shape == (360, 640, 3)
    assert annotated_response.content != original
    assert metadata["frame_id"] == "PF-single"
    assert metadata["target_found"] is True
    assert metadata["detection_count"] == 1
    assert metadata["detections"][0]["zone"] == "CENTER"
    assert metadata["detections"][0]["center_x"] == 0.2
    assert metadata["timing"] == TIMING
    assert metadata["updated_at"].endswith("Z")


def test_zone_boundaries_and_multiple_people():
    cases = (
        (0.0, "LEFT"),
        (0.1999, "LEFT"),
        (0.20, "CENTER"),
        (0.50, "CENTER"),
        (0.6070, "CENTER"),
        (0.80, "CENTER"),
        (0.8001, "RIGHT"),
        (0.88, "RIGHT"),
        (1.0, "RIGHT"),
    )
    for center_x, expected in cases:
        assert zone_for_center(center_x) == expected
    assert boundary_pixel_positions(640) == (128, 512)
    store = LatestPersonFollowFrameStore()
    original = jpeg()
    image = cv2.imdecode(np.frombuffer(original, np.uint8), cv2.IMREAD_COLOR)
    store.update(
        original,
        image,
        success("PF-multi", [display_detection(0.1), display_detection(0.5), display_detection(0.9)]),
    )
    metadata = store.get().metadata
    assert metadata["detection_count"] == 3
    assert [item["zone"] for item in metadata["detections"]] == ["LEFT", "CENTER", "RIGHT"]


def test_no_target_and_new_frame_overwrites_old_frame():
    store = LatestPersonFollowFrameStore()
    first = jpeg()
    first_image = cv2.imdecode(np.frombuffer(first, np.uint8), cv2.IMREAD_COLOR)
    store.update(first, first_image, success("PF-old", [display_detection(0.5)]))

    second_image = np.full((360, 640, 3), 40, dtype=np.uint8)
    ok, second_encoded = cv2.imencode(".jpg", second_image)
    assert ok
    second = second_encoded.tobytes()
    store.update(second, second_image, success("PF-new"))

    latest = store.get()
    assert latest.metadata["frame_id"] == "PF-new"
    assert latest.metadata["target_found"] is False
    assert latest.metadata["detection_count"] == 0
    assert latest.metadata["detections"] == []
    assert latest.original_jpeg_bytes == second
    annotated = cv2.imdecode(np.frombuffer(latest.annotated_jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
    assert np.count_nonzero(annotated[15:65, 15:240]) > 0


def test_concurrent_reads_and_writes_are_coherent():
    store = LatestPersonFollowFrameStore()
    original = jpeg()
    image = cv2.imdecode(np.frombuffer(original, np.uint8), cv2.IMREAD_COLOR)

    def write(index: int) -> None:
        detections = [] if index % 2 == 0 else [display_detection(0.5)]
        store.update(original, image, success(f"PF-{index}", detections))

    def read_many() -> None:
        for _ in range(100):
            frame = store.get()
            if frame is not None:
                metadata = frame.metadata
                assert metadata["target_found"] == bool(metadata["detections"])
                assert metadata["detection_count"] == len(metadata["detections"])

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(write, index) for index in range(20)]
        futures.extend(pool.submit(read_many) for _ in range(8))
        for future in futures:
            future.result()


def test_viewer_update_does_not_change_person_follow_response_contract():
    store = LatestPersonFollowFrameStore()
    instance = service(FakeDetector([detection()]))
    instance.latest_frame_sink = store.update
    app = FastAPI()
    app.include_router(create_person_follow_router(instance))
    response = TestClient(app).post(
        "/api/v1/person-follow/detect",
        files={"image": ("frame.jpg", jpeg(), "image/jpeg"), "metadata": metadata_part()},
    )
    body = response.json()
    assert response.status_code == 200
    assert set(body) == {
        "schema_version", "frame_id", "frame_generation", "capture_timestamp_us",
        "capture_clock_domain", "image_width", "image_height", "target_class",
        "target_found", "detections", "model", "processing_time_ms",
    }
    assert "zone" not in body["detections"][0]
    assert store.get().metadata["detections"][0]["zone"] == "CENTER"
