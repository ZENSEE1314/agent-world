"""LLM adapters. The default is a rule-based Mock brain so the sim runs with
no API keys. Set BRAIN=anthropic|openai|ollama + matching keys for real LLMs."""
from __future__ import annotations

import json
import os
import random
from typing import Any, Dict, List, Optional

import httpx

from .config import CFG


# ------------------------------------------------------------------ helpers

VALID_ACTIONS = {
    "work", "eat", "ask_mentor", "teach", "chat", "broadcast",
    "build_project", "idle", "gift",
}


def _safe_parse(raw: str) -> Optional[dict]:
    """Accept either strict JSON or JSON embedded in text."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    l = raw.find("{")
    r = raw.rfind("}")
    if 0 <= l < r:
        try:
            return json.loads(raw[l:r + 1])
        except json.JSONDecodeError:
            return None
    return None


def _coerce_decision(parsed: Optional[dict], fallback: dict) -> dict:
    if not isinstance(parsed, dict):
        return fallback
    action = parsed.get("action")
    if action not in VALID_ACTIONS:
        return fallback
    out = {"action": action, "reason": str(parsed.get("reason", ""))[:240]}
    for k in ("job", "target", "text", "amount", "topic"):
        if k in parsed:
            out[k] = parsed[k]
    return out


# ------------------------------------------------------------------ prompt

SYSTEM_PROMPT = """You are an autonomous survival agent in a 2D simulated world.
You must stay alive (hunger > 0, health > 0) AND hit KPIs (earn in-world cash by producing real-world value via your jobs).

HARD RULES:
- Food costs $300 and restores hunger. If hunger < 30, prioritize eating.
- If cash < $300 and hunger < 40, prioritize WORK (pick a safer job like data_labeling).
- Mentorship from Hermes (the Coder) boosts your job multiplier for a few ticks.
- You can chat with other agents, broadcast, or gift cash to teammates.
- Build projects (saved to disk) when you have spare cycles — they count toward legacy.

RESPOND WITH STRICT JSON ONLY:
{"action": "<one of: work|eat|ask_mentor|teach|chat|broadcast|build_project|idle|gift>",
 "job": "<job_id if action=work>",
 "target": "<agent_id if action in [chat, gift, teach, ask_mentor]>",
 "text": "<message text if chat/broadcast>",
 "topic": "<topic if build_project>",
 "amount": <int if gift>,
 "reason": "<short why>"}
