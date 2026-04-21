"""WebSocket fan-out — pushes each tick's snapshot to every connected client."""
from __future__ import annotations

import asyncio
import json
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..engine.world import WORLD


router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self.active: Set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)
        # Send current state immediately
        try:
            await ws.send_text(json.dumps({"type": "snapshot",
                                           "snapshot": WORLD.snapshot()}))
        except Exception:
            pass

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def _send_all(self, payload: str) -> None:
        dead = []
        for ws in list(self.active):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.discard(ws)

    def broadcast(self, evt: dict) -> None:
        """Called from sync engine callbacks — schedules a coroutine on the loop."""
        if self._loop is None or not self.active:
            return
        payload = json.dumps(evt, default=str)
        asyncio.run_coroutine_threadsafe(self._send_all(payload), self._loop)


MANAGER = ConnectionManager()


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await MANAGER.connect(ws)
    try:
        while True:
            # We don't read client messages for now (could add in future)
            await ws.receive_text()
    except WebSocketDisconnect:
        MANAGER.disconnect(ws)
    except Exception:
        MANAGER.disconnect(ws)
