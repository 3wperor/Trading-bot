#!/usr/bin/env python3
"""Backtest the configured strategies on historical OANDA candles.

Usage:
    python backtest.py                     # all configured pairs
    python backtest.py EUR_USD --count 5000 --granularity M15

Simulates the same logic the live engine uses: enter on a combined signal at
the next candle's open with ATR-based SL/TP, exit on SL/TP or opposite signal.
Results are in R-multiples (1R = the amount risked per trade), so they apply
at any account size. Spreads/slippage are NOT modelled — treat results as an
upper bound.
"""

import argparse
import asyncio
import sys

from forexbot import indicators as ta
from forexbot.config import load_settings
from forexbot.oanda import OandaClient
from forexbot.strategies import Candles, build_combiner


def window(c: Candles, start: int, end: int) -> Candles:
    return Candles(c.times[start:end], c.opens[start:end], c.highs[start:end],
                   c.lows[start:end], c.closes[start:end])


def run_backtest(candles: Candles, combiner, sl_mult: float, tp_mult: float,
                 lookback: int = 250, exit_on_opposite: bool = True) -> list:
    atr_all = ta.atr(candles.highs, candles.lows, candles.closes, 14)
    trades = []
    position = None  # dict(direction, entry, sl, tp, entry_index)
    start = max(combiner.min_candles(), 60)

    for i in range(start, len(candles) - 1):
        # Check open position against this candle's range first.
        if position:
            hi, lo = candles.highs[i], candles.lows[i]
            d = position["direction"]
            hit_sl = lo <= position["sl"] if d > 0 else hi >= position["sl"]
            hit_tp = hi >= position["tp"] if d > 0 else lo <= position["tp"]
            exit_price = None
            if hit_sl:  # conservative: assume SL fills before TP in same candle
                exit_price = position["sl"]
            elif hit_tp:
                exit_price = position["tp"]
            if exit_price is not None:
                risk = abs(position["entry"] - position["sl"])
                r = (exit_price - position["entry"]) * d / risk
                trades.append({"r": r, "bars": i - position["entry_index"]})
                position = None

        sig = combiner.evaluate(window(candles, max(0, i - lookback), i + 1))

        if position and exit_on_opposite and sig.direction == -position["direction"]:
            d = position["direction"]
            risk = abs(position["entry"] - position["sl"])
            r = (candles.closes[i] - position["entry"]) * d / risk
            trades.append({"r": r, "bars": i - position["entry_index"]})
            position = None

        if position is None and sig.direction != 0 and atr_all[i]:
            entry = candles.opens[i + 1]
            sl = entry - sig.direction * atr_all[i] * sl_mult
            tp = entry + sig.direction * atr_all[i] * tp_mult
            position = {"direction": sig.direction, "entry": entry,
                        "sl": sl, "tp": tp, "entry_index": i + 1}
    return trades


def report(pair: str, trades: list, risk_pct: float):
    if not trades:
        print(f"{pair}: no trades generated")
        return 0.0
    wins = [t for t in trades if t["r"] > 0]
    total_r = sum(t["r"] for t in trades)
    gross_win = sum(t["r"] for t in wins)
    gross_loss = -sum(t["r"] for t in trades if t["r"] <= 0)
    pf = gross_win / gross_loss if gross_loss else float("inf")
    print(f"{pair}: {len(trades)} trades | win rate {len(wins) / len(trades) * 100:.0f}% | "
          f"profit factor {pf:.2f} | net {total_r:+.1f}R "
          f"(≈ {total_r * risk_pct:+.1f}% at {risk_pct}% risk/trade)")
    return total_r


async def amain():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pairs", nargs="*", help="pairs to test (default: config.yaml pairs)")
    parser.add_argument("--count", type=int, default=5000, help="candles to fetch (max 5000)")
    parser.add_argument("--granularity", default=None, help="e.g. M15, H1 (default: config)")
    args = parser.parse_args()

    settings = load_settings()
    pairs = args.pairs or settings.pairs
    granularity = args.granularity or settings.granularity
    oanda = OandaClient(settings.oanda_token, settings.oanda_account_id,
                        settings.oanda_base_url)
    combiner = build_combiner(settings.strategies, settings.combine_mode)

    print(f"Backtesting {', '.join(pairs)} @ {granularity}, "
          f"{args.count} candles, strategies: "
          f"{', '.join(s.name for s in combiner.strategies)} "
          f"(mode: {combiner.mode})\n")
    try:
        total = 0.0
        for pair in pairs:
            candles = await oanda.candles(pair, granularity, min(args.count, 5000))
            total += report(pair, run_backtest(
                candles, combiner, settings.sl_atr_mult, settings.tp_atr_mult,
                exit_on_opposite=settings.exit_on_opposite),
                settings.risk_per_trade_pct)
        print(f"\nPortfolio net: {total:+.1f}R "
              f"(≈ {total * settings.risk_per_trade_pct:+.1f}% of equity at "
              f"{settings.risk_per_trade_pct}% risk/trade)")
        print("Note: spreads and slippage are not modelled; live results will be lower.")
    finally:
        await oanda.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
