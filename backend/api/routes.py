"""REST endpoints for God View."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..engine.logger import recent, recent_for
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


@router.get("/healthz")
def healthz():
    return {"ok": True, "tick": WORLD.tick_no, "agents": len(WORLD.agents)}
