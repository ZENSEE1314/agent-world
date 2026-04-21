"""Live market data from Coingecko's public API (no key needed).

Keeps a short in-memory cache so we don't hammer the API each tick."""
from __future__ import annotations

import time
from threading import Lock
from typing import Dict, Optional

import httpx


COINS = ["bitcoin", "ethereum", "solana", "dogecoin", "ripple"]
SYMBOLS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "dogecoin": "DOGE",
    "ripple": "XRP",
}
CACHE_TTL = 90.0  # seconds


_cache: Dict[str, dict] = {}
_cache_ts: float = 0.0
_lock = Lock()


def snapshot() -> Dict[str, dict]:
    """Return {symbol: {price_usd, change_24h_pct}} for the tracked coins.
    Falls back to an empty dict on failure; sim must keep running."""
    global _cache_ts
    with _lock:
        if _cache and (time.time() - _cache_ts) < CACHE_TTL:
            return dict(_cache)
    fresh = _fetch()
    if fresh:
        with _lock:
            _cache.clear()
            _cache.update(fresh)
            _cache_ts = time.time()
        return dict(fresh)
    # On failure, return whatever we have (even if stale)
    with _lock:
        return dict(_cache)


def _fetch() -> Optional[Dict[str, dict]]:
    try:
        resp = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": ",".join(COINS),
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            timeout=8.0,
            headers={"User-Agent": "agent-world/0.1"},
        )
        resp.raise_for_status()
        raw = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    out = {}
    for coin, info in raw.items():
        sym = SYMBOLS.get(coin, coin.upper())
        out[sym] = {
            "price_usd": float(info.get("usd", 0.0)),
            "change_24h_pct": float(info.get("usd_24h_change", 0.0)),
        }
    return out


def summarize(snap: Dict[str, dict]) -> str:
    """Short text summary for prompts."""
    if not snap:
        return "(market data unavailable)"
    parts = []
    for sym, d in snap.items():
        parts.append(f"{sym} ${d['price_usd']:.2f} ({d['change_24h_pct']:+.2f}%)")
    return ", ".join(parts)


def trend_score(snap: Dict[str, dict], coin: str) -> float:
    """Return a -1..+1-ish trend score for one coin. Used to bias job payouts."""
    if coin not in snap:
        return 0.0
    pct = snap[coin]["change_24h_pct"]
    # Clamp and scale: a +5% day maps to ~+1.0 bias, a -5% day to ~-1.0
    return max(-1.5, min(1.5, pct / 5.0))
