"""SQLite trade journal."""

import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT UNIQUE,
    instrument TEXT NOT NULL,
    direction TEXT NOT NULL,
    units REAL NOT NULL,
    entry_price REAL,
    sl REAL,
    tp REAL,
    strategy TEXT,
    opened_at TEXT,
    exit_price REAL,
    closed_at TEXT,
    pl REAL,
    status TEXT NOT NULL DEFAULT 'open'
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TradeLog:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(SCHEMA)
        self.conn.commit()

    def record_open(self, trade_id: str, instrument: str, direction: int,
                    units: float, entry_price: float, sl: float, tp: float,
                    strategy: str):
        self.conn.execute(
            """INSERT OR IGNORE INTO trades
               (trade_id, instrument, direction, units, entry_price, sl, tp,
                strategy, opened_at, status)
               VALUES (?,?,?,?,?,?,?,?,?, 'open')""",
            (trade_id, instrument, "long" if direction > 0 else "short",
             units, entry_price, sl, tp, strategy, _utcnow()))
        self.conn.commit()

    def record_close(self, trade_id: str, exit_price: float, pl: float,
                     closed_at: str | None = None) -> bool:
        cur = self.conn.execute(
            """UPDATE trades SET exit_price=?, pl=?, closed_at=?, status='closed'
               WHERE trade_id=? AND status='open'""",
            (exit_price, pl, closed_at or _utcnow(), trade_id))
        self.conn.commit()
        return cur.rowcount > 0

    def recent(self, limit: int = 10) -> list:
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE status='closed' "
            "ORDER BY closed_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        row = self.conn.execute(
            """SELECT COUNT(*) AS n,
                      COALESCE(SUM(pl), 0) AS total_pl,
                      SUM(CASE WHEN pl > 0 THEN 1 ELSE 0 END) AS wins,
                      COALESCE(SUM(CASE WHEN pl > 0 THEN pl ELSE 0 END), 0) AS gross_win,
                      COALESCE(SUM(CASE WHEN pl < 0 THEN -pl ELSE 0 END), 0) AS gross_loss
               FROM trades WHERE status='closed'""").fetchone()
        n = row["n"] or 0
        wins = row["wins"] or 0
        return {
            "trades": n,
            "wins": wins,
            "losses": n - wins,
            "win_rate": (wins / n * 100.0) if n else 0.0,
            "total_pl": row["total_pl"],
            "profit_factor": (row["gross_win"] / row["gross_loss"])
                             if row["gross_loss"] else None,
        }

    def close(self):
        self.conn.close()
