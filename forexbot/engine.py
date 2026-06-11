"""Main trading engine: scans pairs on each completed candle, applies the
strategy combiner, sizes positions, places orders and reports via Telegram."""

import asyncio
import logging
from datetime import datetime, timezone

from . import indicators as ta
from .oanda import OandaError
from .risk import RiskManager
from .strategies import build_combiner

log = logging.getLogger("engine")


class Engine:
    def __init__(self, settings, oanda, telegram, trade_log):
        self.s = settings
        self.oanda = oanda
        self.tg = telegram
        self.trade_log = trade_log
        self.risk = RiskManager(settings, oanda)
        self.combiner = build_combiner(settings.strategies, settings.combine_mode)

        self.paused = False
        self.daily_halt = False
        self.started_at = datetime.now(timezone.utc)
        self.last_candle_time: dict = {}      # pair -> ISO time of last evaluated candle
        self.trade_meta: dict = {}            # trade_id -> strategy reason (for closes)
        self.account_currency = ""
        self.day_start_balance = 0.0
        self.current_day = None
        self.last_tx_id = "0"

    # -- lifecycle ------------------------------------------------------------

    async def run(self, stop: asyncio.Event):
        await self._startup()
        while not stop.is_set():
            try:
                await self._tick()
            except OandaError as exc:
                log.error("tick failed: %s", exc)
            except Exception:
                log.exception("unexpected error in tick")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.s.poll_interval)
            except asyncio.TimeoutError:
                pass
        await self.tg.send("🛑 Bot shutting down. Open positions keep their SL/TP.")

    async def _startup(self):
        account = await self.oanda.account_summary()
        self.account_currency = account["currency"]
        self.day_start_balance = float(account["balance"])
        self.current_day = datetime.now(timezone.utc).date()
        self.last_tx_id = account["lastTransactionID"]
        await self.oanda.load_instruments(self.s.pairs)

        mode = "🔴 LIVE" if self.s.oanda_env == "live" else "🟡 PRACTICE"
        names = ", ".join(st.name for st in self.combiner.strategies)
        await self.tg.send(
            f"🤖 <b>Forex bot started</b> ({mode})\n"
            f"Pairs: {', '.join(self.s.pairs)} @ {self.s.granularity}\n"
            f"Strategies: {names} (mode: {self.combiner.mode})\n"
            f"Risk: {self.s.risk_per_trade_pct}%/trade, "
            f"max {self.s.max_open_trades} open, "
            f"daily stop {self.s.max_daily_loss_pct}%\n"
            f"Balance: {self.day_start_balance:.2f} {self.account_currency}")
        log.info("started: %s account, balance %.2f %s",
                 self.s.oanda_env, self.day_start_balance, self.account_currency)

    # -- main loop ------------------------------------------------------------

    async def _tick(self):
        await self._rollover_day_if_needed()
        await self._process_account_changes()

        account = await self.oanda.account_summary()
        equity = float(account["NAV"])
        margin_available = float(account["marginAvailable"])

        if self.risk.daily_loss_exceeded(equity, self.day_start_balance):
            if not self.daily_halt:
                self.daily_halt = True
                await self.tg.send(
                    f"🛑 <b>Daily loss limit hit</b> "
                    f"({self.s.max_daily_loss_pct}%). No new trades until "
                    f"tomorrow (UTC) or /resume.")
            return

        positions = await self.oanda.open_positions()
        prices = await self.oanda.pricing(self.s.pairs)

        for pair in self.s.pairs:
            try:
                await self._process_pair(pair, positions, prices, equity, margin_available)
            except OandaError as exc:
                log.error("%s: %s", pair, exc)

    async def _process_pair(self, pair, positions, prices, equity, margin_available):
        candles = await self.oanda.candles(pair, self.s.granularity, self.s.candle_count)
        if len(candles) < self.combiner.min_candles():
            return
        latest = candles.times[-1]
        if self.last_candle_time.get(pair) == latest:
            return  # no new completed candle yet
        first_seen = pair not in self.last_candle_time
        self.last_candle_time[pair] = latest
        if first_seen:
            return  # don't trade a stale signal right after startup

        signal = self.combiner.evaluate(candles)
        pos = positions.get(pair)

        # Exit on a confirmed opposite signal.
        if pos and self.s.exit_on_opposite and signal.direction == -pos["direction"]:
            await self.oanda.close_position(pair)
            await self.tg.send(
                f"🔁 <b>Closed {pair}</b> ({'long' if pos['direction'] > 0 else 'short'}) "
                f"on opposite signal.\n{signal.reason}")
            log.info("%s: closed on opposite signal", pair)
            return

        if signal.direction == 0 or pos:
            return
        if self.paused or self.daily_halt:
            return
        if len(positions) >= self.s.max_open_trades:
            log.info("%s: signal skipped, max open trades reached", pair)
            return

        price_info = prices.get(pair)
        if not price_info or not price_info["tradeable"]:
            log.info("%s: not tradeable right now", pair)
            return
        if not self.risk.spread_ok(pair, price_info["spread"]):
            log.info("%s: spread too wide (%.1f pips), skipping", pair,
                     price_info["spread"] / self.oanda.pip_size(pair))
            return

        await self._enter(pair, signal, candles, price_info, equity, margin_available)

    async def _enter(self, pair, signal, candles, price_info, equity, margin_available):
        atr_values = ta.atr(candles.highs, candles.lows, candles.closes, 14)
        atr_value = atr_values[-1]
        if not atr_value:
            return
        entry_est = price_info["ask"] if signal.direction > 0 else price_info["bid"]
        sl, tp = self.risk.stops_for(signal.direction, entry_est, atr_value,
                                     self.s.sl_atr_mult, self.s.tp_atr_mult)
        units = await self.risk.position_size(
            pair, equity, self.account_currency,
            abs(entry_est - sl), margin_available, entry_est)
        if units <= 0:
            return

        fill = await self.oanda.market_order(pair, units * signal.direction, sl, tp)
        fill_price = float(fill.get("price", entry_est))
        trade_id = (fill.get("tradeOpened") or {}).get("tradeID", fill.get("id", ""))
        strategy_names = ", ".join(s.strategy for s in signal.votes)
        self.trade_meta[trade_id] = strategy_names
        self.trade_log.record_open(trade_id, pair, signal.direction, units,
                                   fill_price, sl, tp, strategy_names)

        side = "BUY 🟢" if signal.direction > 0 else "SELL 🔴"
        await self.tg.send(
            f"<b>{side} {pair}</b>\n"
            f"{units:,} units @ {self.oanda.fmt_price(pair, fill_price)}\n"
            f"SL {self.oanda.fmt_price(pair, sl)} | "
            f"TP {self.oanda.fmt_price(pair, tp)}\n"
            f"Signal — {signal.reason}")
        log.info("%s: %s %d units @ %.5f (SL %.5f, TP %.5f)",
                 pair, side.split()[0], units, fill_price, sl, tp)

    # -- account change tracking (SL/TP hits etc.) -----------------------------

    async def _process_account_changes(self):
        data = await self.oanda.changes(self.last_tx_id)
        self.last_tx_id = data.get("lastTransactionID", self.last_tx_id)
        for trade in data.get("changes", {}).get("tradesClosed", []):
            trade_id = trade.get("id", "")
            pl = float(trade.get("realizedPL", 0))
            exit_price = float(trade.get("averageClosePrice", 0) or 0)
            was_ours = self.trade_log.record_close(trade_id, exit_price, pl,
                                                   trade.get("closeTime"))
            emoji = "✅" if pl >= 0 else "❌"
            strategy = self.trade_meta.pop(trade_id, "")
            via = f" ({strategy})" if strategy else ""
            await self.tg.send(
                f"{emoji} <b>Closed {trade.get('instrument', '?')}</b>{via}\n"
                f"Exit @ {exit_price}\n"
                f"P/L: {pl:+.2f} {self.account_currency}")
            log.info("trade %s closed, P/L %.2f (tracked=%s)", trade_id, pl, was_ours)

    async def _rollover_day_if_needed(self):
        today = datetime.now(timezone.utc).date()
        if today == self.current_day:
            return
        st = self.trade_log.stats()
        account = await self.oanda.account_summary()
        balance = float(account["balance"])
        await self.tg.send(
            f"📅 <b>Daily rollover</b> ({self.current_day} UTC)\n"
            f"Balance: {balance:.2f} {self.account_currency} "
            f"({balance - self.day_start_balance:+.2f} on the day)\n"
            f"All-time: {st['trades']} trades, {st['win_rate']:.0f}% wins, "
            f"P/L {st['total_pl']:+.2f}")
        self.current_day = today
        self.day_start_balance = balance
        self.daily_halt = False

    # -- used by Telegram commands ---------------------------------------------

    async def close_all(self) -> list:
        positions = await self.oanda.open_positions()
        closed = []
        for pair in positions:
            await self.oanda.close_position(pair)
            closed.append(pair)
        return closed

    async def status_text(self) -> str:
        account = await self.oanda.account_summary()
        positions = await self.oanda.open_positions()
        uptime = datetime.now(timezone.utc) - self.started_at
        hours, rem = divmod(int(uptime.total_seconds()), 3600)
        state = ("🛑 daily loss halt" if self.daily_halt
                 else "⏸ paused" if self.paused else "▶️ trading")
        mode = "🔴 LIVE" if self.s.oanda_env == "live" else "🟡 PRACTICE"
        return (f"<b>Status</b> {state} ({mode})\n"
                f"Uptime: {hours}h {rem // 60}m\n"
                f"Equity: {float(account['NAV']):.2f} {self.account_currency}\n"
                f"Open positions: {len(positions)}/{self.s.max_open_trades}\n"
                f"Unrealized P/L: {float(account['unrealizedPL']):+.2f}\n"
                f"Pairs: {', '.join(self.s.pairs)} @ {self.s.granularity}")

    async def balance_text(self) -> str:
        a = await self.oanda.account_summary()
        return (f"<b>Account</b>\n"
                f"Balance: {float(a['balance']):.2f} {a['currency']}\n"
                f"Equity (NAV): {float(a['NAV']):.2f}\n"
                f"Unrealized P/L: {float(a['unrealizedPL']):+.2f}\n"
                f"Margin used: {float(a['marginUsed']):.2f}\n"
                f"Margin available: {float(a['marginAvailable']):.2f}")

    async def positions_text(self) -> str:
        trades = await self.oanda.open_trades()
        if not trades:
            return "No open positions."
        lines = ["<b>Open positions</b>"]
        for t in trades:
            units = float(t["currentUnits"])
            side = "long" if units > 0 else "short"
            pl = float(t.get("unrealizedPL", 0))
            emoji = "🟢" if pl >= 0 else "🔴"
            sl = (t.get("stopLossOrder") or {}).get("price", "—")
            tp = (t.get("takeProfitOrder") or {}).get("price", "—")
            lines.append(
                f"{emoji} {t['instrument']} {side} {abs(units):,.0f}u "
                f"@ {t['price']}\n   SL {sl} | TP {tp} | P/L {pl:+.2f}")
        return "\n".join(lines)
