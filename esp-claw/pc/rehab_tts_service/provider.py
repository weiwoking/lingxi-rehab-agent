from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class ProviderError(RuntimeError):
    """Stable provider failure exposed by the service."""


class TTSProvider(Protocol):
    name: str

    async def validate_voice(self, voice: str) -> None: ...

    async def synthesize(
        self, text: str, voice: str, rate: str, pitch: str, destination: Path
    ) -> None: ...


@dataclass
class EdgeTTSProvider:
    name: str = "edge-tts"

    @staticmethod
    def _module():
        try:
            import edge_tts
        except ImportError as exc:
            raise ProviderError("edge-tts dependency is not installed") from exc
        return edge_tts

    async def validate_voice(self, voice: str) -> None:
        edge_tts = self._module()
        try:
            voices = await edge_tts.list_voices()
        except Exception as exc:
            raise ProviderError("edge-tts voice validation failed") from exc
        if not any(item.get("ShortName") == voice for item in voices):
            raise ProviderError(f"configured voice is unavailable: {voice}")

    async def synthesize(
        self, text: str, voice: str, rate: str, pitch: str, destination: Path
    ) -> None:
        edge_tts = self._module()
        try:
            communicate = edge_tts.Communicate(
                text=text, voice=voice, rate=rate, pitch=pitch
            )
            await communicate.save(str(destination))
        except Exception as exc:
            raise ProviderError("edge-tts synthesis failed") from exc
        if not destination.is_file() or destination.stat().st_size == 0:
            raise ProviderError("edge-tts returned empty audio")
        with destination.open("rb") as stream:
            header = stream.read(3)
        is_id3 = header == b"ID3"
        is_mpeg_frame = (
            len(header) >= 2
            and header[0] == 0xFF
            and (header[1] & 0xE0) == 0xE0
        )
        if not (is_id3 or is_mpeg_frame):
            raise ProviderError("edge-tts returned non-MP3 audio")
