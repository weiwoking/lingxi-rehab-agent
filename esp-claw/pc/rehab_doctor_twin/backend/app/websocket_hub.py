from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect


class WebSocketHub:
    def __init__(self, queue_size: int = 64) -> None:
        self._clients: dict[WebSocket, asyncio.Queue[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()
        self._queue_size = queue_size

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, websocket: WebSocket, snapshot_message: dict[str, Any]) -> None:
        await websocket.accept()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._clients[websocket] = queue
        await websocket.send_json(snapshot_message)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(websocket, None)

    async def broadcast(self, message: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients.items())
        stale: list[WebSocket] = []
        for websocket, queue in clients:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with suppress(asyncio.QueueFull):
                    queue.put_nowait(message)
            except RuntimeError:
                stale.append(websocket)
        for websocket in stale:
            await self.disconnect(websocket)

    async def pump(self, websocket: WebSocket) -> None:
        queue = self._clients[websocket]
        try:
            while True:
                message = await queue.get()
                await websocket.send_json(message)
        except (WebSocketDisconnect, RuntimeError):
            await self.disconnect(websocket)


hub = WebSocketHub()

