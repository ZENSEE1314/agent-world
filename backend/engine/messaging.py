"""Agent-to-agent message bus. Messages are tiny; agents poll their inbox each tick."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Deque, Dict, List


class MessageBus:
    def __init__(self, capacity: int = 50) -> None:
        self._inbox: Dict[str, Deque[dict]] = defaultdict(lambda: deque(maxlen=capacity))
        self._all: Deque[dict] = deque(maxlen=200)
        self._lock = Lock()

    def send(self, src: str, dst: str, text: str, kind: str = "chat", **extra) -> dict:
        msg = {
            "ts": time.time(),
            "from": src,
            "to": dst,
            "kind": kind,
            "text": text,
            **extra,
        }
        with self._lock:
            self._inbox[dst].append(msg)
            self._all.append(msg)
        return msg

    def broadcast(self, src: str, text: str, kind: str = "broadcast", **extra) -> dict:
        msg = {
            "ts": time.time(),
            "from": src,
            "to": "*",
            "kind": kind,
            "text": text,
            **extra,
        }
        with self._lock:
            self._all.append(msg)
            for dst_id in list(self._inbox.keys()):
                if dst_id != src:
                    self._inbox[dst_id].append(msg)
        return msg

    def ensure_inbox(self, agent_id: str) -> None:
        with self._lock:
            _ = self._inbox[agent_id]  # touch to create

    def drain(self, agent_id: str, max_n: int = 10) -> List[dict]:
        with self._lock:
            box = self._inbox[agent_id]
            out = []
            while box and len(out) < max_n:
                out.append(box.popleft())
            return out

    def recent(self, n: int = 50) -> List[dict]:
        with self._lock:
            return list(self._all)[-n:]

    def forget(self, agent_id: str) -> None:
        with self._lock:
            self._inbox.pop(agent_id, None)
