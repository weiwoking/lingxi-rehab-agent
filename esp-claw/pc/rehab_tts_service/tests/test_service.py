from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="FastAPI is installed by the launcher")
from fastapi.testclient import TestClient

from rehab_tts_service.app import create_app
from rehab_tts_service.cache import AudioCache
from rehab_tts_service.config import Settings
from rehab_tts_service.provider import ProviderError


class MockProvider:
    name = "mock-edge-tts"

    def __init__(self) -> None:
        self.calls = 0
        self.texts: list[str] = []

    async def validate_voice(self, voice: str) -> None:
        assert voice

    async def synthesize(
        self, text: str, voice: str, rate: str, pitch: str, destination: Path
    ) -> None:
        self.calls += 1
        self.texts.append(text)
        await asyncio.sleep(0.01)
        destination.write_bytes(b"ID3mock-mp3")


class RecoveryProvider(MockProvider):
    def __init__(
        self,
        *,
        validation_error: BaseException | None = None,
        synthesize_results: list[BaseException | None] | None = None,
    ) -> None:
        super().__init__()
        self.validation_error = validation_error
        self.synthesize_results = list(synthesize_results or [])

    async def validate_voice(self, voice: str) -> None:
        assert voice
        if self.validation_error is not None:
            raise self.validation_error

    async def synthesize(
        self, text: str, voice: str, rate: str, pitch: str, destination: Path
    ) -> None:
        self.calls += 1
        self.texts.append(text)
        result = self.synthesize_results.pop(0) if self.synthesize_results else None
        if result is not None:
            raise result
        destination.write_bytes(b"ID3recovered-mp3")


def settings(tmp_path: Path) -> Settings:
    return Settings(
        service_name="p4-rehab-tts",
        provider_name="edge-tts",
        provider_version="mock-v1",
        default_voice="zh-CN-XiaoxiaoNeural",
        default_rate="+0%",
        default_pitch="+0Hz",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        cache_ttl_seconds=3600,
        cache_max_files=8,
        cache_max_bytes=1024 * 1024,
    )


def make_client(tmp_path: Path):
    provider = MockProvider()
    app = create_app(
        settings=settings(tmp_path),
        provider=provider,
        validate_provider_on_startup=False,
    )
    return TestClient(app), provider


def payload(text: str, **overrides):
    result = {
        "request_id": "wechat-1",
        "text": text,
        "voice": None,
        "rate": None,
        "pitch": None,
    }
    result.update(overrides)
    return result


def recovery_app(tmp_path: Path, provider: RecoveryProvider):
    return create_app(
        settings=settings(tmp_path),
        provider=provider,
        validate_provider_on_startup=True,
    )


def test_health_identity(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["service"] == "p4-rehab-tts"
        assert response.json()["status"] == "ok"


def test_exact_text_cache_and_audio_mime(tmp_path):
    client, provider = make_client(tmp_path)
    text = "请在14点30分启动P4X和ESP32演示。"
    with client:
        first = client.post("/api/v1/tts/synthesize", json=payload(text))
        second = client.post("/api/v1/tts/synthesize", json=payload(text))
        assert first.status_code == second.status_code == 200
        assert first.json()["cache_hit"] is False
        assert second.json()["cache_hit"] is True
        assert first.json()["audio_id"] == second.json()["audio_id"]
        assert provider.calls == 1
        assert provider.texts == [text]
        audio = client.get(first.json()["audio_url"])
        assert audio.status_code == 200
        assert audio.headers["content-type"].startswith("audio/mpeg")


def test_validation_failure_allows_cached_audio_without_false_recovery(tmp_path):
    provider = RecoveryProvider(
        validation_error=ProviderError("temporary validation failure")
    )
    app = recovery_app(tmp_path, provider)
    text = "cached recovery prompt"

    with TestClient(app) as client:
        runtime = app.state.runtime
        key = runtime.cache.make_key(
            text,
            runtime.settings.default_voice,
            runtime.settings.default_rate,
            runtime.settings.default_pitch,
            runtime.settings.provider_version,
        )
        runtime.cache.path_for(key).write_bytes(b"ID3cached-mp3")

        response = client.post("/api/v1/tts/synthesize", json=payload(text))
        health = client.get("/health").json()

        assert response.status_code == 200
        assert response.json()["cache_hit"] is True
        assert provider.calls == 0
        assert health["status"] == "degraded"
        assert health["provider_status"] == "error"
        assert health["provider_error"] == "temporary validation failure"


def test_validation_failure_then_real_synthesis_recovers_health(tmp_path):
    provider = RecoveryProvider(
        validation_error=ProviderError("temporary validation failure")
    )
    app = recovery_app(tmp_path, provider)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/tts/synthesize", json=payload("uncached recovery prompt")
        )
        health = client.get("/health").json()

        assert response.status_code == 200
        assert response.json()["cache_hit"] is False
        assert provider.calls == 1
        assert health["status"] == "ok"
        assert health["provider_status"] == "ready"
        assert health["provider_error"] == ""


