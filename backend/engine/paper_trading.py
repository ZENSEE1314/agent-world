"""Paper trading against real Coingecko mid-prices.

When an agent picks a crypto-style job we open a LONG paper position at the
live price, model 0.10% fee + 0.05% slippage per side, hold 1-4 ticks, then
close at the live price at close time. The realized P&L (in USD) is fed back
to `EconomyManager.credit_work`, which already handles the symmetric 10x rule.

We keep books small and in-memory — this is a simulation, not a ledger."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, List, Optional

from . import market


FEE_PER_SIDE = 0.0010     # 0.10%
SLIPPAGE_PER_SIDE = 0.0005  # 0.05%
DEFAULT_MIN_SIZE_USD = 2.0
DEFAULT_MAX_SIZE_USD = 40.0

# Map our job ids to which coin they trade
JOB_TO_COIN: Dict[str, str] = {
    "crypto_trade": "BTC",
    "defi_arbitrage": "ETH",
    "memecoin_sniping": "DOGE",
}


@dataclass
class Position:
    id: str
    agent_id: str
    coin: str
    job: str
    entry_price: float
    size_usd: float        # real-world notional
    opened_tick: int
    hold_ticks: int
    entry_fee: float       # real USD charged at open
    opened_at: float = field(default_factory=time.time)

    def ready_to_close(self, current_tick: int) -> bool:
        return current_tick >= self.opened_tick + self.hold_ticks


@dataclass
class Closed:
    id: str
    agent_id: str
    coin: str
    job: str
    entry_price: float
    exit_price: float
    size_usd: float
    pnl_usd: float
    closed_tick: int
    closed_at: float = field(default_factory=time.time)


class PaperTradingBook:
    def __init__(self) -> None:
        self._open: Dict[str, Position] = {}
        self._closed: List[Closed] = []
        self._lock = Lock()
        self._counter = 0

    # ---- open / close ------------------------------------------------------

    def open_position(
        self,
        agent_id: str,
        job: str,
        cash_in_world: int,
        current_tick: int,
    ) -> Optional[Position]:
        coin = JOB_TO_COIN.get(job)
        if coin is None:
            return None
        snap = market.snapshot()
        if coin not in snap or snap[coin]["price_usd"] <= 0:
            return None
        mid = float(snap[coin]["price_usd"])
        # Buy with slippage up (you pay a bit more than mid)
        entry_price = mid * (1 + SLIPPAGE_PER_SIDE)

        # Size: 10% of in-world cash, converted back to real-world $ (10x rule).
        # Clamped so agents can't bet the farm on tick 1.
        real_cash_equiv = max(0.0, cash_in_world / 10.0)
        size_usd = max(DEFAULT_MIN_SIZE_USD,
                       min(DEFAULT_MAX_SIZE_USD, real_cash_equiv * 0.10))

        # Don't open if the agent literally can't afford the fee.
        entry_fee = size_usd * FEE_PER_SIDE
        if cash_in_world < int(round(entry_fee * 10 * 1.5)):
            return None

        hold = random.choice([1, 2, 2, 3, 3, 4])
        with self._lock:
            self._counter += 1
            pid = f"p{self._counter}"
            pos = Position(
                id=pid, agent_id=agent_id, coin=coin, job=job,
                entry_price=entry_price, size_usd=size_usd,
                opened_tick=current_tick, hold_ticks=hold,
                entry_fee=entry_fee,
            )
            self._open[pid] = pos
        return pos

    def close_due(self, current_tick: int) -> List[Closed]:
        """Close every position whose hold has elapsed. Returns Closed list."""
        snap = market.snapshot()
        to_close: List[Position] = []
        with self._lock:
            for pid, pos in list(self._open.items()):
                if pos.ready_to_close(current_tick):
                    to_close.append(pos)
                    del self._open[pid]
        closed: List[Closed] = []
        for pos in to_close:
            coin_info = snap.get(pos.coin)
            if not coin_info or coin_info["price_usd"] <= 0:
                # Can't price it — return the stake minus entry fee as a wash
                c = Closed(
                    id=pos.id, agent_id=pos.agent_id, coin=pos.coin, job=pos.job,
                    entry_price=pos.entry_price, exit_price=pos.entry_price,
                    size_usd=pos.size_usd, pnl_usd=-pos.entry_fee,
                    closed_tick=current_tick,
                )
            else:
                mid = float(coin_info["price_usd"])
                exit_price = mid * (1 - SLIPPAGE_PER_SIDE)   # sell slips down
                gross_pnl = (exit_price - pos.entry_price) / pos.entry_price * pos.size_usd
                exit_fee = pos.size_usd * FEE_PER_SIDE
                net_pnl = gross_pnl - pos.entry_fee - exit_fee
                c = Closed(
                    id=pos.id, agent_id=pos.agent_id, coin=pos.coin, job=pos.job,
                    entry_price=pos.entry_price, exit_price=exit_price,
                    size_usd=pos.size_usd, pnl_usd=round(net_pnl, 4),
                    closed_tick=current_tick,
                )
            closed.append(c)
            with self._lock:
                self._closed.append(c)
                if len(self._closed) > 300:
                    self._closed = self._closed[-300:]
        return closed

    # ---- views -------------------------------------------------------------

    def snapshot(self) -> dict:
        snap = market.snapshot()
        with self._lock:
            open_list = []
            for pos in self._open.values():
                current = float(snap.get(pos.coin, {}).get("price_usd", pos.entry_price))
                unreal = (current - pos.entry_price) / pos.entry_price * pos.size_usd
                open_list.append({
                    "id": pos.id,
                    "agent": pos.agent_id,
                    "coin": pos.coin,
                    "job": pos.job,
                    "entry_price": round(pos.entry_price, 4),
                    "current_price": round(current, 4),
                    "size_usd": round(pos.size_usd, 2),
                    "unrealized_pnl": round(unreal, 4),
                    "opened_tick": pos.opened_tick,
                    "closes_at_tick": pos.opened_tick + pos.hold_ticks,
                })
            closed_list = [
                {
                    "id": c.id, "agent": c.agent_id, "coin": c.coin, "job": c.job,
                    "entry_price": round(c.entry_price, 4),
                    "exit_price": round(c.exit_price, 4),
                    "size_usd": round(c.size_usd, 2),
                    "pnl_usd": c.pnl_usd,
                    "closed_tick": c.closed_tick,
                    "closed_at": c.closed_at,
                }
                for c in self._closed[-40:]
            ]
        return {"open": open_list, "recent_closed": closed_list}


BOOK = PaperTradingBook()
