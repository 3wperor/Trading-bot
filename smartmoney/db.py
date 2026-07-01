"""SQLite storage + the point-in-time AsOf gateway.

SQLite is deliberate: zero-config, single-file, perfect for a home server.
The schema is Postgres-portable if you outgrow it.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text())
    conn.commit()


def reset_replay_tables(conn: sqlite3.Connection) -> None:
    """Clear only the rebuildable replay outputs; keep raw data."""
    for t in ("signals", "trades", "equity_curve"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()


class AsOf:
    """The no-lookahead gateway.

    Every data read the replay engine performs goes through an AsOf pinned
    to the simulated current date. It is structurally impossible to read a
    filing that wasn't public yet, or a price from the future, because the
    date filter is baked into every query here — the engine never touches
    the raw tables directly.
    """

    def __init__(self, conn: sqlite3.Connection, now: str):
        self.conn = conn
        self.now = now  # ISO date string, the simulated "today"

    # -- filings visible as of `now` --------------------------------------
    def filings_accepted_on(self, date: str, form_type: str) -> list[sqlite3.Row]:
        """Filings that BECAME PUBLIC exactly on `date` (must be <= now)."""
        assert date <= self.now, "AsOf violation: asked for a future date"
        return self.conn.execute(
            """SELECT * FROM filings
               WHERE date(acceptance_datetime) = date(?)
                 AND form_type = ?
                 AND date(acceptance_datetime) <= date(?)""",
            (date, form_type, self.now),
        ).fetchall()

    def holdings_for(self, accession: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM holdings WHERE accession = ?", (accession,)
        ).fetchall()

    def prior_holdings(self, cik: str, cusip: str, before: str):
        """A manager's most recent PUBLIC position in a name before `before`
        — used to tell a new position from an add. Gated on acceptance."""
        assert before <= self.now, "AsOf violation"
        return self.conn.execute(
            """SELECT h.shares FROM holdings h
               JOIN filings f ON f.accession = h.accession
               WHERE f.cik = ? AND h.cusip = ? AND f.form_type LIKE '13F%'
                 AND date(f.acceptance_datetime) < date(?)
                 AND date(f.acceptance_datetime) <= date(?)
               ORDER BY f.acceptance_datetime DESC LIMIT 1""",
            (cik, cusip, before, self.now),
        ).fetchone()

    # -- prices, strictly no future ---------------------------------------
    def price_asof(self, ticker: str, date: str) -> float | None:
        """Latest close on or before `date` (and never after `now`)."""
        d = min(date, self.now)
        row = self.conn.execute(
            """SELECT close FROM prices
               WHERE ticker = ? AND date(date) <= date(?)
               ORDER BY date DESC LIMIT 1""",
            (ticker, d),
        ).fetchone()
        return float(row["close"]) if row else None

    def next_price_after(self, ticker: str, date: str) -> tuple[str, float] | None:
        """First close STRICTLY after `date` but not after `now`
        (used to fill a buy at filing day + 1, honestly)."""
        row = self.conn.execute(
            """SELECT date, close FROM prices
               WHERE ticker = ? AND date(date) > date(?) AND date(date) <= date(?)
               ORDER BY date ASC LIMIT 1""",
            (ticker, date, self.now),
        ).fetchone()
        return (row["date"], float(row["close"])) if row else None

    def catalyst_between(self, ticker: str, start: str, end: str) -> bool:
        end = min(end, self.now)
        row = self.conn.execute(
            """SELECT 1 FROM catalysts
               WHERE ticker = ? AND date(event_date) BETWEEN date(?) AND date(?)
               LIMIT 1""",
            (ticker, start, end),
        ).fetchone()
        return row is not None

    def sector_of(self, ticker: str) -> str | None:
        row = self.conn.execute(
            "SELECT sector FROM securities WHERE ticker = ?", (ticker,)
        ).fetchone()
        return row["sector"] if row else None


def all_dates_with_filings(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    """Distinct calendar dates on which SOMETHING became public — the event
    clock for the replay (we also step every trading day for marking)."""
    rows = conn.execute(
        """SELECT DISTINCT date(acceptance_datetime) d FROM filings
           WHERE date(acceptance_datetime) BETWEEN date(?) AND date(?)
           ORDER BY d""",
        (start, end),
    ).fetchall()
    return [r["d"] for r in rows]


def trading_days(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT date FROM prices
           WHERE date(date) BETWEEN date(?) AND date(?) ORDER BY date""",
        (start, end),
    ).fetchall()
    return [r["date"] for r in rows]