"""


def build_user_prompt(ctx: dict) -> str:
    return (
        f"You are {ctx['name']} ({ctx['id']}), personality: {ctx['personality']}.\n"
        f"Stats — health: {ctx['health']}/100, hunger: {ctx['hunger']}/100, cash: ${ctx['cash']}.\n"
        f"Real-world $ produced so far: ${ctx['real_usd']}.\n"
        f"Skill multiplier: {ctx['skill_multiplier']:.2f} "
        f"(boosted by mentorship from Hermes).\n"
        f"Other agents alive: {', '.join(ctx['peers']) or 'none'}.\n"
        f"Unread messages ({len(ctx['inbox'])}):\n"
        + ("\n".join(f"  - {m['from']}: {m['text']}" for m in ctx['inbox'][:5]) or "  (empty)")
        + f"\n\nAvailable jobs: {', '.join(ctx['jobs'])}.\n"
        f"Recent actions you took: {', '.join(ctx['recent_actions'][-5:]) or '(none)'}.\n\n"
        "Choose ONE action. Return JSON only."
    )


# ------------------------------------------------------------------ mock brain

def _mock_decide(ctx: dict) -> dict:
    """Rule-based fallback that behaves like a reasonable agent."""
    hunger = ctx["hunger"]
    health = ctx["health"]
    cash = ctx["cash"]
    role = ctx.get("role", "worker")

    # urgent: hunger danger
    if hunger < 30 and cash >= 300:
        return {"action": "eat", "reason": "hunger critical"}

    # urgent: no cash + hungry → work safe job
    if cash < 300 and hunger < 50:
        return {"action": "work", "job": "data_labeling",
                "reason": "need cash for food, picking safe job"}

    # inbox-driven: help a peer who asked
    for msg in ctx.get("inbox", []):
        if msg.get("kind") == "knowledge_request" and role == "mentor":
            return {"action": "teach", "target": msg["from"],
                    "reason": f"answering knowledge request from {msg['from']}"}

    # mentor role: occasionally broadcast tips
    if role == "mentor" and random.random() < 0.15:
        tips = [
            "Tip: diversify — don't put every tick on build_micro_saas.",
            "Tip: data_labeling is boring but it pays the rent.",
            "Ping me (ask_mentor) and I'll boost your multiplier.",
        ]
        return {"action": "broadcast", "text": random.choice(tips),
                "reason": "sharing wisdom"}

    # ask for mentorship if struggling and not already boosted
    if ctx.get("skill_multiplier", 1.0) <= 1.0 and random.random() < 0.12:
        return {"action": "ask_mentor", "target": "hermes",
                "topic": random.choice(["crypto_trade", "freelance_code", "defi_arbitrage"]),
                "reason": "could use a boost"}

    # occasional social
    if ctx.get("peers") and random.random() < 0.08:
        return {"action": "chat", "target": random.choice(ctx["peers"]),
                "text": random.choice([
                    "How's your grind going?",
                    "I'm thinking about teaming up.",
                    "Got a tip on a decent job?",
                ]), "reason": "small talk"}

    # occasional project
    if random.random() < 0.05:
        topics = ["daily-log", "trading-notes", "micro-saas-idea",
                  "hunger-strategy", "bug-triage"]
        return {"action": "build_project", "topic": random.choice(topics),
                "reason": "spare cycles — shipping something"}

    # default: work
    # pick by hunger/cash state
    if cash > 1500:
        job = random.choice(["defi_arbitrage", "freelance_code", "build_micro_saas",
                             "crypto_trade", "content_writing"])
    else:
        job = random.choice(["data_labeling", "content_writing", "crypto_trade",
                             "freelance_code"])
    return {"action": "work", "job": job, "reason": "steady earning"}


# ------------------------------------------------------------------ LLM adapters

def _call_anthropic(ctx: dict) -> Optional[dict]:
    if not CFG.anthropic_key:
        return None
    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CFG.anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 256,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": build_user_prompt(ctx)}],
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(
            block.get("text", "") for block in data.get("content", [])
            if block.get("type") == "text"
        )
        return _safe_parse(text)
    except (httpx.HTTPError, json.JSONDecodeError, KeyError):
        return None


def _call_openai(ctx: dict) -> Optional[dict]:
    if not CFG.openai_key:
        return None
    try:
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {CFG.openai_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_prompt(ctx)},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 256,
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        return _safe_parse(text)
    except (httpx.HTTPError, KeyError, json.JSONDecodeError):
        return None


def _call_ollama(ctx: dict) -> Optional[dict]:
    try:
        resp = httpx.post(
            f"{CFG.ollama_host.rstrip('/')}/api/chat",
            json={
                "model": CFG.ollama_model,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_prompt(ctx)},
                ],
            },
            timeout=25.0,
        )
        resp.raise_for_status()
        text = resp.json().get("message", {}).get("content", "")
        return _safe_parse(text)
    except (httpx.HTTPError, KeyError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------ public API

def decide(ctx: dict) -> dict:
    """Returns a decision dict. Always returns SOMETHING — never raises."""
    fallback = _mock_decide(ctx)
    brain = CFG.brain
    if brain == "mock":
        return fallback
    parsed: Optional[dict] = None
    if brain == "anthropic":
        parsed = _call_anthropic(ctx)
    elif brain == "openai":
        parsed = _call_openai(ctx)
    elif brain == "ollama":
        parsed = _call_ollama(ctx)
    return _coerce_decision(parsed, fallback)
