"""Economy: per-agent cash, food purchases, real→game multiplier, leaderboard stats."""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, List

from .config import CFG
from .logger import log


@dataclass
class Ledger:
    cash: int = CFG.starting_cash
    total_earned: int = 0   # lifetime in-world $ from work
    total_spent: int = 0    # lifetime in-world $ spent (food etc)
    real_value_usd: float = 0.0  # simulated "real" $ produced by their work
    meals_eaten: int = 0


class EconomyManager:
    def __init__(self) -> None:
        self._ledgers: Dict[str, Ledger] = {}
        self._lock = Lock()

    def register(self, agent_id: str, starting_cash: int | None = None) -> Ledger:
        with self._lock:
            l = Ledger(cash=starting_cash if starting_cash is not None else CFG.starting_cash)
            self._ledgers[agent_id] = l
            return l

    def get(self, agent_id: str) -> Ledger:
        with self._lock:
            return self._ledgers[agent_id]

    def remove(self, agent_id: str) -> None:
        with self._lock:
            self._ledgers.pop(agent_id, None)

    # ---- transactions -------------------------------------------------------

    def credit_work(self, agent_id: str, real_usd: float, reason: str = "work") -> int:
        """Convert real-world $ to in-world $ via the 10x multiplier. Returns in-world $ credited."""
        in_world = int(round(real_usd * CFG.real_to_game_multiplier))
        with self._lock:
            l = self._ledgers[agent_id]
            l.cash += in_world
            l.total_earned += in_world
            l.real_value_usd += real_usd
        log(agent_id, "earn", f"+${in_world} ({reason}; real ${real_usd:.2f})",
            cash_delta=in_world, real_usd=real_usd)
        return in_world

    def buy_food(self, agent_id: str) -> bool:
        with self._lock:
            l = self._ledgers[agent_id]
            if l.cash < CFG.food_cost:
                log(agent_id, "fail", f"tried to buy food but only has ${l.cash}")
                return False
            l.cash -= CFG.food_cost
            l.total_spent += CFG.food_cost
            l.meals_eaten += 1
        log(agent_id, "buy", f"bought a meal for ${CFG.food_cost}")
        return True

    def transfer(self, src: str, dst: str, amount: int, reason: str = "gift") -> bool:
        if amount <= 0:
            return False
        with self._lock:
            if src not in self._ledgers or dst not in self._ledgers:
                return False
            if self._ledgers[src].cash < amount:
                return False
            self._ledgers[src].cash -= amount
            self._ledgers[src].total_spent += amount
            self._ledgers[dst].cash += amount
        log(src, "transfer", f"sent ${amount} to {dst} ({reason})")
        log(dst, "transfer", f"received ${amount} from {src} ({reason})")
        return True

    # ---- views --------------------------------------------------------------

    def snapshot(self) -> Dict[str, dict]:
        with self._lock:
            return {
                aid: {
                    "cash": l.cash,
                    "earned": l.total_earned,
                    "spent": l.total_spent,
                    "real_usd": round(l.real_value_usd, 2),
                    "meals": l.meals_eaten,
                }
                for aid, l in self._ledgers.items()
            }

    def leaderboard(self) -> List[dict]:
        with self._lock:
            rows = [
                {
                    "agent": aid,
                    "cash": l.cash,
                    "earned": l.total_earned,
                    "real_usd": round(l.real_value_usd, 2),
                }
                for aid, l in self._ledgers.items()
            ]
        rows.sort(key=lambda r: (r["real_usd"], r["earned"]), reverse=True)
        return rows
