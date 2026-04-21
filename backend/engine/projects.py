"""Project artifacts produced by agents — stored with review state so the user
can approve / reject each one. Approved artifacts are what the user actually
pastes into Upwork / Medium / Substack / Twitter."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional

from .config import CFG


KINDS = ["article_draft", "cold_email", "tweet_thread", "code_snippet",
         "product_brief", "note"]


@dataclass
class Project:
    id: str
    agent_id: str
    agent_name: str
    kind: str          # one of KINDS
    topic: str
    title: str
    body: str          # full markdown
    status: str        # pending | approved | rejected
    created_at: float
    reviewed_at: Optional[float] = None


def _store_dir() -> Path:
    p = Path(CFG.projects_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


class ProjectStore:
    def __init__(self) -> None:
        self._items: Dict[str, Project] = {}
        self._order: List[str] = []   # insertion order newest-last
        self._lock = Lock()
        self._counter = 0
        self._load()

    def _load(self) -> None:
        """Pick up anything already on disk from prior runs."""
        idx = _store_dir() / "_index.json"
        if not idx.exists():
            return
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
            for row in data.get("items", []):
                p = Project(**row)
                self._items[p.id] = p
                self._order.append(p.id)
            self._counter = data.get("counter", len(self._items))
        except (OSError, ValueError, TypeError):
            pass

    def _persist(self) -> None:
        try:
            data = {
                "counter": self._counter,
                "items": [asdict(p) for p in self._items.values()],
            }
            (_store_dir() / "_index.json").write_text(
                json.dumps(data, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    # ---- create ------------------------------------------------------------

    def create(self, agent_id: str, agent_name: str, kind: str, topic: str,
               title: str, body: str) -> Project:
        if kind not in KINDS:
            kind = "note"
        with self._lock:
            self._counter += 1
            pid = f"proj_{self._counter}"
            p = Project(
                id=pid, agent_id=agent_id, agent_name=agent_name,
                kind=kind, topic=topic, title=title, body=body,
                status="pending", created_at=time.time(),
            )
            self._items[pid] = p
            self._order.append(pid)
            # Also write a markdown file so the user can see it on disk
            safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in topic)[:48] or "topic"
            folder = _store_dir() / agent_id
            folder.mkdir(parents=True, exist_ok=True)
            md = (
                f"# {title}\n\n"
                f"_by {agent_name} ({agent_id}) — kind: {kind} — {time.strftime('%Y-%m-%d %H:%M:%S')}_\n\n"
                f"{body}\n"
            )
            try:
                (folder / f"{pid}-{safe}.md").write_text(md, encoding="utf-8")
            except OSError:
                pass
            self._persist()
        return p

    # ---- review ------------------------------------------------------------

    def review(self, pid: str, vote: str) -> Optional[Project]:
        vote = vote.lower()
        if vote not in ("approve", "reject"):
            return None
        with self._lock:
            p = self._items.get(pid)
            if p is None:
                return None
            p.status = "approved" if vote == "approve" else "rejected"
            p.reviewed_at = time.time()
            self._persist()
        return p

    # ---- views -------------------------------------------------------------

    def get(self, pid: str) -> Optional[Project]:
        with self._lock:
            return self._items.get(pid)

    def list_pending(self, limit: int = 20) -> List[Project]:
        with self._lock:
            pending = [self._items[i] for i in self._order if self._items[i].status == "pending"]
            pending.reverse()  # newest first
            return pending[:limit]

    def list_recent(self, limit: int = 40) -> List[Project]:
        with self._lock:
            items = [self._items[i] for i in self._order][-limit:]
            items.reverse()
            return items

    def stats_for(self, agent_id: str) -> dict:
        with self._lock:
            mine = [p for p in self._items.values() if p.agent_id == agent_id]
            return {
                "projects_total": len(mine),
                "approved": sum(1 for p in mine if p.status == "approved"),
                "rejected": sum(1 for p in mine if p.status == "rejected"),
                "pending": sum(1 for p in mine if p.status == "pending"),
            }


    def reset(self) -> None:
        """Wipe the in-memory index AND the persisted _index.json on disk."""
        with self._lock:
            self._items.clear()
            self._order.clear()
            self._counter = 0
            self._persist()


STORE = ProjectStore()
