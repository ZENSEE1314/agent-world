"""REST endpoints for God View."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..engine.logger import recent, recent_for
from ..engine.paper_trading import BOOK as PAPER_BOOK
from ..engine.projects import STORE as PROJECT_STORE
from ..engine.world import WORLD


router = APIRouter(prefix="/api")


class AddHouseIn(BaseModel):
    x: Optional[int] = Field(default=None, ge=0, le=1200)
    y: Optional[int] = Field(default=None, ge=0, le=800)
    color: Optional[str] = None
    style: Optional[str] = "cottage"


class AddAgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    personality: str = Field(default="", max_length=240)
    role: str = Field(default="worker")  # worker | mentor
    avatar: str = Field(default="🙂", max_length=4)
    house_id: Optional[str] = None


class DivineIn(BaseModel):
    text: str = Field(min_length=1, max_length=480)


@router.get("/state")
def get_state():
    return WORLD.snapshot()


@router.get("/agents/{agent_id}/logs")
def agent_logs(agent_id: str, n: int = 80):
    if agent_id not in WORLD.agents:
        raise HTTPException(404, "agent not found")
    return {"agent": agent_id, "logs": recent_for(agent_id, n=n)}


@router.get("/logs")
def world_logs(n: int = 120):
    return {"logs": recent(n=n)}


@router.post("/houses")
def add_house(body: AddHouseIn):
    h = WORLD.add_house(x=body.x, y=body.y,
                        color=body.color, style=body.style or "cottage")
    return {"ok": True, "house": h.__dict__}


@router.post("/agents")
def add_agent(body: AddAgentIn):
    if body.role not in ("worker", "mentor"):
        raise HTTPException(400, "role must be 'worker' or 'mentor'")
    a = WORLD.add_agent(name=body.name, personality=body.personality,
                        role=body.role, avatar=body.avatar,
                        house_id=body.house_id)
    return {"ok": True, "agent_id": a.id}


@router.post("/divine")
def divine(body: DivineIn):
    entry = WORLD.divine_command(body.text)
    return {"ok": True, "entry": entry}


@router.post("/reset")
def reset_world():
    return WORLD.reset_world()


@router.get("/healthz")
def healthz():
    return {"ok": True, "tick": WORLD.tick_no, "agents": len(WORLD.agents)}


# ---- paper trading ---------------------------------------------------------

@router.get("/paper")
def paper_book():
    return PAPER_BOOK.snapshot()


# ---- project review queue --------------------------------------------------

class ReviewIn(BaseModel):
    vote: str = Field(pattern=r"^(approve|reject)$")


@router.get("/projects")
def list_projects(status: Optional[str] = None, limit: int = 40):
    if status == "pending":
        items = PROJECT_STORE.list_pending(limit=limit)
    else:
        items = PROJECT_STORE.list_recent(limit=limit)
        if status in ("approved", "rejected"):
            items = [p for p in items if p.status == status]
    return {"items": [
        {
            "id": p.id, "agent_id": p.agent_id, "agent_name": p.agent_name,
            "kind": p.kind, "topic": p.topic, "title": p.title,
            "body": p.body, "status": p.status,
            "created_at": p.created_at, "reviewed_at": p.reviewed_at,
        }
        for p in items
    ]}


@router.get("/projects/{pid}")
def get_project(pid: str):
    p = PROJECT_STORE.get(pid)
    if p is None:
        raise HTTPException(404, "project not found")
    return {
        "id": p.id, "agent_id": p.agent_id, "agent_name": p.agent_name,
        "kind": p.kind, "topic": p.topic, "title": p.title,
        "body": p.body, "status": p.status,
        "created_at": p.created_at, "reviewed_at": p.reviewed_at,
    }


@router.post("/projects/{pid}/review")
def review_project(pid: str, body: ReviewIn):
    p = PROJECT_STORE.review(pid, body.vote)
    if p is None:
        raise HTTPException(404, "project not found")
    # Nudge the author's skill floor based on the verdict — they learn from YOUR taste.
    agent = WORLD.agents.get(p.agent_id)
    if agent is not None:
        if body.vote == "approve":
            agent.nudge_skill(+0.05)
        else:
            agent.nudge_skill(-0.03)
    return {"ok": True, "status": p.status}
