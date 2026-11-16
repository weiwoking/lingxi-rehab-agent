from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class Settings:
    service_name: str
    provider_name: str
    provider_version: str
    default_voice: str
    default_rate: str
    default_pitch: str
    cache_dir: Path
    log_dir: Path
    cache_ttl_seconds: int
    cache_max_files: int
    cache_max_bytes: int

    @classmethod
    def from_environment(cls) -> "Settings":
        runtime_root = Path(
            os.environ.get("P4_REHAB_TTS_DATA_DIR", Path(__file__).resolve().parents[3] / "external" / "P4RehabTTS")
        )
        return cls(
            service_name="p4-rehab-tts",
            provider_name="edge-tts",
            provider_version="edge-tts-7.2.8/config-v1",
            default_voice=os.environ.get(
                "P4_REHAB_TTS_VOICE", "zh-CN-XiaoxiaoNeural"
            ),
            default_rate=os.environ.get("P4_REHAB_TTS_RATE", "+0%"),
            default_pitch=os.environ.get("P4_REHAB_TTS_PITCH", "+0Hz"),
            cache_dir=runtime_root / "cache",
            log_dir=runtime_root / "logs",
            cache_ttl_seconds=_positive_int(
                "P4_REHAB_TTS_CACHE_TTL_SECONDS", 7 * 24 * 60 * 60
            ),
            cache_max_files=_positive_int("P4_REHAB_TTS_CACHE_MAX_FILES", 256),
            cache_max_bytes=_positive_int(
                "P4_REHAB_TTS_CACHE_MAX_BYTES", 256 * 1024 * 1024
            ),
        )
