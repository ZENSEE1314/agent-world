"""WorldEngine: owns houses + agents, runs the tick loop, routes decisions."""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import Callable, Dict, List, Optional

from . import market
from .agent import Agent, House
from .brain import brain_status, decide, write_content
from .config import CFG
from .economy import EconomyManager
from .income import LEDGER as INCOME_LEDGER
from .jobs import JOBS, list_jobs, run_job
from .logger import log, recent, recent_for
from .messaging import MessageBus
from .paper_trading import BOOK as PAPER_BOOK, JOB_TO_COIN
from .projects import KINDS as PROJECT_KINDS, STORE as PROJECT_STORE


HOUSE_COLORS = ["#f6b6c3", "#f7d488", "#a6d4a1", "#9ecbe8",
                "#c8a5e3", "#f0a48a", "#b8d3a8", "#e9c5a0"]


DEFAULT_AGENTS = [
    # (id,     name,       personality,                                    role,      avatar)
    ("hermes", "Hermes",    "Patient mentor, explains tradeoffs clearly.",  "mentor",  "🧙"),
    ("athena", "Athena",    "Strategic, prefers steady high-value jobs.",   "worker",  "🦉"),
    ("apollo", "Apollo",    "Bold creator, loves building micro-SaaS.",     "worker",  "☀️"),
    ("artemis","Artemis",   "Sharp scout, hunts arbitrage opportunities.",  "worker",  "🏹"),
    ("ares",   "Ares",      "Risk taker, chases high-variance wins.",       "worker",  "⚔️"),
    ("dionysus","Dionysus", "Social butterfly, builds teams and trades.",   "worker",  "🍇"),
    ("hera",   "Hera",      "Disciplined, never lets hunger drop below 50.","worker",  "👑"),
]


