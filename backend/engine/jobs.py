"""Money-making jobs. Mocked outcomes so the sim runs offline; deterministic-ish
with noise so the leaderboard actually shifts."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class JobResult:
    job: str
    success: bool
    real_usd: float
    note: str


# Each entry: (base_real_usd, success_rate, risk_stddev, description)
JOBS: Dict[str, dict] = {
    "crypto_trade": {
        "base": 8.0, "rate": 0.55, "sd": 4.0,
        "desc": "Simulated spot trade on a major pair",
    },
    "defi_arbitrage": {
        "base": 14.0, "rate": 0.40, "sd": 9.0,
        "desc": "Cross-DEX arbitrage (MEV-ish, higher variance)",
    },
    "freelance_code": {
        "base": 22.0, "rate": 0.70, "sd": 6.0,
        "desc": "Small bug-fix or script gig on a freelance board",
    },
    "content_writing": {
        "base": 6.0, "rate": 0.85, "sd": 1.5,
        "desc": "Short article or blog post",
    },
    "data_labeling": {
        "base": 3.0, "rate": 0.95, "sd": 0.5,
        "desc": "Steady, boring, almost-guaranteed piecework",
    },
    "build_micro_saas": {
        "base": 60.0, "rate": 0.15, "sd": 40.0,
        "desc": "Long shot — build and launch a micro-SaaS",
    },
}


def list_jobs() -> List[dict]:
    return [{"id": k, **v} for k, v in JOBS.items()]


def run_job(job_id: str, skill_multiplier: float = 1.0) -> JobResult:
    """Run a job. skill_multiplier is 1.0 by default; mentorship can raise it."""
    spec = JOBS.get(job_id)
    if spec is None:
        return JobResult(job=job_id, success=False, real_usd=0.0,
                         note=f"unknown job '{job_id}'")
    effective_rate = min(0.98, spec["rate"] * (0.8 + 0.4 * skill_multiplier))
    if random.random() > effective_rate:
        return JobResult(job=job_id, success=False, real_usd=0.0,
                         note=f"failed ({spec['desc']})")
    payout = max(0.5, random.gauss(spec["base"], spec["sd"])) * skill_multiplier
    return JobResult(job=job_id, success=True, real_usd=round(payout, 2),
                     note=spec["desc"])
