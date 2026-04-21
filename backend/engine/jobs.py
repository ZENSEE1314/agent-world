"""Money-making jobs. Outcomes can be positive OR negative — losing trades cost real $."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List

from . import market


@dataclass
class JobResult:
    job: str
    success: bool
    real_usd: float   # + when you earn, - when you lose
    note: str


# Each entry: (base_real_usd, success_rate, risk_stddev, can_lose, market_coin, description)
JOBS: Dict[str, dict] = {
    "crypto_trade": {
        "base": 9.0, "rate": 0.55, "sd": 5.0, "can_lose": True, "coin": "BTC",
        "desc": "Spot trade BTC — biased by today's 24h trend",
    },
    "defi_arbitrage": {
        "base": 16.0, "rate": 0.45, "sd": 11.0, "can_lose": True, "coin": "ETH",
        "desc": "Cross-DEX arbitrage (high variance, MEV-ish)",
    },
    "memecoin_sniping": {
        "base": 30.0, "rate": 0.25, "sd": 45.0, "can_lose": True, "coin": "DOGE",
        "desc": "Chase a trending memecoin — huge upside, huge downside",
    },
}


def list_jobs() -> List[dict]:
    return [{"id": k, **{kk: v for kk, v in vv.items() if kk != "coin"}}
            for k, vv in JOBS.items()]


def run_job(job_id: str, skill_multiplier: float = 1.0) -> JobResult:
    """Run a job. May return negative real_usd for lose-capable jobs."""
    spec = JOBS.get(job_id)
    if spec is None:
        return JobResult(job=job_id, success=False, real_usd=0.0,
                         note=f"unknown job '{job_id}'")

    # Live market bias: a hot coin raises success odds, a crashing one lowers them.
    coin = spec.get("coin")
    snap = market.snapshot() if coin else {}
    bias = market.trend_score(snap, coin) if coin else 0.0
    effective_rate = max(0.05, min(0.98,
        spec["rate"] * (0.8 + 0.4 * skill_multiplier) + 0.12 * bias))

    win = random.random() < effective_rate
    payout = max(0.5, random.gauss(spec["base"], spec["sd"])) * skill_multiplier

    if win:
        note = spec["desc"]
        if coin:
            note += f" (24h {coin} {snap.get(coin, {}).get('change_24h_pct', 0):+.2f}%)"
        return JobResult(job=job_id, success=True, real_usd=round(payout, 2), note=note)

    if spec["can_lose"]:
        # Losing trade: lose a fraction of the stake, biased further by the market.
        loss = payout * random.uniform(0.3, 1.0) * (1 - 0.3 * bias)
        loss = max(0.5, round(loss, 2))
        note = f"lost on {spec['desc']}"
        if coin:
            note += f" (24h {coin} {snap.get(coin, {}).get('change_24h_pct', 0):+.2f}%)"
        return JobResult(job=job_id, success=False, real_usd=-loss, note=note)

    return JobResult(job=job_id, success=False, real_usd=0.0,
                     note=f"failed ({spec['desc']})")