class WorldEngine:
    def __init__(self) -> None:
        self.houses: Dict[str, House] = {}
        self.agents: Dict[str, Agent] = {}
        self.economy = EconomyManager()
        self.bus = MessageBus()
        self.tick_no: int = 0
        self.started_at: float = time.time()
        self.divine_commands: List[dict] = []
        self.paused: bool = False
        self._lock = Lock()
        self._listeners: List[Callable[[dict], None]] = []
        self._seed_world()

    # ---- setup -------------------------------------------------------------

    def _seed_world(self) -> None:
        # Six starter houses in a friendly grid
        positions = [(200, 200), (500, 180), (800, 220),
                     (220, 500), (540, 520), (820, 500)]
        for i, (x, y) in enumerate(positions):
            color = HOUSE_COLORS[i % len(HOUSE_COLORS)]
            self.add_house(f"house_{i+1}", x, y, color=color, log_it=False)
        # Seven starter agents — Hermes + 6 workers
        house_ids = list(self.houses.keys())
        for i, (aid, name, personality, role, avatar) in enumerate(DEFAULT_AGENTS):
            house = house_ids[i % len(house_ids)] if house_ids else None
            self.add_agent(aid, name, personality, role=role, avatar=avatar,
                           house_id=house, log_it=False)

    # ---- mutation ----------------------------------------------------------

    def add_house(self, house_id: Optional[str] = None, x: Optional[int] = None,
                  y: Optional[int] = None, color: Optional[str] = None,
                  style: str = "cottage", log_it: bool = True) -> House:
        with self._lock:
            if house_id is None:
                idx = len(self.houses) + 1
                while f"house_{idx}" in self.houses:
                    idx += 1
                house_id = f"house_{idx}"
            if x is None or y is None:
                x = random.randint(150, 950)
                y = random.randint(150, 620)
            if color is None:
                color = random.choice(HOUSE_COLORS)
            h = House(id=house_id, x=x, y=y, color=color, style=style)
            self.houses[house_id] = h
        if log_it:
            log("world", "spawn", f"new house {house_id} at ({x},{y})")
            self._emit_event({"type": "house_added", "house": asdict(h)})
        return h

    def add_agent(self, agent_id: Optional[str] = None, name: Optional[str] = None,
                  personality: str = "", role: str = "worker",
                  avatar: str = "🙂", house_id: Optional[str] = None,
                  log_it: bool = True) -> Agent:
        with self._lock:
            if agent_id is None:
                base = (name or "agent").lower().replace(" ", "_") or "agent"
                agent_id = base
                n = 2
                while agent_id in self.agents:
                    agent_id = f"{base}_{n}"
                    n += 1
            if name is None:
                name = agent_id.capitalize()
            if house_id is None and self.houses:
                # Pick the house with the fewest residents
                occupancy: Dict[str, int] = {h: 0 for h in self.houses}
                for a in self.agents.values():
                    if a.house_id in occupancy:
                        occupancy[a.house_id] += 1
                house_id = min(occupancy, key=lambda h: occupancy[h])
            ag = Agent(id=agent_id, name=name, personality=personality,
                       role=role, avatar=avatar, house_id=house_id)
            self.agents[agent_id] = ag
            self.economy.register(agent_id)
            self.bus.ensure_inbox(agent_id)
        if log_it:
            log(agent_id, "birth", f"{name} joined the world")
            self._emit_event({"type": "agent_added",
                              "agent": ag.public_state(self.economy.snapshot())})
        return ag

    def remove_agent(self, agent_id: str) -> None:
        with self._lock:
            self.agents.pop(agent_id, None)
            self.economy.remove(agent_id)
            self.bus.forget(agent_id)
        log("world", "remove", f"agent {agent_id} removed")

    def reset_world(self) -> dict:
        """Wipe all accumulated state — ledgers, paper book, projects, messages,
        logs, agent stats — but keep houses + agents identities so the world
        layout survives. Tick counter resets to 0."""
        from . import logger as _logger
        with self._lock:
            self.tick_no = 0
            self.started_at = time.time()
            self.divine_commands.clear()
            # Agents keep identity but lose everything earned
            for a in self.agents.values():
                a.health = float(CFG.max_health)
                a.hunger = float(CFG.max_hunger)
                a.alive = True
                a.skill_multiplier = 1.0
                a.skill_floor = 1.0
                a.mentor_ticks = 0
                a.recent_actions.clear()
                a.recent_outcomes.clear()
                a.projects_built = 0
                a.reflections_written = 0
                a.knowledge_requests_sent = 0
                a.knowledge_requests_answered = 0
                a.last_action = "idle"
            self.economy.reset_all()
            self.bus.reset()
            for aid in self.agents:
                self.bus.ensure_inbox(aid)
            PAPER_BOOK.reset()
            PROJECT_STORE.reset()
            INCOME_LEDGER.reset()
            _logger.reset()
        log("world", "reset", "world state wiped — fresh start")
        snap = self.snapshot()
        self._emit_event({"type": "reset", "snapshot": snap})
        return {"ok": True, "tick": self.tick_no, "agents": len(self.agents)}

    def divine_command(self, text: str) -> dict:
        entry = {"ts": time.time(), "text": text}
        self.divine_commands.append(entry)
        if len(self.divine_commands) > 20:
            self.divine_commands = self.divine_commands[-20:]
        log("world", "divine", f"Creator says: {text}")
        # Broadcast to all alive agents
        for aid, agent in self.agents.items():
            if agent.alive:
                self.bus.send("creator", aid, text, kind="divine")
        return entry

    # ---- event stream ------------------------------------------------------

    def on_event(self, cb: Callable[[dict], None]) -> None:
        self._listeners.append(cb)

    def _emit_event(self, evt: dict) -> None:
        for cb in list(self._listeners):
            try:
                cb(evt)
            except Exception:
                pass  # listeners must not break the engine

    # ---- snapshots ---------------------------------------------------------

    def snapshot(self) -> dict:
        econ = self.economy.snapshot()
        agents = [a.public_state(econ) for a in self.agents.values()]
        houses = [asdict(h) for h in self.houses.values()]
        return {
            "tick": self.tick_no,
            "started_at": self.started_at,
            "now": time.time(),
            "paused": self.paused,
            "brain": CFG.brain,
            "brain_status": brain_status(),
            "sim_speed": CFG.sim_speed,
            "tick_seconds": CFG.tick_seconds,
            "houses": houses,
            "agents": agents,
            "jobs": list_jobs(),
            "market": market.snapshot(),
            "paper": PAPER_BOOK.snapshot(),
            "businesses": INCOME_LEDGER.snapshot(),
            "review_queue": [
                {
                    "id": p.id, "agent_id": p.agent_id, "agent_name": p.agent_name,
                    "kind": p.kind, "topic": p.topic, "title": p.title,
                    "body": p.body, "status": p.status, "created_at": p.created_at,
                }
                for p in PROJECT_STORE.list_pending(12)
            ],
            "leaderboard": self.economy.leaderboard(),
            "messages": self.bus.recent(40),
            "logs": recent(80),
            "divine": self.divine_commands[-5:],
        }

    # ---- tick loop ---------------------------------------------------------

    def _build_ctx(self, agent: Agent) -> dict:
        econ = self.economy.get(agent.id)
        peers = [a.id for a in self.agents.values()
                 if a.id != agent.id and a.alive]
        inbox = self.bus.drain(agent.id, max_n=6)
        return {
            "id": agent.id,
            "name": agent.name,
            "personality": agent.personality,
            "role": agent.role,
            "health": int(agent.health),
            "hunger": int(agent.hunger),
            "cash": econ.cash,
            "real_usd": round(econ.real_value_usd, 2),
            "skill_multiplier": agent.skill_multiplier,
            "peers": peers,
            "inbox": inbox,
            "jobs": list(JOBS.keys()),
            "recent_actions": agent.recent_actions,
            "recent_outcomes": agent.recent_outcomes,
            "market": market.snapshot(),
        }

    def _apply_decision(self, agent: Agent, decision: dict) -> None:
        action = decision.get("action", "idle")
        reason = decision.get("reason", "")

        if action == "work":
            job_id = decision.get("job") or random.choice(list(JOBS.keys()))
            # SaaS launches are special: they create a Product that pays recurring
            # MRR on every subsequent tick (handled at the top of _tick_sync).
            if job_id == "launch_saas":
                outcome = INCOME_LEDGER.launch_saas(
                    agent.id, self.tick_no,
                    skill_multiplier=agent.skill_multiplier,
                )
                if outcome["ok"]:
                    agent.remember_action("launched:saas")
                    agent.remember_outcome(f"launch:+MRR${outcome['mrr']:.2f}/tick")
                    agent.nudge_skill(+0.02)
                    agent.append_memory("saas-log",
                        f"{outcome['name']} ✓ MRR ${outcome['mrr']:.2f}/tick churn {outcome['churn']:.1%}")
                    log(agent.id, "launch", outcome["note"])
                else:
                    self.economy.credit_work(agent.id, outcome["real_usd"], reason="saas_launch_fail")
                    agent.remember_action("launch-fail:saas")
                    agent.remember_outcome(f"loss:${outcome['real_usd']:.2f}:launch_saas")
                    agent.nudge_skill(-0.015)
                    agent.append_memory("saas-log", f"✗ {outcome['note']}")
                return

            # Crypto-style jobs route through paper trading at real Coingecko prices.
            # Non-crypto jobs keep the old deterministic-ish RNG payout.
            if job_id in JOB_TO_COIN:
                cash = self.economy.get(agent.id).cash
                pos = PAPER_BOOK.open_position(agent.id, job_id, cash, self.tick_no)
                if pos is not None:
                    log(agent.id, "work",
                        f"{job_id}: opened paper {pos.coin} ${pos.size_usd:.2f} "
                        f"@ ${pos.entry_price:.2f} (closes tick {pos.opened_tick+pos.hold_ticks})")
                    agent.remember_action(f"opened:{job_id}")
                    return
                # Couldn't open (market unavailable or too poor) → fall through to safe job
                job_id = "data_labeling"

            result = run_job(job_id, skill_multiplier=agent.skill_multiplier)
            if result.success:
                self.economy.credit_work(agent.id, result.real_usd, reason=result.job)
                agent.remember_action(f"worked:{job_id}")
                agent.remember_outcome(f"win:+${result.real_usd:.2f}:{job_id}")
                agent.nudge_skill(+0.015)
                agent.append_memory("work-log",
                    f"{job_id} ✓ +${result.real_usd:.2f} real ({result.note})")
            elif result.real_usd < 0:
                self.economy.credit_work(agent.id, result.real_usd, reason=result.job)
                agent.remember_action(f"lost:{job_id}")
                agent.remember_outcome(f"loss:${result.real_usd:.2f}:{job_id}")
                agent.nudge_skill(-0.02)
                agent.append_memory("work-log",
                    f"{job_id} ✗ lost ${-result.real_usd:.2f} real ({result.note})")
            else:
                log(agent.id, "work", f"{job_id} failed — {result.note}")
                agent.remember_action(f"work-fail:{job_id}")
                agent.remember_outcome(f"fail:$0:{job_id}")
                agent.append_memory("work-log", f"{job_id} ✗ {result.note}")
            return

        if action == "eat":
            if self.economy.buy_food(agent.id):
                agent.feed()
                agent.remember_action("ate")
            else:
                # Too broke — fall back to safe work
                result = run_job("data_labeling", skill_multiplier=agent.skill_multiplier)
                if result.success:
                    self.economy.credit_work(agent.id, result.real_usd, reason=result.job)
                agent.remember_action("broke-fallback-work")
            return

        if action == "ask_mentor":
            target_id = decision.get("target", "hermes")
            mentor = self.agents.get(target_id)
            if mentor and mentor.alive and mentor.role == "mentor":
                topic = decision.get("topic") or "work"
                self.bus.send(agent.id, mentor.id,
                              f"Can you help me with {topic}?",
                              kind="knowledge_request", topic=topic)
                agent.knowledge_requests_sent += 1
                agent.remember_action(f"asked-mentor:{topic}")
                log(agent.id, "ask", f"sent knowledge request to {mentor.name} ({topic})")
            else:
                agent.remember_action("ask-mentor-failed")
            return

        if action == "teach":
            target_id = decision.get("target")
            if target_id and target_id in self.agents and self.agents[target_id].alive:
                student = self.agents[target_id]
                student.receive_mentorship(multiplier=1.5, ticks=10)
                agent.knowledge_requests_answered += 1
                self.bus.send(agent.id, student.id,
                              "Here's a trick I learned — your skill is boosted.",
                              kind="mentorship")
                log(agent.id, "teach", f"boosted {student.name} (×1.5 for 10 ticks)")
                agent.remember_action(f"taught:{target_id}")
            return

        if action == "chat":
            target_id = decision.get("target")
            text = decision.get("text", "Hey, how's it going?")[:240]
            if target_id and target_id in self.agents:
                self.bus.send(agent.id, target_id, text, kind="chat")
                agent.remember_action(f"chat:{target_id}")
            return

        if action == "broadcast":
            text = decision.get("text", "Hello, world.")[:240]
            self.bus.broadcast(agent.id, text, kind="broadcast")
            agent.remember_action("broadcast")
            return

        if action == "gift":
            target_id = decision.get("target")
            try:
                amount = int(decision.get("amount", 50))
            except (TypeError, ValueError):
                amount = 50
            if target_id and target_id in self.agents:
                self.economy.transfer(agent.id, target_id, amount, reason="gift")
                agent.remember_action(f"gift:{target_id}:${amount}")
            return

        if action == "build_project":
            topic = decision.get("topic") or random.choice([
                "freelance pricing for solo developers",
                "cold outreach to SaaS founders",
                "how to evaluate a new memecoin in under 5 minutes",
                "weekly notes: what I'd tell a junior engineer",
                "one-page brief for a niche habit tracker",
                "thread on building in public for lazy people",
                "python script: rename a folder of files by pattern",
            ])
            # Pick a kind based on the topic word, or let the decision request one
            hint = str(decision.get("kind") or "").lower()
            if hint in PROJECT_KINDS:
                kind = hint
            elif "email" in topic.lower() or "outreach" in topic.lower():
                kind = "cold_email"
            elif "thread" in topic.lower():
                kind = "tweet_thread"
            elif "script" in topic.lower() or "python" in topic.lower() or "code" in topic.lower():
                kind = "code_snippet"
            elif "brief" in topic.lower() or "product" in topic.lower():
                kind = "product_brief"
            elif "note" in topic.lower():
                kind = "note"
            else:
                kind = "article_draft"
            body = write_content(kind, topic, agent.name)
            title = topic.strip().capitalize()[:80]
            proj = PROJECT_STORE.create(
                agent_id=agent.id, agent_name=agent.name,
                kind=kind, topic=topic, title=title, body=body,
            )
            agent.projects_built += 1
            log(agent.id, "build", f"shipped {kind} → {proj.id} ({title})")
            agent.remember_action(f"built:{kind}")
            return

        if action == "reflect":
            topic = decision.get("topic") or "general"
            wins = [o for o in agent.recent_outcomes if o.startswith("win:")]
            losses = [o for o in agent.recent_outcomes if o.startswith("loss:")]
            summary = (
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Reflection on {topic}: "
                f"{len(wins)} wins, {len(losses)} losses in last 20 actions. "
                f"Cash ${self.economy.get(agent.id).cash}. "
                f"Skill floor {agent.skill_floor:.2f}. "
                f"Note: {decision.get('reason', '')[:120]}"
            )
            agent.write_reflection(topic, summary)
            log(agent.id, "reflect", f"wrote reflection on {topic}")
            agent.remember_action(f"reflect:{topic}")
            return

        # idle
        agent.remember_action("idle")

    async def tick(self) -> dict:
        """Async wrapper so existing callers still work. Body runs in a thread."""
        await asyncio.to_thread(self._tick_sync)
        return self.snapshot()

    async def run_forever(self) -> None:
        # Brief grace period so the healthcheck can hit /api/healthz before the
        # first tick kicks off a storm of synchronous LLM calls on the event loop.
        await asyncio.sleep(5)
        while True:
            if not self.paused:
                try:
                    # Run the tick body in a thread pool — decide() / market calls /
                    # Ollama http are sync blocking, and running them directly on
                    # the loop starves FastAPI handlers (including the healthcheck).
                    await asyncio.to_thread(self._tick_sync)
                except Exception as e:
                    log("world", "error", f"tick crashed: {e}")
            await asyncio.sleep(CFG.tick_seconds)

    def set_paused(self, paused: bool) -> dict:
        self.paused = bool(paused)
        log("world", "info", "paused" if self.paused else "resumed")
        self._emit_event({"type": "pause_change", "paused": self.paused})
        return {"ok": True, "paused": self.paused}

    def _tick_sync(self) -> None:
        """Synchronous tick body — safe to run in a worker thread."""
        self.tick_no += 1
        # 0. Pay out recurring real-USD income from launched SaaS / approved
        #    content — pillars 1 and 2's long tail.
        for payout in INCOME_LEDGER.tick_recurring(self.tick_no):
            agent = self.agents.get(payout.agent_id)
            if agent is None:
                continue
            self.economy.credit_work(agent.id, payout.real_usd, reason=payout.note)
            agent.remember_outcome(f"mrr:+${payout.real_usd:.2f}:{payout.product_id}")
        # 1. Close due paper trades (hits Coingecko; blocking OK in thread)
        closed = PAPER_BOOK.close_due(self.tick_no)
        for c in closed:
            agent = self.agents.get(c.agent_id)
            if agent is None:
                continue
            self.economy.credit_work(agent.id, c.pnl_usd, reason=f"{c.job}:{c.coin}")
            if c.pnl_usd >= 0:
                agent.remember_outcome(f"win:+${c.pnl_usd:.2f}:{c.job}")
                agent.nudge_skill(+0.015)
                agent.append_memory("trade-log",
                    f"{c.coin} ✓ +${c.pnl_usd:.2f} real ({c.entry_price:.2f}→{c.exit_price:.2f})")
                log(agent.id, "earn",
                    f"closed {c.coin} +${c.pnl_usd:.2f} "
                    f"({c.entry_price:.2f}→{c.exit_price:.2f})")
            else:
                agent.remember_outcome(f"loss:${c.pnl_usd:.2f}:{c.job}")
                agent.nudge_skill(-0.02)
                agent.append_memory("trade-log",
                    f"{c.coin} ✗ ${c.pnl_usd:.2f} real ({c.entry_price:.2f}→{c.exit_price:.2f})")
                log(agent.id, "loss",
                    f"closed {c.coin} ${c.pnl_usd:.2f} "
                    f"({c.entry_price:.2f}→{c.exit_price:.2f})")
        # 2. Decay
        for agent in list(self.agents.values()):
            agent.decay()
        # 3. Decide + act
        for agent in list(self.agents.values()):
            if not agent.alive:
                continue
            ctx = self._build_ctx(agent)
            try:
                decision = decide(ctx)
            except Exception as e:
                decision = {"action": "idle", "reason": f"brain error: {e}"}
                log(agent.id, "error", f"brain crashed: {e}")
            try:
                self._apply_decision(agent, decision)
            except Exception as e:
                log(agent.id, "error", f"action crashed: {e}")
        snap = self.snapshot()
        self._emit_event({"type": "tick", "snapshot": snap})


# Global singleton
WORLD = WorldEngine()
