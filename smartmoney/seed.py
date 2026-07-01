"""Synthetic-but-realistic data generator.

This lets you exercise the ENTIRE pipeline (ingest schema -> analytics ->
no-lookahead replay -> visuals) with no network access, and it builds in a
*genuine but noisy* edge so the replay has something real to find:

  * Each security has a latent "quality" q. Higher q => higher expected
    drift (quality/momentum persistence — a real market phenomenon).
  * "Smart" managers tilt toward high-q names; "noise" managers pick at
    random. All managers disclose with a realistic ~45-day 13F lag.
  * Because quality persists, following clusters of smart managers captures
    forward drift that is still available AFTER the filing is public — so
    the edge is exploitable without any lookahead.
  * Plenty of volatility, some outright losers, and delistings keep it
    honest (trailing stops and survivorship actually matter).

It is a sandbox, not the market. Swap in real EDGAR data via
smartmoney.ingest.edgar for the real thing.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

from .db import connect, init_db

SECTORS = ["Tech", "Energy", "Health", "Financials", "Consumer",
           "Industrials", "Materials", "Utilities"]


def _business_days(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def _quarters(start: date, end: date):
    """Yield (period_end, acceptance_date) with a realistic ~45-day 13F lag."""
    y = start.year
    ends = []
    for yr in range(start.year, end.year + 1):
        for m, d in [(3, 31), (6, 30), (9, 30), (12, 31)]:
            pe = date(yr, m, d)
            if start <= pe <= end:
                ends.append(pe)
    for pe in ends:
        # filers use most of the 45-day window; jitter per filer added later
        yield pe, pe + timedelta(days=44)


def generate(db_path: str, start="2015-01-01", end="2025-01-01",
             n_managers=60, n_securities=120, seed=7):
    rng = random.Random(seed)
    start_d, end_d = date.fromisoformat(start), date.fromisoformat(end)

    conn = connect(db_path)
    init_db(conn)
    # fresh sandbox
    for t in ("holdings", "filings", "managers", "securities", "prices",
              "catalysts", "signals", "trades", "equity_curve"):
        conn.execute(f"DELETE FROM {t}")

    # ---- securities with latent quality -------------------------------
    securities = []
    for i in range(n_securities):
        q = rng.gauss(0, 1)
        ticker = f"S{i:03d}"
        cusip = f"{i:09d}"
        sector = SECTORS[i % len(SECTORS)]
        delisted = None
        # a few low-quality names delist partway through (survivorship!)
        if q < -1.3 and rng.random() < 0.5:
            delisted = (start_d + timedelta(days=rng.randint(400, 2500))).isoformat()
        securities.append(dict(ticker=ticker, cusip=cusip, sector=sector,
                               q=q, delisted=delisted))
    conn.executemany(
        "INSERT INTO securities(cusip,ticker,name,sector,delisted_date) VALUES(?,?,?,?,?)",
        [(s["cusip"], s["ticker"], s["ticker"], s["sector"], s["delisted"])
         for s in securities])

    # ---- daily price paths (quality -> drift) -------------------------
    px = {s["ticker"]: 20.0 + rng.random() * 180 for s in securities}
    days = list(_business_days(start_d, end_d))
    price_rows = []
    catalysts = []
    for s in securities:
        t = s["ticker"]
        # annualized drift rises with quality; daily vol ~2%
        mu = 0.04 + 0.10 * s["q"]
        sig = 0.02 + 0.005 * abs(s["q"])
        delist = date.fromisoformat(s["delisted"]) if s["delisted"] else None
        price = px[t]
        for d in days:
            if delist and d >= delist:
                break
            drift = mu / 252.0
            shock = rng.gauss(0, sig)
            price *= math.exp(drift - 0.5 * sig * sig + shock)
            price = max(0.5, price)
            price_rows.append((t, d.isoformat(), round(price, 4)))
            # high-quality names occasionally print an earnings beat catalyst
            if s["q"] > 0.6 and rng.random() < 0.0009:
                catalysts.append((t, d.isoformat(), "earnings_beat", "synthetic"))
    conn.executemany("INSERT INTO prices(ticker,date,close) VALUES(?,?,?)", price_rows)
    conn.executemany(
        "INSERT INTO catalysts(ticker,event_date,kind,detail) VALUES(?,?,?,?)", catalysts)

    # ---- managers -----------------------------------------------------
    managers = []
    for i in range(n_managers):
        smart = rng.random() < 0.45          # ~45% skilled, rest are noise
        skill = rng.uniform(0.5, 1.0) if smart else rng.uniform(0.0, 0.25)
        managers.append(dict(cik=f"{1000000 + i}", name=f"Whale {i:02d}",
                             smart=smart, skill=skill))
    conn.executemany(
        "INSERT INTO managers(cik,name,first_seen,active) VALUES(?,?,?,1)",
        [(m["cik"], m["name"], start) for m in managers])

    # ---- quarterly 13F filings & holdings -----------------------------
    by_q = list(_quarters(start_d, end_d))
    sec_by_q = sorted(securities, key=lambda s: s["q"], reverse=True)
    filing_rows = []
    holding_rows = []
    for pe, base_accept in by_q:
        for m in managers:
            # each manager files with its own jitter inside the 45-day window
            accept = base_accept + timedelta(days=rng.randint(-3, 1))
            accept = min(accept, end_d)
            accession = f"{m['cik']}-{pe.isoformat()}"
            filing_rows.append((accession, m["cik"], "13F-HR", pe.isoformat(),
                                accept.isoformat() + "T18:00:00", accept.isoformat()))
            # skilled managers pick from the top-quality pool (with noise);
            # noise managers pick at random.
            k = rng.randint(18, 30)
            if m["smart"]:
                pool_size = int(len(sec_by_q) * (0.15 + 0.5 * (1 - m["skill"])))
                pool = sec_by_q[:max(k, pool_size)]
            else:
                pool = securities
            picks = rng.sample(pool, min(k, len(pool)))
            # weight the book; skilled managers concentrate more
            for s in picks:
                # skip names already delisted by the period end
                if s["delisted"] and pe.isoformat() >= s["delisted"]:
                    continue
                p = _price_on(conn, s["ticker"], pe.isoformat())
                if p is None:
                    continue
                conc = rng.uniform(1, 5) * (2.0 if m["smart"] else 1.0)
                shares = round(conc * 1_000_00 / p)
                value = shares * p
                holding_rows.append((accession, s["cusip"], float(shares),
                                     float(value), None))
    conn.executemany(
        """INSERT INTO filings(accession,cik,form_type,period_of_report,
           acceptance_datetime,filed_date) VALUES(?,?,?,?,?,?)""", filing_rows)
    conn.executemany(
        """INSERT INTO holdings(accession,cusip,shares,value_usd,put_call)
           VALUES(?,?,?,?,?)""", holding_rows)
    conn.commit()

    stats = dict(
        securities=len(securities), managers=len(managers),
        prices=len(price_rows), filings=len(filing_rows),
        holdings=len(holding_rows), catalysts=len(catalysts),
        delisted=sum(1 for s in securities if s["delisted"]),
        smart=sum(1 for m in managers if m["smart"]),
    )
    conn.close()
    return stats


def _price_on(conn, ticker, d):
    row = conn.execute(
        "SELECT close FROM prices WHERE ticker=? AND date(date)<=date(?) "
        "ORDER BY date DESC LIMIT 1", (ticker, d)).fetchone()
    return float(row["close"]) if row else None
