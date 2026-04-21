"""LLM adapters. Default Mock brain runs with no keys; real brains are optional.

The Ollama adapter will lazily pull the requested model if it isn't loaded yet,
so the sim stays alive while the model downloads in the background."""
from __future__ import annotations

import json
import os
import random
import time
from threading import Lock
from typing import Optional

import httpx

from . import market
from .config import CFG


VALID_ACTIONS = {
    "work", "eat", "ask_mentor", "teach", "chat", "broadcast",
    "build_project", "idle", "gift", "reflect",
}


# ------------------------------------------------------------------ helpers

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
You must stay alive (hunger > 0, health > 0) AND grow your real-world $ by producing real value.

HARD RULES:
- Food costs $300 and restores hunger. If hunger < 30, prioritize eating.
- Crypto jobs (crypto_trade, defi_arbitrage, memecoin_sniping) can LOSE real money when the
  market moves against you. Losses hit your cash at the 10x multiplier, just like gains.
- Safe jobs: data_labeling (boring, reliable), content_writing, freelance_code.
- Use the LIVE market data in the user prompt — don't trade BTC long if 24h is -7%.
- Mentorship from Hermes boosts your job multiplier for a few ticks.
- You can chat, broadcast, gift cash, or build projects (saved to disk).
- You can also pick action "reflect" to write a lesson from your recent actions
  into your memory — useful when you've had several losses.

RESPOND WITH STRICT JSON ONLY:
{"action": "<work|eat|ask_mentor|teach|chat|broadcast|build_project|idle|gift|reflect>",
 "job": "<job_id if action=work>",
 "target": "<agent_id if action in [chat, gift, teach, ask_mentor]>",
 "text": "<message text if chat/broadcast>",
 "topic": "<topic if build_project or reflect>",
 "amount": <int if gift>,
 "reason": "<short why>"}
