from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable


@dataclass(frozen=True)
class CacheEntry:
    audio_id: str
    path: Path
    cache_hit: bool
    provider_ms: int


class AudioCache:
    def __init__(
        self,
        root: Path,
        *,
        ttl_seconds: int,
        max_files: int,
        max_bytes: int,
    ) -> None:
        self.root = root
        self.ttl_seconds = ttl_seconds
        self.max_files = max_files
        self.max_bytes = max_bytes
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    @staticmethod
    def make_key(
        text: str, voice: str, rate: str, pitch: str, provider_version: str
    ) -> str:
        payload = json.dumps(
            {
                "text": text,
                "voice": voice,
                "rate": rate,
                "pitch": pitch,
                "provider_version": provider_version,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def path_for(self, audio_id: str) -> Path:
        if (
            len(audio_id) != 64
            or any(char not in "0123456789abcdef" for char in audio_id)
        ):
            raise ValueError("invalid audio id")
        return self.root / f"{audio_id}.mp3"

    def prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for temp in self.root.glob("*.tmp"):
            try:
                temp.unlink()
            except OSError:
                pass
        self.prune()

    def prune(self) -> None:
        now = time.time()
        entries: list[tuple[Path, os.stat_result]] = []
        for path in self.root.glob("*.mp3"):
            try:
                stat = path.stat()
            except OSError:
                continue
            if now - stat.st_mtime > self.ttl_seconds:
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            entries.append((path, stat))

        entries.sort(key=lambda item: item[1].st_mtime, reverse=True)
        total = 0
        for index, (path, stat) in enumerate(entries):
            total += stat.st_size
            if index >= self.max_files or total > self.max_bytes:
                try:
                    path.unlink()
                except OSError:
                    pass

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(key, asyncio.Lock())

    async def get_or_create(
        self,
        key: str,
        producer: Callable[[Path], Awaitable[int]],
    ) -> CacheEntry:
        target = self.path_for(key)
        if target.is_file() and target.stat().st_size > 0:
            os.utime(target, None)
            return CacheEntry(key, target, True, 0)

        lock = await self._lock_for(key)
        async with lock:
            if target.is_file() and target.stat().st_size > 0:
                os.utime(target, None)
                return CacheEntry(key, target, True, 0)

            self.root.mkdir(parents=True, exist_ok=True)
            temp = self.root / f".{key}.{uuid.uuid4().hex}.tmp"
            provider_ms = 0
            try:
                provider_ms = await producer(temp)
                if not temp.is_file() or temp.stat().st_size == 0:
                    raise RuntimeError("provider returned empty audio")
                os.replace(temp, target)
            finally:
                try:
                    temp.unlink()
                except FileNotFoundError:
                    pass
            self.prune()
            return CacheEntry(key, target, False, provider_ms)
