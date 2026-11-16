from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.detector_registry import DetectorRegistry, WarmupSettings
from app.model_adapter import PredictionOutput
from tests.person_follow_helpers import FakeDetector, service


WARM = WarmupSettings(enabled=True, image_size=640, device="cpu", half=False)


class CountingDetector(FakeDetector):
    def __init__(self, warm_error=None):
        super().__init__()
        self.ready = False
        self.load_count = 0
        self.warm_count = 0
        self.close_count = 0
        self.warm_error = warm_error

    def load(self):
        self.load_count += 1
        self.ready = True

    def predict_detailed(self, *_args, **_kwargs):
        self.warm_count += 1
        if self.warm_error:
            raise self.warm_error
        return PredictionOutput([], {})

    def close(self):
        self.close_count += 1


def test_same_artifact_loads_once_and_aliases_same_instance(tmp_path: Path):
    registry = DetectorRegistry()
    created = []

    def factory():
        detector = CountingDetector()
        created.append(detector)
        return detector

    artifact = tmp_path / "model.pt"
    fusion = registry.register("fusion", artifact, factory, WARM)
    person = registry.register("person_follow", artifact, factory, WARM)
    registry.load_all()
    assert len(created) == 1
    assert registry.unique_count == 1
    assert fusion is person
    assert fusion.load_count == 1


def test_shared_alias_warms_once_and_shutdown_releases_once(tmp_path: Path):
    registry = DetectorRegistry()
    detector = CountingDetector()
    artifact = tmp_path / "model.pt"
    registry.register("fusion", artifact, lambda: detector, WARM)
    registry.register("person_follow", artifact, lambda: pytest.fail("duplicate factory"), WARM)
    registry.load_all()
    registry.warmup_all()
    registry.warmup_all()
    assert detector.warm_count == 1
    assert registry.is_warmed("fusion")
    assert registry.is_warmed("person_follow")
    registry.close_all()
    registry.close_all()
    assert detector.close_count == 1


def test_warm_failure_keeps_person_follow_not_ready(tmp_path: Path):
    registry = DetectorRegistry()
    detector = CountingDetector(RuntimeError("warm boom"))
    registry.register("fusion", tmp_path / "model.pt", lambda: detector, WARM)
    registry.register("person_follow", tmp_path / "model.pt", lambda: detector, WARM)
    person_service = service(detector)
    person_service.set_ready(False)
    registry.load_all()
    with pytest.raises(RuntimeError, match="warm boom"):
        registry.warmup_all()
    assert not registry.is_warmed("person_follow")
    assert not person_service.ready


def test_lifespan_marks_ready_only_after_one_load_and_warm(tmp_path: Path):
    registry = DetectorRegistry()
    detector = CountingDetector()
    registry.register("fusion", tmp_path / "model.pt", lambda: detector, WARM)
    registry.register("person_follow", tmp_path / "model.pt", lambda: detector, WARM)
    person_service = service(detector)
    person_service.set_ready(False)

    @asynccontextmanager
    async def lifespan(_app):
        registry.load_all()
        registry.warmup_all()
        person_service.set_ready(registry.is_warmed("person_follow"))
        try:
            yield
        finally:
            person_service.set_ready(False)
            registry.close_all()

    app = FastAPI(lifespan=lifespan)
    app.get("/ready")(lambda: {"ready": person_service.ready})
    with TestClient(app) as test_client:
        assert test_client.get("/ready").json() == {"ready": True}
        assert detector.load_count == 1
        assert detector.warm_count == 1
    assert person_service.ready is False
    assert detector.close_count == 1
