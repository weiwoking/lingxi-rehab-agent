from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from . import __version__
from .cache import AudioCache
from .config import Settings
from .provider import EdgeTTSProvider, ProviderError, TTSProvider
from .validation import TextValidationError, validate_text

MIME_TYPE = "audio/mpeg"


class SynthesizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    text: str
    voice: str | None = None
    rate: str | None = None
    pitch: str | None = None


@dataclass
class Runtime:
    settings: Settings
    provider: TTSProvider
    cache: AudioCache
    provider_status: str = "pending"
    provider_error: str = ""


def _safe_request_id(value: str) -> str:
    return value[:96]


def _exception_message(exc: BaseException) -> str:
    message = str(exc).strip()
    return message or type(exc).__name__


def _error(status: int, category: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"ok": False, "error": category, "message": message},
    )


def _configure_logging(settings: Settings) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = settings.log_dir / "service.log"
    root = logging.getLogger("p4_rehab_tts")
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)


async def _validate_provider(runtime: Runtime) -> None:
    try:
        await asyncio.wait_for(
            runtime.provider.validate_voice(runtime.settings.default_voice),
            timeout=10.0,
        )
    except Exception as exc:
        runtime.provider_status = "error"
        runtime.provider_error = _exception_message(exc)
        logging.getLogger("p4_rehab_tts").error(
            "provider validation failed provider=%s reason=%s",
            runtime.provider.name,
            type(exc).__name__,
        )
    else:
        runtime.provider_status = "ready"
        runtime.provider_error = ""


def create_app(
    *,
    settings: Settings | None = None,
    provider: TTSProvider | None = None,
    validate_provider_on_startup: bool = True,
) -> FastAPI:
    active_settings = settings or Settings.from_environment()
    runtime = Runtime(
        settings=active_settings,
        provider=provider or EdgeTTSProvider(),
        cache=AudioCache(
            active_settings.cache_dir,
            ttl_seconds=active_settings.cache_ttl_seconds,
            max_files=active_settings.cache_max_files,
            max_bytes=active_settings.cache_max_bytes,
        ),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        _configure_logging(active_settings)
        runtime.cache.prepare()
        if validate_provider_on_startup:
            await _validate_provider(runtime)
        else:
            runtime.provider_status = "ready"
        yield

    api = FastAPI(
        title="P4 Rehab TTS",
        version=__version__,
        lifespan=lifespan,
    )
    api.state.runtime = runtime

    @api.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "error": "http_error", "message": str(exc.detail)},
        )

    @api.get("/health")
    async def health() -> dict[str, Any]:
        status = "ok" if runtime.provider_status == "ready" else "degraded"
        return {
            "service": active_settings.service_name,
            "status": status,
            "provider": runtime.provider.name,
            "provider_status": runtime.provider_status,
            "provider_error": runtime.provider_error,
            "version": __version__,
            "voice": active_settings.default_voice,
            "cache_dir": str(active_settings.cache_dir),
            "log_dir": str(active_settings.log_dir),
        }

    @api.post("/api/v1/tts/synthesize")
    async def synthesize(payload: SynthesizeRequest, request: Request):
        started = time.perf_counter()
        text = payload.text
        try:
            codepoints = validate_text(text)
        except TextValidationError as exc:
            raise _error(422, exc.category, str(exc)) from exc

        voice = payload.voice or active_settings.default_voice
        rate = payload.rate or active_settings.default_rate
        pitch = payload.pitch or active_settings.default_pitch
        key = runtime.cache.make_key(
            text, voice, rate, pitch, active_settings.provider_version
        )
        logger = logging.getLogger("p4_rehab_tts")
        request_id = _safe_request_id(payload.request_id)
        logger.info(
            "request accepted request_id=%s codepoints=%d text_hash=%s",
            request_id,
            codepoints,
            hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        )

        async def produce(destination):
            provider_started = time.perf_counter()
            logger.info("provider start request_id=%s cache_key=%s", request_id, key[:12])
            try:
                await runtime.provider.synthesize(
                    text, voice, rate, pitch, destination
                )
            except ProviderError as exc:
                runtime.provider_status = "error"
                runtime.provider_error = _exception_message(exc)
                raise
            except Exception as exc:
                runtime.provider_status = "error"
                runtime.provider_error = _exception_message(exc)
                raise ProviderError("TTS provider failed") from exc
            runtime.provider_status = "ready"
            runtime.provider_error = ""
            provider_ms = int((time.perf_counter() - provider_started) * 1000)
            logger.info(
                "provider end request_id=%s cache_key=%s provider_ms=%d",
                request_id,
                key[:12],
                provider_ms,
            )
            return provider_ms

        try:
            entry = await runtime.cache.get_or_create(key, produce)
        except ProviderError as exc:
            logger.warning(
                "provider failure request_id=%s cache_key=%s", request_id, key[:12]
            )
            raise _error(502, "provider_failed", _exception_message(exc)) from exc
        except Exception as exc:
            logger.exception(
                "cache failure request_id=%s cache_key=%s", request_id, key[:12]
            )
            raise _error(500, "cache_failed", "audio cache operation failed") from exc

        base_url = str(request.base_url).rstrip("/")
        total_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "audio ready request_id=%s cache_key=%s cache_hit=%s total_ms=%d",
            request_id,
            key[:12],
            entry.cache_hit,
            total_ms,
        )
        return {
            "ok": True,
            "request_id": payload.request_id,
            "audio_id": entry.audio_id,
            # The P4 simple player selects the decoder from the URI suffix.
            "audio_url": (
                f"{base_url}/api/v1/tts/audio/{entry.audio_id}.mp3"
            ),
            "mime_type": MIME_TYPE,
            "cache_hit": entry.cache_hit,
            "text_codepoints": codepoints,
            "provider_ms": entry.provider_ms,
            "total_ms": total_ms,
        }

    @api.get("/api/v1/tts/audio/{audio_id}")
    async def audio(audio_id: str):
        if audio_id.endswith(".mp3"):
            audio_id = audio_id[:-4]
        try:
            path = runtime.cache.path_for(audio_id)
        except ValueError as exc:
            raise _error(404, "audio_not_found", "audio was not found") from exc
        if not path.is_file() or path.stat().st_size == 0:
            raise _error(404, "audio_not_found", "audio was not found")
        return FileResponse(
            path,
            media_type=MIME_TYPE,
            filename=f"{audio_id}.mp3",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    return api


app = create_app()