def test_synthesis_failure_does_not_lock_out_next_request(tmp_path):
    provider = RecoveryProvider(
        synthesize_results=[ProviderError("temporary synthesis failure"), None]
    )
    app = recovery_app(tmp_path, provider)

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/tts/synthesize", json=payload("first uncached prompt")
        )
        failed_health = client.get("/health").json()
        second = client.post(
            "/api/v1/tts/synthesize", json=payload("second uncached prompt")
        )
        recovered_health = client.get("/health").json()

        assert first.status_code == 502
        assert first.json()["error"] == "provider_failed"
        assert failed_health["status"] == "degraded"
        assert failed_health["provider_status"] == "error"
        assert failed_health["provider_error"] == "temporary synthesis failure"
        assert second.status_code == 200
        assert provider.calls == 2
        assert recovered_health["status"] == "ok"
        assert recovered_health["provider_status"] == "ready"
        assert recovered_health["provider_error"] == ""


def test_empty_validation_exception_has_nonempty_health_error(tmp_path):
    provider = RecoveryProvider(validation_error=TimeoutError())
    app = recovery_app(tmp_path, provider)

    with TestClient(app) as client:
        health = client.get("/health").json()

        assert health["status"] == "degraded"
        assert health["provider_status"] == "error"
        assert health["provider_error"] == "TimeoutError"


def test_cache_key_includes_voice_rate_and_pitch(tmp_path):
    client, provider = make_client(tmp_path)
    with client:
        responses = [
            client.post("/api/v1/tts/synthesize", json=payload("你好", voice="v1")),
            client.post("/api/v1/tts/synthesize", json=payload("你好", rate="+10%")),
            client.post("/api/v1/tts/synthesize", json=payload("你好", pitch="+2Hz")),
        ]
        assert all(item.status_code == 200 for item in responses)
        assert len({item.json()["audio_id"] for item in responses}) == 3
        assert provider.calls == 3


def test_text_boundaries_and_exact_preservation(tmp_path):
    client, provider = make_client(tmp_path)
    with client:
        assert client.post("/api/v1/tts/synthesize", json=payload("")).status_code == 422
        assert (
            client.post("/api/v1/tts/synthesize", json=payload(" \t\n")).status_code
            == 422
        )
        exact = "  中文 ESP32 P4X 14点30分  "
        assert (
            client.post("/api/v1/tts/synthesize", json=payload(exact)).status_code
            == 200
        )
        assert provider.texts[-1] == exact
        assert (
            client.post("/api/v1/tts/synthesize", json=payload("中" * 300)).status_code
            == 200
        )
        too_long = client.post(
            "/api/v1/tts/synthesize", json=payload("中" * 301)
        )
        assert too_long.status_code == 422
        assert too_long.json()["error"] == "text_too_long"
