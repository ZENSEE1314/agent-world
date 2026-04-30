"""Recurring-income ledger for the four pillars beyond one-shot trades.

Pillar 1 — Productized service / micro-SaaS:
    Each `launch_saas` success creates a Product with starting MRR (real $/tick)
    and a per-tick churn rate. Every tick the WorldEngine asks this ledger for
    `tick_recurring()` and credits each owner. MRR decays multiplicatively each
    tick by the churn rate; once it falls below DEAD_MRR the product is retired.

Pillar 2 — Content / affiliate royalties:
    When the user APPROVES a project in the review queue, `royalty_for_kind()`
    returns a one-shot real-USD payout. Approved articles also seed a small
    long-tail ad income stream (a Product with very low MRR, high churn).

Pillar 3 — Automated arbitrage (POD listings, freelance bids, affiliate posts):
    Handled directly inside `jobs.py`. No persistent state needed beyond what
    EconomyManager already tracks.

Pillar 4 — Trading bots:
    Already covered by `paper_trading.py`. We don't duplicate it here.

Everything is in-memory; the sim is not a real ledger."""
from __future__ import annotations

import random
import time
from dataclasses import asdict, dataclass, field
from threading import Lock
from typing import Dict, List, Optional


DEAD_MRR = 0.20   # below this real-$/tick we kill the product


# ---- product spec by kind -------------------------------------------------

# (label, starting MRR range, churn-per-tick range, success-rate at launch,
#  failed-launch-cost-real-usd)
SAAS_NICHES = [
    ("micro_dashboard",   (3.5, 11.0), (0.020, 0.060), 0.55, 5.0),
    ("niche_api",         (4.5, 14.0), (0.025, 0.070), 0.45, 7.0),
    ("automation_widget", (2.5,  8.0), (0.030, 0.080), 0.60, 4.0),
    ("scraper_saas",      (5.5, 18.0), (0.035, 0.090), 0.40, 9.0),
    ("ai_wrapper",        (3.0, 10.0), (0.040, 0.100), 0.50, 6.0),
]


# Royalty paid (real USD) when the human APPROVES a project of this kind.
# Tuple: (one_shot_min, one_shot_max, seed_mrr, seed_churn).
# seed_mrr=0 means "no recurring component, just the one-shot payout".
APPROVAL_ROYALTY = {
    "article_draft":  ((4.0, 12.0), (0.40, 0.18)),   # ad/affiliate trickle
    "cold_email":     ((20.0, 80.0), (0.0, 0.0)),    # one reply ⇒ one client
    "tweet_thread":   ((1.0, 5.0), (0.10, 0.25)),    # tiny but viral-eligible
    "code_snippet":   ((2.0, 8.0), (0.0, 0.0)),
    "product_brief":  ((5.0, 15.0), (0.0, 0.0)),
    "note":           ((0.0, 0.0), (0.0, 0.0)),
}


@dataclass
class Product:
    id: str
    agent_id: str
    kind: str            # "saas:<niche>" or "content:<project_kind>"
    name: str
    mrr: float           # current real-USD per tick
    churn: float         # multiplicative decay per tick
    born_tick: int
    born_at: float = field(default_factory=time.time)
    last_paid_tick: int = -1
    total_real_usd: float = 0.0
    alive: bool = True


@dataclass
class TickPayout:
    agent_id: str
    product_id: str
    real_usd: float
    note: str


