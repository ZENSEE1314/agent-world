"""Append-only per-agent logs + in-memory ring buffer for UI."""
from __future__ import annotations

import os
import time
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Deque, Dict, List

from .config import CFG


_RING_SIZE = 400
_ring: Deque[dict] = deque(maxlen=_RING_SIZE)
_lock = Lock()
_fp_cache: Dict[str, "Path"] = {}


def _path_for(agent_id: str) -> Path:
    Path(CFG.logs_dir).mkdir(parents=True, exist_ok=True)
    return Path(CFG.logs_dir) / f"{agent_id}.log"


def log(agent_id: str, kind: str, message: str, **extra) -> dict:
    entry = {
        "ts": time.time(),
        "agent": agent_id,
        "kind": kind,
        "msg": message,
    }
    if extra:
        entry.update(extra)
    with _lock:
        _ring.append(entry)
    try:
        with _path_for(agent_id).open("a", encoding="utf-8") as fp:
            fp.write(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(entry['ts']))}] "
                f"{kind.upper():10s} {message}\n"
            )
    except OSError:
        pass  # logging must never crash the sim
    return entry


def recent(n: int = 100) -> List[dict]:
    with _lock:
        return list(_ring)[-n:]


def recent_for(agent_id: str, n: int = 50) -> List[dict]:
    with _lock:
        return [e for e in _ring if e["agent"] == agent_id][-n:]


def reset() -> None:
    with _lock:
        _ring.clear()
