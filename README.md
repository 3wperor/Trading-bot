# 🐋 SmartMoney

Follow the whales. Scan SEC filings to find where top investors are
concentrating capital, cross-check those moves against catalysts, and
**test the whole idea with a strictly point-in-time historical replay that
has no lookahead bias** — so the backtest can't lie to you.

This is a research/education tool, **not** investment advice. Nothing here
is foolproof; markets aren't. The goal is a system that is *honest* and
*robust*.

---

## The idea in three steps

1. **Scan** — pull position data from the top investors (13F filings), plus
   faster signals (Form 4 insider buys, 13D activist stakes).
2. **Map** — find the stocks and sectors where *multiple high-quality*
   whales are concentrating at once (confluence + conviction scoring).
3. **Cross-check** — confirm those moves against catalysts (earnings beats,
   buybacks, activist filings) to separate real conviction from noise.

## Why the replay is the centerpiece

Instead of paper-trading forward for a year, we replay 10+ years of history
**as it actually unfolded**. The key discipline:

- Every filing carries an **`acceptance_datetime`** — the instant it became
  *public* on EDGAR (not the quarter it describes, which can be 45 days
  older).
- All data access during the replay goes through an **`AsOf` gateway**
  (`smartmoney/db.py`) pinned to the simulated "today". It *structurally
  refuses* to return any filing or price from the future.
- Buys fill at **filing-day + 1** prices, never the quarter-end price.
- The universe is **survivorship-complete**: managers that blew up and
  stocks that delisted keep their real history.

Because no-lookahead is a property of the code (not a hope), the tests in
`tests/test_no_lookahead.py` can assert it directly. **If those tests ever
fail, the backtest is lying to you.**

## Quick start

```bash
pip install -r requirements.txt

# Build the synthetic sandbox, run the replay, build the report — one shot:
python -m smartmoney all

# Or step by step:
python -m smartmoney seed      # build sandbox DB (no network needed)
python -m smartmoney replay    # run the no-lookahead historical replay
python -m smartmoney report    # write reports/replay.html

# Interactive dashboard for the home server:
streamlit run dashboard/app.py

# The test that keeps you honest:
pytest tests/
```

Open `reports/replay.html` for the equity curve vs. an equal-weight
benchmark, drawdown, exposure, and the signal timeline;
`reports/sector_flows.html` for the smart-money sector heatmap.

## Sandbox vs. real data

Out of the box the project runs on a **synthetic sandbox** (`smartmoney/seed.py`)
so you can see everything work with no network access. It builds in a
*genuine but noisy* edge (quality/momentum persistence) so the replay has
something real to find.

For real data, run the ingestion at home:

- `smartmoney/ingest/edgar.py` — SEC EDGAR client (rate-limited, captures
  `acceptance_datetime`), daily-index poller, and a 13F info-table parser.
- `smartmoney/ingest/cusip.py` — CUSIP → ticker via the free **OpenFIGI**
  API (the licensed CUSIP master isn't needed).

You'll also need a point-in-time price/corporate-actions source to populate
the `prices` table.

## Architecture

```
ingest/  → EDGAR + OpenFIGI               (fast layer: Form4/13D/8-K;
                                            slow layer: quarterly 13F)
db.py    → SQLite, point-in-time schema   (AsOf gateway = no-lookahead)
analytics/
  manager_quality.py → rank whales by realized, point-in-time track record
  conviction.py      → confluence + conviction + catalyst multiplier
replay/  → event-driven engine + portfolio/PnL/stops
report.py + dashboard/ → visuals
```

## Config

All strategy knobs live in `config.yaml` — deliberately few, to limit
overfitting. Notable ones: `min_managers_for_confluence`,
`conviction_threshold`, `catalyst_multiplier`, `max_weight_per_position`,
`trailing_stop_pct`, `max_holding_days`.

## Roadmap

- [x] Point-in-time storage + AsOf gateway
- [x] Confluence / conviction / catalyst scoring
- [x] No-lookahead historical replay + visuals + tests
- [ ] Wire live EDGAR ingestion end-to-end (fast + slow layers)
- [ ] Real price/corporate-actions feed
- [ ] Walk-forward split (tune on early years, validate untouched on later)
- [ ] Forward paper-trading harness + Telegram alerts
- [ ] (Only after validation) optional broker execution with hard risk limits

## A word on "foolproof"

No market system is foolproof. What this project engineers is a system that
**doesn't fool you**: point-in-time honesty, survivorship completeness,
realistic fills/costs, few tunable knobs, and a benchmark it must beat. Do
your own research; risk only what you can afford to lose.
