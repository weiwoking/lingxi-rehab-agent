import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np


logger = logging.getLogger("vision-interpreter.detectors")


@dataclass(frozen=True)
class WarmupSettings:
    enabled: bool
    image_size: int
    device: str
    half: bool


@dataclass
class _Entry:
    artifact_key: str
    detector: Any
    warmup: WarmupSettings
    load_attempted: bool = False
    warmed: bool = False
    warmup_ms: float | None = None
    warmup_error: str | None = None
    closed: bool = False


def artifact_key(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve()).casefold()


class DetectorRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._slots: dict[str, _Entry] = {}

    def register(
        self,
        logical_slot: str,
        artifact: str | Path,
        factory: Callable[[], Any],
        warmup: WarmupSettings,
    ) -> Any:
        if logical_slot in self._slots:
            raise ValueError(f"Detector slot already registered: {logical_slot}")
        key = artifact_key(artifact)
        entry = self._entries.get(key)
        if entry is None:
            entry = _Entry(key, factory(), warmup)
            self._entries[key] = entry
        elif warmup.enabled and not entry.warmup.enabled:
            entry.warmup = warmup
        self._slots[logical_slot] = entry
        return entry.detector

    def get(self, logical_slot: str) -> Any:
        return self._slots[logical_slot].detector

    def aliases_same_instance(self, left: str, right: str) -> bool:
        return self.get(left) is self.get(right)

    @property
    def unique_count(self) -> int:
        return len(self._entries)

    def load_all(self) -> None:
        for entry in self._entries.values():
            if entry.load_attempted:
                continue
            entry.load_attempted = True
            entry.detector.load()
            if not entry.detector.ready:
                raise RuntimeError(getattr(entry.detector, "load_error", None) or "Detector failed to load")

    def warmup_all(self) -> None:
        dummy = np.zeros((360, 640, 3), dtype=np.uint8)
        for entry in self._entries.values():
            if entry.warmed or not entry.warmup.enabled:
                continue
            started = time.perf_counter()
            try:
                entry.detector.predict_detailed(
                    dummy,
                    "__startup_warmup__",
                    image_size=entry.warmup.image_size,
                    device=entry.warmup.device,
                    half=entry.warmup.half,
                )
                entry.warmed = True
                entry.warmup_ms = (time.perf_counter() - started) * 1000.0
                logger.info("Detector warm-up complete artifact=%s elapsed_ms=%.3f", entry.artifact_key, entry.warmup_ms)
            except Exception as exc:
                entry.warmup_ms = (time.perf_counter() - started) * 1000.0
                entry.warmup_error = str(exc)
                logger.exception("Detector warm-up failed artifact=%s elapsed_ms=%.3f", entry.artifact_key, entry.warmup_ms)
                raise RuntimeError(f"Detector warm-up failed: {exc}") from exc

    def is_warmed(self, logical_slot: str) -> bool:
        entry = self._slots[logical_slot]
        return entry.warmed or not entry.warmup.enabled

    def close_all(self) -> None:
        for entry in self._entries.values():
            if entry.closed:
                continue
            close = getattr(entry.detector, "close", None)
            if callable(close):
                close()
            entry.closed = True
