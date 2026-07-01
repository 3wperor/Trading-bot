"""Event-driven historical replay — strictly no lookahead.

The engine walks the calendar forward one trading day at a time. On each
day it:
  1. executes buys decided on the PREVIOUS day (fill at day+1 price, never
     the quarter-end price),
  2. resolves matured manager picks to update point-in-time quality,
  3. if new 13Fs became public today, forms confluence/conviction signals,
  4. applies exits (trailing stop / max holding),
  5. marks the book to market and records the equity curve.

Every data read goes through an AsOf pinned to `today`, so the engine
literally cannot see a filing that wasn't public or a price from the future.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..db import AsOf, trading_days, all_dates_with_filings
from ..analytics.conviction import Confluence, Vote, score as conviction_score
from ..analytics.manager_quality import ManagerQuality
from .portfolio import Portfolio


@dataclass
class PendingBuy:
    ticker: str
    conviction: float
    reason: str


class ReplayEngine:
    def __init__(self, conn, cfg):
        self.conn = conn
        self.cfg = cfg
        s = cfg["strategy"]
        self.pf = Portfolio(
            cash=s["starting_cash"],
            slippage_bps=s["slippage_bps"],
            commission_bps=s["commission_bps"],
        )
        self.mq = ManagerQuality()
        self.pending: list[PendingBuy] = []
        self.signals: list[dict] = []
        self.trades: list[dict] = []
        self.equity: list[dict] = []
        # cusip->ticker cache
        self._tick = {r["cusip"]: r["ticker"] for r in
                      conn.execute("SELECT cusip, ticker FROM securities").fetchall()}

    # ------------------------------------------------------------------
    def run(self):
        start = self.cfg["replay"]["start_date"]
        end = self.cfg["replay"]["end_date"]
        days = trading_days(self.conn, start, end)
        filing_days = set(all_dates_with_filings(self.conn, start, end))

        for today in days:
            asof = AsOf(self.conn, today)
            self._execute_pending(asof, today)
            self.mq.resolve_due(today, asof.price_asof,
                                horizon_days=126)
            if today in filing_days:
                self._process_filings(asof, today)
            self._apply_exits(asof, today)
            self._mark(asof, today)

        return self

    # ------------------------------------------------------------------
    def _execute_pending(self, asof: AsOf, today: str):
        """Fill yesterday's decisions at today's price (honest day+1 fill)."""
        if not self.pending:
            return
        s = self.cfg["strategy"]
        _, equity = self.pf.mark(lambda t: asof.price_asof(t, today))
        target = s["max_weight_per_position"] * equity
        still = []
        for pb in self.pending:
            if len(self.pf.positions) >= s["max_positions"]:
                break
            px = asof.price_asof(pb.ticker, today)
            if px is None:
                continue
            tr = self.pf.buy(pb.ticker, target, px, today)
            if tr:
                tr.update(date=today, reason=pb.reason)
                self.trades.append(tr)
                # register the pick for point-in-time quality scoring
                for cik in getattr(pb, "ciks", []):
                    self.mq.record_pick(cik, pb.ticker, tr["price"], today)
        self.pending = still  # decisions are one-shot; unfilled are dropped

    # ------------------------------------------------------------------
    def _process_filings(self, asof: AsOf, today: str):
        cfg = self.cfg
        # gather every 13F newly public today, build per-ticker confluence
        confl: dict[str, Confluence] = {}
        pick_ciks: dict[str, set[str]] = {}
        for form in ("13F-HR", "13F-HR/A"):
            for f in asof.filings_accepted_on(today, form):
                rows = asof.holdings_for(f["accession"])
                aum = sum((r["value_usd"] or 0) for r in rows) or 1.0
                for r in rows:
                    if r["put_call"]:
                        continue  # ignore options notional for now
                    ticker = self._tick.get(r["cusip"])
                    if not ticker:
                        continue
                    prior = asof.prior_holdings(f["cik"], r["cusip"], today)
                    if prior is None:
                        ptype = "new"
                    elif (r["shares"] or 0) > (prior["shares"] or 0) * 1.05:
                        ptype = "add"
                    else:
                        ptype = "hold"
                    q = self.mq.score(f["cik"])
                    pw = (r["value_usd"] or 0) / aum
                    confl.setdefault(ticker, Confluence(ticker)).votes.append(
                        Vote(f["cik"], ptype, pw, q))
                    pick_ciks.setdefault(ticker, set()).add(f["cik"])

        s = cfg["strategy"]
        for ticker, c in confl.items():
            if c.n_managers < s["min_managers_for_confluence"]:
                continue
            has_cat = asof.catalyst_between(ticker, _shift(today, -90), today)
            sc = conviction_score(cfg, c, has_cat)
            if sc < s["conviction_threshold"]:
                continue
            if ticker in self.pf.positions:
                continue
            self.signals.append(dict(
                date=today, ticker=ticker, conviction=round(sc, 3),
                n_managers=c.n_managers, has_catalyst=int(has_cat),
                action="BUY",
                reason=f"{c.n_managers} whales, score={sc:.2f}"
                       + (", catalyst" if has_cat else "")))
            pb = PendingBuy(ticker, sc, self.signals[-1]["reason"])
            pb.ciks = list(pick_ciks.get(ticker, []))  # type: ignore[attr-defined]
            self.pending.append(pb)

    # ------------------------------------------------------------------
    def _apply_exits(self, asof: AsOf, today: str):
        s = self.cfg["strategy"]
        for ticker in list(self.pf.positions):
            p = self.pf.positions[ticker]
            px = asof.price_asof(ticker, today)
            if px is None:
                continue
            held = (date.fromisoformat(today) - date.fromisoformat(p.opened)).days
            trailing = px <= p.high_water * (1 - s["trailing_stop_pct"])
            aged = held >= s["max_holding_days"]
            if trailing or aged:
                tr = self.pf.sell(ticker, px, today)
                if tr:
                    reason = "trailing_stop" if trailing else "max_holding"
                    tr.update(date=today, reason=reason)
                    self.trades.append(tr)
                    self.signals.append(dict(
                        date=today, ticker=ticker, conviction=0.0,
                        n_managers=0, has_catalyst=0, action="SELL",
                        reason=reason))

    # ------------------------------------------------------------------
    def _mark(self, asof: AsOf, today: str):
        pv, total = self.pf.mark(lambda t: asof.price_asof(t, today))
        self.equity.append(dict(date=today, cash=self.pf.cash,
                                positions_val=pv, total=total,
                                n_positions=len(self.pf.positions)))

    # ------------------------------------------------------------------
    def persist(self):
        cur = self.conn.cursor()
        cur.executemany(
            """INSERT INTO signals(date,ticker,conviction,n_managers,
               has_catalyst,action,reason) VALUES(?,?,?,?,?,?,?)""",
            [(x["date"], x["ticker"], x["conviction"], x["n_managers"],
              x["has_catalyst"], x["action"], x["reason"]) for x in self.signals])
        cur.executemany(
            """INSERT INTO trades(date,ticker,side,shares,price,value,reason)
               VALUES(?,?,?,?,?,?,?)""",
            [(x["date"], x["ticker"], x["side"], x["shares"], x["price"],
              x["value"], x["reason"]) for x in self.trades])
        cur.executemany(
            """INSERT INTO equity_curve(date,cash,positions_val,total,n_positions)
               VALUES(?,?,?,?,?)""",
            [(x["date"], x["cash"], x["positions_val"], x["total"],
              x["n_positions"]) for x in self.equity])
        self.conn.commit()


def _shift(iso: str, days: int) -> str:
    from datetime import timedelta
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()
