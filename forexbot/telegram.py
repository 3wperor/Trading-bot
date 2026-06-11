"""Telegram notifications and bot commands via the Bot API (long polling)."""

import asyncio
import html
import logging

import httpx

log = logging.getLogger("telegram")

HELP_TEXT = """<b>Forex bot commands</b>
/status — bot state, mode, open positions, equity
/balance — account balance and margin
/positions — open positions with unrealized P/L
/trades — last 10 closed trades
/performance — win rate, profit factor, total P/L
/pause — stop opening new trades (existing positions keep their SL/TP)
/resume — resume trading
/closeall — close all open positions now
/help — this message"""


class TelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.chat_id = str(chat_id) if chat_id else ""
        self._client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}",
            timeout=httpx.Timeout(70.0))
        self.engine = None  # set after Engine is constructed

    async def close(self):
        await self._client.aclose()

    async def send(self, text: str, chat_id: str | None = None):
        target = chat_id or self.chat_id
        if not target:
            log.info("telegram (no chat configured): %s", text)
            return
        try:
            resp = await self._client.post("/sendMessage", json={
                "chat_id": target, "text": text, "parse_mode": "HTML",
                "disable_web_page_preview": True})
            if resp.status_code >= 400:
                log.error("telegram send failed: %s %s", resp.status_code, resp.text[:200])
        except httpx.HTTPError as exc:
            log.error("telegram send failed: %s", exc)

    async def run(self, stop: asyncio.Event):
        offset = 0
        while not stop.is_set():
            try:
                resp = await self._client.get("/getUpdates", params={
                    "timeout": 50, "offset": offset,
                    "allowed_updates": '["message"]'})
                resp.raise_for_status()
                for update in resp.json().get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message") or {}
                    text = (msg.get("text") or "").strip()
                    chat = str(msg.get("chat", {}).get("id", ""))
                    if text:
                        await self._dispatch(text, chat)
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("telegram poll error: %s", exc)
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise

    async def _dispatch(self, text: str, chat: str):
        cmd = text.split()[0].split("@")[0].lower()
        if not self.chat_id:
            # Not yet configured: help the user find their chat id.
            await self.send(
                f"Your chat id is <code>{chat}</code>.\n"
                "Put it in .env as TELEGRAM_CHAT_ID and restart the bot.",
                chat_id=chat)
            return
        if chat != self.chat_id:
            log.warning("ignoring command from unauthorized chat %s", chat)
            return
        if self.engine is None:
            await self.send("Bot is still starting up, try again shortly.")
            return

        handlers = {
            "/start": self._help, "/help": self._help,
            "/status": self._status, "/balance": self._balance,
            "/positions": self._positions, "/trades": self._trades,
            "/performance": self._performance,
            "/pause": self._pause, "/resume": self._resume,
            "/closeall": self._closeall,
        }
        handler = handlers.get(cmd)
        if handler is None:
            await self.send("Unknown command. /help for the list.")
            return
        try:
            await handler()
        except Exception as exc:
            log.exception("command %s failed", cmd)
            await self.send(f"⚠️ Command failed: {html.escape(str(exc)[:300])}")

    # -- command handlers -----------------------------------------------------

    async def _help(self):
        await self.send(HELP_TEXT)

    async def _status(self):
        await self.send(await self.engine.status_text())

    async def _balance(self):
        await self.send(await self.engine.balance_text())

    async def _positions(self):
        await self.send(await self.engine.positions_text())

    async def _trades(self):
        rows = self.engine.trade_log.recent(10)
        if not rows:
            await self.send("No closed trades yet.")
            return
        lines = ["<b>Last closed trades</b>"]
        for r in rows:
            emoji = "🟢" if (r["pl"] or 0) >= 0 else "🔴"
            lines.append(
                f"{emoji} {r['instrument']} {r['direction']} "
                f"{abs(r['units']):.0f}u @ {r['entry_price']} → "
                f"{r['exit_price']}  P/L {r['pl']:+.2f}")
        await self.send("\n".join(lines))

    async def _performance(self):
        st = self.engine.trade_log.stats()
        pf = f"{st['profit_factor']:.2f}" if st["profit_factor"] else "n/a"
        await self.send(
            f"<b>Performance</b>\n"
            f"Closed trades: {st['trades']} "
            f"({st['wins']}W / {st['losses']}L, {st['win_rate']:.0f}%)\n"
            f"Total P/L: {st['total_pl']:+.2f}\n"
            f"Profit factor: {pf}")

    async def _pause(self):
        self.engine.paused = True
        await self.send("⏸ Trading paused. Open positions keep their SL/TP. /resume to continue.")

    async def _resume(self):
        self.engine.paused = False
        self.engine.daily_halt = False
        await self.send("▶️ Trading resumed.")

    async def _closeall(self):
        closed = await self.engine.close_all()
        if closed:
            await self.send(f"Closed positions: {', '.join(closed)}")
        else:
            await self.send("No open positions to close.")
