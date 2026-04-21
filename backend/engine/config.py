"""Simulation constants. Env-overridable at process start."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Config:
    # Tick cadence
    tick_seconds: float = _float("TICK_SECONDS", 3.0)
    # Speed multiplier: 1.0 = spec (hunger -1%/5min real), 60.0 = 60x faster
    sim_speed: float = _float("SIM_SPEED", 60.0)

    # Agent defaults
    max_health: int = 100
    max_hunger: int = 100

    # Economy
    starting_cash: int = 1000
    food_cost: int = 300
    food_restores_hunger: int = 80  # one meal fills ~80%
    real_to_game_multiplier: int = 10  # $1 real → $10 in-world

    # Brain
    brain: str = os.getenv("BRAIN", "mock").strip().lower()
    anthropic_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    openai_key: str = os.getenv("OPENAI_API_KEY", "")
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "gemma2:2b")

    # Paths
    projects_dir: str = os.getenv("PROJECTS_DIR", "projects")
    memory_dir: str = os.getenv("MEMORY_DIR", "memory")
    logs_dir: str = os.getenv("LOGS_DIR", "logs")


CFG = Config()


def effective_tick_hunger_drop() -> float:
    """
    Spec: hunger drops 1% per 5 minutes of real time = 1/300 per second.
    Applied per tick, scaled by sim_speed.
    """
    per_second = 1.0 / 300.0
    return per_second * CFG.tick_seconds * CFG.sim_speed


def effective_tick_health_drop() -> float:
    """Spec: at 0 hunger, health drops 1 HP per hour = 1/3600 per second."""
    per_second = 1.0 / 3600.0
    return per_second * CFG.tick_seconds * CFG.sim_speed
