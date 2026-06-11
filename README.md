# Forex Trading Bot (OANDA + Telegram)

A self-hosted forex trading bot for Ubuntu. It scans currency pairs on every
completed candle, trades two combinable strategies with strict risk management,
and reports everything to you on Telegram — including commands to check status,
pause it, or close everything from your phone.

> ⚠️ **Risk warning.** No trading bot can guarantee profit — most retail forex
> traders lose money, and leveraged forex can lose money quickly. This bot
> manages risk (small position sizes, stop-losses, a daily loss circuit
> breaker), but it cannot remove market risk. **Run it on an OANDA practice
> account first** (`OANDA_ENV=practice` — same prices, fake money), backtest
> with `backtest.py`, and only go live with money you can afford to lose.

## What it does

- **Scans** EUR/USD, GBP/USD, USD/JPY, AUD/USD (configurable) on M15 candles.
- **Two strategies, combined by vote:**
  - *Trend-following* — EMA 20/50 crossover, confirmed by RSI, filtered by ADX
    so it only trades when a real trend exists.
  - *Mean-reversion* — Bollinger Band + RSI extremes, only in ranging markets
    (low ADX). The two are regime-complementary; conflicting signals cancel.
- **Risk management** — every trade has a stop-loss (2×ATR) and take-profit
  (3×ATR) attached *at the broker* (they execute even if your server dies).
  Position size targets 0.5% of equity per losing trade. Max 3 open trades,
  3% daily-loss circuit breaker, wide-spread filter.
- **Telegram** — notifies you on every buy, sell, and close (with P/L), plus a
  daily summary. Commands: `/status` `/balance` `/positions` `/trades`
  `/performance` `/pause` `/resume` `/closeall` `/help`.
- **Trade journal** in SQLite (`trades.db`).
- **systemd service** so it starts on boot and restarts on failure.

## Setup (fresh Ubuntu server)

### 1. Accounts and tokens (10 minutes)

**OANDA** (broker — best Linux support, free practice accounts):
1. Sign up at [oanda.com](https://www.oanda.com) and create an **fxTrade
   Practice** account (and later a live account when ready).
2. Log in → *My Services* → *Manage API Access* → generate a **personal access
   token**.
3. Find your **account ID** (looks like `101-001-1234567-001`) on the account
   page. Note: practice and live have *different* tokens and account IDs.

**Telegram bot:**
1. Message [@BotFather](https://t.me/botfather), send `/newbot`, pick a name —
   it gives you a **bot token**.
2. Your **chat id** comes later, in step 3 below.

### 2. Install

```bash
sudo apt update && sudo apt install -y python3-venv git
git clone https://github.com/3wperor/Trading-bot.git
cd Trading-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env        # paste your OANDA token, account id, and Telegram bot token
```

Keep `OANDA_ENV=practice` for now and leave `TELEGRAM_CHAT_ID` empty.

### 3. First run & Telegram pairing

```bash
.venv/bin/python -m forexbot.main
```

Open Telegram, send `/start` to your bot — it replies with your **chat id**.
Stop the bot (Ctrl-C), put the id in `.env` as `TELEGRAM_CHAT_ID`, and start it
again. You should get a "Forex bot started" message. From now on only that
chat can control the bot.

### 4. Backtest before trusting it

```bash
.venv/bin/python backtest.py                      # configured pairs
.venv/bin/python backtest.py EUR_USD --granularity H1 --count 5000
```

Results are in R-multiples (1R = the amount risked per trade). Spread and
slippage are not modelled, so expect live results to be worse. Tune
`config.yaml` (strategy params, `combine_mode: agree` for fewer/stronger
signals) and re-run.

### 5. Run permanently (systemd)

```bash
# Edit deploy/forexbot.service first if your user/paths differ
sudo cp deploy/forexbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now forexbot
journalctl -u forexbot -f        # live logs
```

### 6. Going live (after the practice week)

1. Create an OANDA **live** account and fund it.
2. Generate a live API token and get the live account ID.
3. In `.env`: set the live `OANDA_TOKEN`, `OANDA_ACCOUNT_ID`, and
   `OANDA_ENV=live`.
4. `sudo systemctl restart forexbot` — the startup message will show 🔴 LIVE.

Recommended for the first live weeks: keep `risk_per_trade_pct` at 0.5% or
lower, and watch `/performance` daily.

## Configuration

All trading parameters live in [`config.yaml`](config.yaml) (pairs, timeframe,
risk limits, strategy params, voting mode) — comments inline. Secrets live in
`.env`. Restart the bot after changes.

## Telegram commands

| Command | What it does |
|---|---|
| `/status` | Mode, paused/active, equity, open position count |
| `/balance` | Balance, equity, margin |
| `/positions` | Open positions with SL/TP and unrealized P/L |
| `/trades` | Last 10 closed trades |
| `/performance` | Win rate, profit factor, total P/L |
| `/pause` / `/resume` | Stop/resume opening new trades (SL/TP stay active) |
| `/closeall` | Close every open position immediately |

## How it trades (one cycle)

Every 30 s the bot checks each pair for a newly completed candle. When one
appears it computes EMA/RSI/ADX/Bollinger/ATR over the last 250 candles, asks
both strategies for a signal, and combines them. A trade is opened only if:
no position is already open on that pair, fewer than `max_open_trades` are
open overall, the spread is acceptable, the daily loss limit hasn't tripped,
and trading isn't paused. Orders go in as market orders with stop-loss and
take-profit attached, so exits are enforced server-side at OANDA. SL/TP hits
are detected via the transactions API and reported to Telegram with P/L.
