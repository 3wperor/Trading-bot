"""Entry point: wires up config, OANDA, Telegram and the engine."""

import asyncio
import logging
import signal
import sys

from .config import load_settings
from .engine import Engine
from .oanda import OandaClient
from .storage import TradeLog
from .telegram import TelegramBot


async def amain() -> int:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    oanda = OandaClient(settings.oanda_token, settings.oanda_account_id,
                        settings.oanda_base_url)
    trade_log = TradeLog(settings.db_path)
    telegram = TelegramBot(settings.telegram_token, settings.telegram_chat_id)
    engine = Engine(settings, oanda, telegram, trade_log)
    telegram.engine = engine

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    try:
        await asyncio.gather(engine.run(stop), telegram.run(stop))
    finally:
        await telegram.close()
        await oanda.close()
        trade_log.close()
    return 0


def main():
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
