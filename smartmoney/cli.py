"""Command-line entry point.

    python -m smartmoney seed        # build the synthetic sandbox DB
    python -m smartmoney replay      # run the no-lookahead historical replay
    python -m smartmoney report      # build the interactive HTML report
    python -m smartmoney all         # seed + replay + report in one shot
"""
from __future__ import annotations

import argparse

from .config import Config
from .db import connect, init_db, reset_replay_tables
from .replay.engine import ReplayEngine
from . import seed as seedmod
from . import report as reportmod


def cmd_seed(cfg, args):
    stats = seedmod.generate(
        str(cfg.db_path),
        start=cfg["replay"]["start_date"], end=cfg["replay"]["end_date"],
        n_managers=cfg["universe"]["max_managers"])
    print("Seeded sandbox:", stats)


def cmd_replay(cfg, args):
    conn = connect(cfg.db_path)
    init_db(conn)
    reset_replay_tables(conn)
    print("Running point-in-time replay (no lookahead)...")
    eng = ReplayEngine(conn, cfg).run()
    eng.persist()
    final = eng.equity[-1]["total"] if eng.equity else 0
    print(f"Done. {len(eng.trades)} trades, {len(eng.signals)} signals, "
          f"final equity ${final:,.0f}")
    conn.close()


def cmd_report(cfg, args):
    out = args.out or "reports/replay.html"
    m = reportmod.build(str(cfg.db_path), out,
                        start_cash=cfg["strategy"]["starting_cash"])
    print(f"Report written to {out}")
    print("Metrics:", {k: round(v, 4) for k, v in m.items()})


def cmd_all(cfg, args):
    cmd_seed(cfg, args)
    cmd_replay(cfg, args)
    cmd_report(cfg, args)


def main(argv=None):
    p = argparse.ArgumentParser(prog="smartmoney")
    p.add_argument("--config", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed")
    sub.add_parser("replay")
    r = sub.add_parser("report")
    r.add_argument("--out", default=None)
    sub.add_parser("all")
    args = p.parse_args(argv)

    cfg = Config.load(args.config)
    {"seed": cmd_seed, "replay": cmd_replay,
     "report": cmd_report, "all": cmd_all}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
