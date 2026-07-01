"""Point-in-time manager quality scoring.

We do NOT treat all whales equally. A cluster of mediocre closet-indexers
means little; a few top-decile concentrated managers initiating the same
name means a lot.

Crucially this is computed WITHOUT lookahead: quality is a running tally of
how a manager's *already-public, already-resolved* new positions performed
between when they were disclosed and the current simulated date. A pick that
hasn't had time to play out yet does not count.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _Stat:
    n: int = 0
    sum_ret: float = 0.0


@dataclass
class ManagerQuality:
    # cik -> running realized-return stats of its disclosed new positions
    stats: dict[str, _Stat] = field(default_factory=dict)
    # pending picks awaiting resolution: (cik, ticker, entry_price, entry_date)
    _pending: list[tuple[str, str, float, str]] = field(default_factory=list)

    def record_pick(self, cik: str, ticker: str, entry_price: float, entry_date: str):
        if entry_price and entry_price > 0:
            self._pending.append((cik, ticker, entry_price, entry_date))

    def resolve_due(self, now: str, price_lookup, horizon_days: int = 126):
        """Resolve picks whose evaluation horizon has elapsed as of `now`.
        price_lookup(ticker, date) must be point-in-time (<= now)."""
        from datetime import date

        still_pending = []
        for cik, ticker, entry_px, entry_date in self._pending:
            elapsed = (date.fromisoformat(now) - date.fromisoformat(entry_date)).days
            if elapsed < horizon_days:
                still_pending.append((cik, ticker, entry_px, entry_date))
                continue
            px = price_lookup(ticker, now)
            if px is None:
                # delisted / no price -> treat as total loss (survivorship-honest)
                ret = -1.0
            else:
                ret = px / entry_px - 1.0
            s = self.stats.setdefault(cik, _Stat())
            s.n += 1
            s.sum_ret += ret
        self._pending = still_pending

    def score(self, cik: str) -> float:
        """Quality multiplier in ~[0.5, 2.0]. Managers with no track record
        yet get a neutral 1.0 (innocent until proven mediocre)."""
        s = self.stats.get(cik)
        if not s or s.n == 0:
            return 1.0
        avg = s.sum_ret / s.n
        # map average realized return to a bounded multiplier
        mult = 1.0 + max(-0.5, min(1.0, avg * 3.0))
        return max(0.5, min(2.0, mult))