class IncomeLedger:
    def __init__(self) -> None:
        self._products: Dict[str, Product] = {}
        self._lock = Lock()
        self._counter = 0

    # ---- creation ----------------------------------------------------------

    def _new_id(self) -> str:
        self._counter += 1
        return f"prod_{self._counter}"

    def launch_saas(self, agent_id: str, current_tick: int,
                    skill_multiplier: float = 1.0) -> dict:
        """Try to launch a productized service. Returns a dict with the outcome
        — either a successful launch (a new Product) or a failed launch (cost
        the agent the landing-page bill in real USD)."""
        niche, mrr_range, churn_range, base_rate, fail_cost = random.choice(SAAS_NICHES)
        # Skill nudges launch odds and starting MRR a little.
        rate = max(0.10, min(0.92, base_rate * (0.7 + 0.5 * skill_multiplier)))
        if random.random() >= rate:
            return {
                "ok": False,
                "real_usd": -round(fail_cost, 2),
                "note": f"failed launch ({niche}) — burned ${fail_cost:.0f} on landing/ads",
            }
        mrr = round(random.uniform(*mrr_range) * skill_multiplier, 2)
        churn = round(random.uniform(*churn_range), 4)
        with self._lock:
            pid = self._new_id()
            name = f"{niche.replace('_', ' ').title()} #{self._counter}"
            p = Product(
                id=pid, agent_id=agent_id, kind=f"saas:{niche}",
                name=name, mrr=mrr, churn=churn, born_tick=current_tick,
            )
            self._products[pid] = p
        return {
            "ok": True, "product_id": pid, "name": name, "niche": niche,
            "mrr": mrr, "churn": churn,
            "note": f"launched {name} — starting MRR ${mrr:.2f}/tick, churn {churn:.1%}",
        }

    def seed_content_stream(self, agent_id: str, project_kind: str,
                            project_id: str, current_tick: int) -> Optional[Product]:
        """When the user approves a project of a kind that has a recurring
        ad/affiliate trickle, create a small Product so it pays out over time."""
        seed = APPROVAL_ROYALTY.get(project_kind)
        if not seed:
            return None
        seed_mrr, seed_churn = seed[1]
        if seed_mrr <= 0:
            return None
        with self._lock:
            pid = self._new_id()
            p = Product(
                id=pid, agent_id=agent_id,
                kind=f"content:{project_kind}",
                name=f"{project_kind}:{project_id}",
                mrr=round(seed_mrr, 3), churn=round(seed_churn, 4),
                born_tick=current_tick,
            )
            self._products[pid] = p
        return p

    # ---- per-tick crediting ------------------------------------------------

    def tick_recurring(self, current_tick: int) -> List[TickPayout]:
        """Pay every alive product, then decay MRR and retire dead ones.
        Returns a list of TickPayout records (already in real USD)."""
        payouts: List[TickPayout] = []
        with self._lock:
            for p in list(self._products.values()):
                if not p.alive:
                    continue
                amount = round(p.mrr, 4)
                if amount > 0:
                    payouts.append(TickPayout(
                        agent_id=p.agent_id, product_id=p.id,
                        real_usd=amount,
                        note=f"{p.name} MRR",
                    ))
                    p.total_real_usd = round(p.total_real_usd + amount, 4)
                    p.last_paid_tick = current_tick
                # Decay MRR for next tick
                p.mrr = round(p.mrr * (1.0 - p.churn), 4)
                if p.mrr < DEAD_MRR:
                    p.alive = False
        return payouts

    # ---- views -------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            alive = [asdict(p) for p in self._products.values() if p.alive]
            dead = [asdict(p) for p in self._products.values() if not p.alive]
        alive.sort(key=lambda r: r["mrr"], reverse=True)
        return {
            "alive": alive[:40],
            "alive_count": len(alive),
            "dead_count": len(dead),
            "total_mrr_real_usd": round(sum(p["mrr"] for p in alive), 2),
        }

    def stats_for(self, agent_id: str) -> dict:
        with self._lock:
            mine = [p for p in self._products.values() if p.agent_id == agent_id]
            alive = [p for p in mine if p.alive]
        return {
            "products_total": len(mine),
            "products_alive": len(alive),
            "mrr_real_usd": round(sum(p.mrr for p in alive), 2),
            "lifetime_real_usd": round(sum(p.total_real_usd for p in mine), 2),
        }

    def reset(self) -> None:
        with self._lock:
            self._products.clear()
            self._counter = 0


# ---- one-shot royalty helper ---------------------------------------------

def royalty_for_kind(kind: str) -> float:
    """Real-USD one-shot payout when a project of `kind` gets approved."""
    spec = APPROVAL_ROYALTY.get(kind)
    if not spec:
        return 0.0
    lo, hi = spec[0]
    if hi <= 0:
        return 0.0
    return round(random.uniform(lo, hi), 2)


LEDGER = IncomeLedger()
