"""Money-making jobs covering the four AI-income pillars:

  1. Productized service / micro-SaaS  → `launch_saas` (one-shot to seed MRR
     in `income.LEDGER`; recurring revenue is paid by the world tick loop)
  2. Content pipelines                  → `build_project` action handles
     creation; royalties are paid in `routes.review_project` on approve
  3. Automated arbitrage                → `pod_listing`, `freelance_bid`,
     `affiliate_promo`, plus the original crypto `defi_arbitrage`
  4. Trading bots                       → `crypto_trade`, `defi_arbitrage`,
     `memecoin_sniping` (routed through `paper_trading`)

`run_job` here covers anything that resolves in a single tick. Crypto-pillar
jobs are short-circuited in `world.py` to use real Coingecko prices via
`paper_trading.BOOK`. The SaaS launch is also short-circuited in `world.py`
because it needs to create a Product, not just credit cash."""
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


# Each entry: base_real_usd, success_rate, risk_stddev, can_lose, optional
# `coin` for market-driven jobs, and `pillar` so the UI can label them.
JOBS: Dict[str, dict] = {
    # --- pillar 4: trading bots ------------------------------------------
    "crypto_trade": {
        "base": 9.0, "rate": 0.55, "sd": 5.0, "can_lose": True, "coin": "BTC",
        "pillar": "trading",
        "desc": "Spot trade BTC — biased by today's 24h trend",
    },
    "memecoin_sniping": {
        "base": 30.0, "rate": 0.25, "sd": 45.0, "can_lose": True, "coin": "DOGE",
        "pillar": "trading",
        "desc": "Chase a trending memecoin — huge upside, huge downside",
    },

    # --- pillar 3: automated arbitrage ----------------------------------
    "defi_arbitrage": {
        "base": 16.0, "rate": 0.45, "sd": 11.0, "can_lose": True, "coin": "ETH",
        "pillar": "arbitrage",
        "desc": "Cross-DEX arbitrage on ETH (high variance, MEV-ish)",
    },
    "pod_listing": {
        "base": 1.8, "rate": 0.55, "sd": 1.2, "can_lose": False,
        "pillar": "arbitrage",
        "desc": "List a print-on-demand design — small wins, no inventory",
    },
    "freelance_bid": {
        "base": 9.0, "rate": 0.22, "sd": 6.0, "can_lose": False,
        "pillar": "arbitrage",
        "desc": "Auto-bid on a freelance gig — most fail, occasional clients",
    },
    "affiliate_promo": {
        "base": 3.2, "rate": 0.45, "sd": 2.4, "can_lose": False,
        "pillar": "arbitrage",
        "desc": "Drop an affiliate link in a niche community",
    },
    "ecom_dropship": {
        "base": 4.5, "rate": 0.40, "sd": 3.5, "can_lose": True,
        "pillar": "arbitrage",
        "desc": "List a dropship product — ad spend can outrun margin",
    },

    # --- pillar 1: productized service / micro-SaaS ----------------------
    "launch_saas": {
        "base": 0.0, "rate": 0.0, "sd": 0.0, "can_lose": True,
        "pillar": "saas",
        "desc": "Launch a tiny productized service — recurring MRR if it sticks",
    },

    # --- safe baseline ---------------------------------------------------
    "data_labeling": {
        "base": 1.2, "rate": 0.85, "sd": 0.6, "can_lose": False,
        "pillar": "labor",
        "desc": "Boring labeling work — slow but reliable",
    },
}


def list_jobs() -> List[dict]:
    return [{"id": k, **{kk: v for kk, v in vv.items() if kk != "coin"}}
            for k, vv in JOBS.items()]


def run_job(job_id: str, skill_multiplier: float = 1.0) -> JobResult:
    """Run a one-shot job. May return negative real_usd for lose-capable jobs.

    Note: `crypto_trade`, `defi_arbitrage`, `memecoin_sniping`, and
    `launch_saas` are normally short-circuited in `world.py` and don't reach
    here. We still handle them defensively so the function never raises."""
    spec = JOBS.get(job_id)
    if spec is None:
        return JobResult(job=job_id, success=False, real_usd=0.0,
                         note=f"unknown job '{job_id}'")

    if job_id == "launch_saas":
        # Defensive fallback only — world.py routes this to income.LEDGER.
        return JobResult(job=job_id, success=False, real_usd=0.0,
                         note="launch_saas should be handled by world.py")

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
