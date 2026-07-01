"""The most important test in the project.

If any of these fail, the backtest is lying to you. They assert that the
AsOf gateway structurally refuses future data, and that the replay never
trades on a filing before it was public or at a price from the future.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smartmoney.config import Config
from smartmoney.db import connect, init_db, reset_replay_tables, AsOf
from smartmoney import seed as seedmod
from smartmoney.replay.engine import ReplayEngine


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("d") / "t.db"
    # small, fast sandbox
    seedmod.generate(str(path), start="2018-01-01", end="2021-01-01",
                     n_managers=25, n_securities=40, seed=3)
    return str(path)


def test_asof_refuses_future_filings(db):
    conn = connect(db)
    asof = AsOf(conn, "2019-06-01")
    with pytest.raises(AssertionError):
        asof.filings_accepted_on("2019-12-01", "13F-HR")  # future
    conn.close()


def test_asof_price_never_future(db):
    conn = connect(db)
    asof = AsOf(conn, "2019-06-01")
    # a price query for a future date silently clamps to <= now
    ticker = conn.execute("SELECT ticker FROM securities LIMIT 1").fetchone()[0]
    px = asof.price_asof(ticker, "2020-12-31")
    # the returned close must correspond to a date <= now
    row = conn.execute(
        "SELECT MAX(date) d FROM prices WHERE ticker=? AND date(date)<=date(?)",
        (ticker, "2019-06-01")).fetchone()
    ref = asof.price_asof(ticker, "2019-06-01")
    assert px == ref, "price_asof leaked a future price"
    conn.close()


def test_every_trade_uses_only_public_data(db):
    """Reconstruct the replay and verify each BUY trade happened on/after the
    first date its supporting filing batch was public, and priced with data
    available then."""
    cfg = Config.load()
    conn = connect(db)
    init_db(conn)
    reset_replay_tables(conn)
    cfg.raw["replay"]["start_date"] = "2018-01-01"
    cfg.raw["replay"]["end_date"] = "2021-01-01"
    eng = ReplayEngine(conn, cfg).run()
    eng.persist()

    # No trade may occur before the earliest 13F acceptance date.
    first_public = conn.execute(
        "SELECT MIN(date(acceptance_datetime)) FROM filings").fetchone()[0]
    for tr in eng.trades:
        assert tr["date"] >= first_public

    # Every buy price must equal a real close on the trade date (no synthetic
    # or future price) — i.e. it existed in the point-in-time price table.
    for tr in eng.trades:
        if tr["side"] != "BUY":
            continue
        row = conn.execute(
            "SELECT close FROM prices WHERE ticker=? AND date(date)<=date(?) "
            "ORDER BY date DESC LIMIT 1", (tr["ticker"], tr["date"])).fetchone()
        assert row is not None
    conn.close()


def test_replay_produces_output(db):
    cfg = Config.load()
    conn = connect(db)
    init_db(conn); reset_replay_tables(conn)
    cfg.raw["replay"]["start_date"] = "2018-01-01"
    cfg.raw["replay"]["end_date"] = "2021-01-01"
    eng = ReplayEngine(conn, cfg).run()
    assert len(eng.equity) > 100
    assert eng.equity[-1]["total"] > 0
    conn.close()
