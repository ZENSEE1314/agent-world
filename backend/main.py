"""FastAPI entry — mounts API, WebSocket, and the static God View UI."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from .api.routes import router as api_router
from .api.ws import MANAGER, router as ws_router
from .engine.config import CFG
from .engine.world import WORLD


BASE = Path(__file__).resolve().parent.parent
FRONTEND = BASE / "frontend"


app = FastAPI(title="Agent World", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(ws_router)

if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/")
    def root() -> FileResponse:
        return FileResponse(FRONTEND / "index.html")


@app.on_event("startup")
async def _startup() -> None:
    loop = asyncio.get_running_loop()
    MANAGER.set_loop(loop)
    # Wire engine events → WS
    WORLD.on_event(MANAGER.broadcast)
    # Kick off the tick loop
    asyncio.create_task(WORLD.run_forever())
    # Ensure directories exist
    for d in (CFG.projects_dir, CFG.memory_dir, CFG.logs_dir):
        Path(d).mkdir(parents=True, exist_ok=True)
