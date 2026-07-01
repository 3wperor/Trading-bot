"""Portfolio accounting: cash, positions, mark-to-market, costs, stops."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Position:
    ticker: str
    shares: float
    cost_basis: float          # avg price paid
    opened: str                # ISO date
    high_water: float          # highest close seen since open (for trailing stop)


@dataclass
class Portfolio:
    cash: float
    slippage_bps: float = 10.0
    commission_bps: float = 2.0
    positions: dict[str, Position] = field(default_factory=dict)

    # -- costs ------------------------------------------------------------
    def _buy_price(self, px: float) -> float:
        return px * (1 + self.slippage_bps / 1e4)

    def _sell_price(self, px: float) -> float:
        return px * (1 - self.slippage_bps / 1e4)

    def _commission(self, notional: float) -> float:
        return notional * self.commission_bps / 1e4

    # -- actions ----------------------------------------------------------
    def buy(self, ticker: str, target_value: float, px: float, date: str):
        price = self._buy_price(px)
        shares = target_value / price
        notional = shares * price
        cost = notional + self._commission(notional)
        if cost > self.cash or shares <= 0:
            return None
        self.cash -= cost
        if ticker in self.positions:
            p = self.positions[ticker]
            total = p.shares + shares
            p.cost_basis = (p.cost_basis * p.shares + price * shares) / total
            p.shares = total
        else:
            self.positions[ticker] = Position(ticker, shares, price, date, px)
        return {"side": "BUY", "ticker": ticker, "shares": shares,
                "price": price, "value": notional}

    def sell(self, ticker: str, px: float, date: str):
        if ticker not in self.positions:
            return None
        p = self.positions.pop(ticker)
        price = self._sell_price(px)
        notional = p.shares * price
        self.cash += notional - self._commission(notional)
        return {"side": "SELL", "ticker": ticker, "shares": p.shares,
                "price": price, "value": notional}

    # -- valuation --------------------------------------------------------
    def mark(self, price_lookup) -> tuple[float, float]:
        """Return (positions_value, total_equity) using a price_lookup(ticker)
        that yields the as-of close. Also updates trailing high-water marks."""
        pv = 0.0
        for p in self.positions.values():
            px = price_lookup(p.ticker)
            if px is None:
                px = p.cost_basis  # no fresh price: hold last known basis
            p.high_water = max(p.high_water, px)
            pv += p.shares * px
        return pv, self.cash + pv