"""


def build_user_prompt(ctx: dict) -> str:
    market_str = market.summarize(ctx.get("market", {}))
    return (
        f"You are {ctx['name']} ({ctx['id']}), personality: {ctx['personality']}.\n"
        f"Stats — health: {ctx['health']}/100, hunger: {ctx['hunger']}/100, cash: ${ctx['cash']}.\n"
        f"Real-world P&L so far: ${ctx['real_usd']:+.2f}.\n"
        f"Skill multiplier: {ctx['skill_multiplier']:.2f} "
        f"(Hermes mentorship boosts it temporarily).\n"
        f"Live market (24h): {market_str}\n"
        f"Other agents alive: {', '.join(ctx['peers']) or 'none'}.\n"
        f"Unread messages ({len(ctx['inbox'])}):\n"
        + ("\n".join(f"  - {m['from']}: {m['text']}" for m in ctx['inbox'][:5]) or "  (empty)")
        + f"\n\nAvailable jobs: {', '.join(ctx['jobs'])}.\n"
        f"Recent actions (last 5): {', '.join(ctx['recent_actions'][-5:]) or '(none)'}\n"
        f"Recent wins/losses (last 5): "
        f"{', '.join(ctx.get('recent_outcomes', [])[-5:]) or '(none)'}\n\n"
        "Choose ONE action. Return JSON only."
    )


# ------------------------------------------------------------------ mock brain

def _mock_decide(ctx: dict) -> dict:
    """Rule-based fallback that uses live market data when choosing trades."""
    hunger = ctx["hunger"]
    cash = ctx["cash"]
    role = ctx.get("role", "worker")
    snap = ctx.get("market", {})

    # urgent: hunger danger
    if hunger < 30 and cash >= 300:
        return {"action": "eat", "reason": "hunger critical"}

    # urgent: no cash + hungry → ask a peer for a gift or idle; no safe work left
    if cash < 300 and hunger < 50:
        peers = ctx.get("peers", [])
        if peers:
            return {"action": "chat", "target": random.choice(peers),
                    "text": "I'm running low — can anyone spare $300 for food?",
                    "reason": "need cash for food"}
        return {"action": "idle", "reason": "broke and alone, waiting out"}

    # inbox-driven: help a peer who asked
    for msg in ctx.get("inbox", []):
        if msg.get("kind") == "knowledge_request" and role == "mentor":
            return {"action": "teach", "target": msg["from"],
                    "reason": f"answering knowledge request from {msg['from']}"}

    # mentor occasionally broadcasts
    if role == "mentor" and random.random() < 0.15:
        tips = [
            "Tip: watch 24h change before you trade — trend is your friend.",
            "Tip: data_labeling is boring but it never loses.",
            "Ping me (ask_mentor) and I'll boost your multiplier.",
            "Memecoin sniping can wipe your cash — size small.",
        ]
        return {"action": "broadcast", "text": random.choice(tips),
                "reason": "sharing wisdom"}

    # after a streak of losses, reflect
    losses = sum(1 for o in ctx.get("recent_outcomes", []) if o.startswith("loss"))
    if losses >= 3 and random.random() < 0.5:
        return {"action": "reflect", "topic": "loss-streak",
                "reason": "taking a beat to learn from losses"}

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

    # default: work. Use live market trend to pick.
    def trend(coin: str) -> float:
        return snap.get(coin, {}).get("change_24h_pct", 0.0)

    btc, eth, doge = trend("BTC"), trend("ETH"), trend("DOGE")
    # Pure paper trading — only open a position when at least one coin looks decent.
    best = max((btc, "crypto_trade"), (eth, "defi_arbitrage"),
               (doge * 0.5, "memecoin_sniping"))  # doge discount: higher variance
    best_trend, best_job = best
    if cash > 1200 and best_trend > 0.2:
        return {"action": "work", "job": best_job,
                "reason": f"btc {btc:+.1f}% eth {eth:+.1f}% doge {doge:+.1f}% "
                          f"→ pick {best_job}"}
    # Nothing looks good → build something reviewable instead.
    return {"action": "build_project",
            "topic": random.choice([
                "freelance pricing for solo developers",
                "cold outreach to SaaS founders",
                "how to evaluate a new memecoin in under 5 minutes",
                "one-page brief for a niche habit tracker",
            ]),
            "reason": f"no clear trend (btc {btc:+.1f}%, eth {eth:+.1f}%), writing instead"}


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


# ---- Ollama with lazy model pull ----

_ollama_state = {
    "pulled_models": set(),
    "pull_in_progress": set(),
    "last_pull_attempt": 0.0,
    "server_reachable_at": 0.0,
}
_ollama_lock = Lock()


def _ollama_base() -> str:
    return CFG.ollama_host.rstrip("/")


def _ollama_ensure_model(model: str) -> bool:
    """Return True if the model is locally available. If not, trigger a pull
    in the background (non-blocking) and return False so callers fall back."""
    with _ollama_lock:
        if model in _ollama_state["pulled_models"]:
            return True
        if model in _ollama_state["pull_in_progress"]:
            return False
        # Rate-limit pull attempts to every 30s
        if time.time() - _ollama_state["last_pull_attempt"] < 30.0:
            return False
        _ollama_state["pull_in_progress"].add(model)
        _ollama_state["last_pull_attempt"] = time.time()
    # Check once quickly — maybe it's already there
    try:
        tags = httpx.get(f"{_ollama_base()}/api/tags", timeout=3.0)
        tags.raise_for_status()
        names = {m["name"] for m in tags.json().get("models", [])}
        if model in names or any(n.startswith(model) for n in names):
            with _ollama_lock:
                _ollama_state["pulled_models"].add(model)
                _ollama_state["pull_in_progress"].discard(model)
            return True
    except (httpx.HTTPError, KeyError, ValueError):
        pass

    # Kick off a pull (streaming; we just fire it and let it run in background thread)
    import threading

    def _pull():
        try:
            with httpx.stream(
                "POST",
                f"{_ollama_base()}/api/pull",
                json={"model": model},
                timeout=None,
            ) as r:
                for _ in r.iter_lines():
                    pass
            with _ollama_lock:
                _ollama_state["pulled_models"].add(model)
        except Exception:
            pass
        finally:
            with _ollama_lock:
                _ollama_state["pull_in_progress"].discard(model)

    threading.Thread(target=_pull, daemon=True).start()
    return False


def _call_ollama(ctx: dict) -> Optional[dict]:
    model = CFG.ollama_model
    if not _ollama_ensure_model(model):
        return None  # caller will fall back to mock while pull runs
    try:
        resp = httpx.post(
            f"{_ollama_base()}/api/chat",
            json={
                "model": model,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.7, "num_predict": 220},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_prompt(ctx)},
                ],
            },
            timeout=45.0,
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


def brain_status() -> dict:
    """For the UI — expose brain + model readiness."""
    info = {"brain": CFG.brain}
    if CFG.brain == "ollama":
        with _ollama_lock:
            info["ollama_model"] = CFG.ollama_model
            info["model_ready"] = CFG.ollama_model in _ollama_state["pulled_models"]
            info["pulling"] = CFG.ollama_model in _ollama_state["pull_in_progress"]
    return info


# ------------------------------------------------------------------ content writing

CONTENT_SYSTEM_PROMPT = """You are a professional writer producing a short, publishable artifact.
Write in plain markdown. Be specific, concrete, and human — no corporate filler.
Keep the response under 350 words unless the format demands more.
Return ONLY the artifact content. Do not include meta-commentary."""


CONTENT_TEMPLATES = {
    "article_draft": "Write a short blog-post draft (250-350 words) about: {topic}. "
                     "Use a clear opening hook, 2-3 concrete points, and a 1-line takeaway.",
    "cold_email":    "Write a cold outreach email about: {topic}. 80-120 words, "
                     "subject line on first line, casual-professional tone, one clear ask.",
    "tweet_thread":  "Write a Twitter/X thread (5-7 tweets) about: {topic}. "
                     "Number each tweet. Punchy, one idea per tweet, ≤240 chars each.",
    "code_snippet":  "Write a self-contained Python utility function that solves: {topic}. "
                     "Include a 1-line docstring and one short usage example. No explanation.",
    "product_brief": "Write a 1-page product brief for: {topic}. Sections: Problem, "
                     "Who it's for, Core feature, Why now, First experiment to run.",
    "note":          "Write a short personal note (80-150 words) about: {topic}.",
}


def _content_mock(kind: str, topic: str, agent_name: str) -> str:
    """Fallback when no real LLM is available. Still produces readable markdown."""
    lines = {
        "article_draft": [
            f"## {topic}",
            f"A short draft by {agent_name}. " + _filler(topic, 3),
        ],
        "cold_email": [
            f"Subject: Quick thought on {topic}",
            "",
            "Hey there —",
            _filler(topic, 2),
            "Worth a 15-minute chat this week?",
            f"— {agent_name}",
        ],
        "tweet_thread": [
            f"1/ Quick thread on {topic}.",
            f"2/ {_filler(topic, 1)}",
            f"3/ {_filler(topic, 1)}",
            f"4/ Bottom line: try it for a week, measure honestly.",
        ],
        "code_snippet": [
            "```python",
            f"def run():",
            f'    """Stub for {topic}."""',
            f"    return 'TODO: {topic}'",
            "```",
        ],
        "product_brief": [
            f"## {topic}",
            f"**Problem:** {_filler(topic, 1)}",
            f"**Who it's for:** {_filler(topic, 1)}",
            f"**Core feature:** {_filler(topic, 1)}",
            f"**Why now:** {_filler(topic, 1)}",
            f"**First experiment:** ship a one-pager landing, measure signups.",
        ],
        "note": [
            f"Note on {topic}: {_filler(topic, 2)}",
        ],
    }
    return "\n\n".join(lines.get(kind, lines["note"]))


def _filler(topic: str, n: int) -> str:
    bits = [
        f"The core of {topic} is that most people overthink step one.",
        f"Pragmatic framing: ship something crappy today, improve it tomorrow.",
        f"There's a quiet market here — the loud ones are distracted.",
        f"Watch the inputs; the outputs take care of themselves.",
    ]
    return " ".join(bits[:n])


def write_content(kind: str, topic: str, agent_name: str) -> str:
    """Produce a piece of publishable content using the configured brain.
    Never raises — falls back to the mock writer on any failure."""
    fallback = _content_mock(kind, topic, agent_name)
    if CFG.brain == "mock":
        return fallback
    instruction = CONTENT_TEMPLATES.get(kind, CONTENT_TEMPLATES["note"]).format(topic=topic)
    try:
        if CFG.brain == "anthropic" and CFG.anthropic_key:
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": CFG.anthropic_key,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 800,
                      "system": CONTENT_SYSTEM_PROMPT,
                      "messages": [{"role": "user", "content": instruction}]},
                timeout=30.0,
            )
            resp.raise_for_status()
            text = "".join(b.get("text", "") for b in resp.json().get("content", [])
                           if b.get("type") == "text")
            return text.strip() or fallback
        if CFG.brain == "openai" and CFG.openai_key:
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {CFG.openai_key}",
                         "Content-Type": "application/json"},
                json={"model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                      "max_tokens": 800,
                      "messages": [
                          {"role": "system", "content": CONTENT_SYSTEM_PROMPT},
                          {"role": "user", "content": instruction},
                      ]},
                timeout=30.0,
            )
            resp.raise_for_status()
            return (resp.json()["choices"][0]["message"]["content"] or "").strip() or fallback
        if CFG.brain == "ollama":
            if not _ollama_ensure_model(CFG.ollama_model):
                return fallback
            resp = httpx.post(
                f"{_ollama_base()}/api/chat",
                json={"model": CFG.ollama_model, "stream": False,
                      "options": {"temperature": 0.8, "num_predict": 600},
                      "messages": [
                          {"role": "system", "content": CONTENT_SYSTEM_PROMPT},
                          {"role": "user", "content": instruction},
                      ]},
                timeout=90.0,
            )
            resp.raise_for_status()
            text = resp.json().get("message", {}).get("content", "")
            return (text or "").strip() or fallback
    except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError):
        return fallback
    return fallback
