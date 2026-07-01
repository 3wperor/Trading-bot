"""Build an interactive HTML report from replay outputs.

Renders: equity curve vs benchmark, drawdown, exposure, the signal
timeline (conviction over time, catalyst-flagged), a sector-concentration
heatmap of where the smart money is flowing, and a summary scorecard.
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .db import connect


def _load(conn):
    eq = pd.read_sql_query("SELECT * FROM equity_curve ORDER BY date", conn,
                           parse_dates=["date"])
    trades = pd.read_sql_query("SELECT * FROM trades ORDER BY date", conn,
                               parse_dates=["date"])
    signals = pd.read_sql_query("SELECT * FROM signals ORDER BY date", conn,
                                parse_dates=["date"])
    return eq, trades, signals


def _benchmark(conn, dates, start_cash):
    """Equal-weight buy-and-hold of every security — the 'just own the
    market' baseline the strategy must beat to justify itself."""
    px = pd.read_sql_query("SELECT ticker,date,close FROM prices", conn,
                           parse_dates=["date"])
    wide = px.pivot_table(index="date", columns="ticker", values="close")
    wide = wide.reindex(pd.to_datetime(dates)).ffill()
    first = wide.iloc[0]
    weights = start_cash / len(first.dropna()) / first
    bench = (wide * weights).sum(axis=1)
    return bench


def _metrics(eq: pd.DataFrame, trades: pd.DataFrame) -> dict:
    if eq.empty:
        return {}
    total = eq["total"]
    ret = total.iloc[-1] / total.iloc[0] - 1
    years = max((eq["date"].iloc[-1] - eq["date"].iloc[0]).days / 365.25, 1e-9)
    cagr = (total.iloc[-1] / total.iloc[0]) ** (1 / years) - 1
    daily = total.pct_change().dropna()
    sharpe = (daily.mean() / daily.std() * math.sqrt(252)) if daily.std() else 0
    roll_max = total.cummax()
    max_dd = ((total - roll_max) / roll_max).min()
    # round-trip win rate
    sells = trades[trades["side"] == "SELL"]
    wins = int((sells["reason"] == "max_holding").sum())  # rough proxy label
    return dict(total_return=ret, cagr=cagr, sharpe=sharpe, max_dd=max_dd,
                n_trades=len(trades), n_sells=len(sells))


def build(db_path: str, out_html: str, start_cash: float = 1_000_000.0) -> dict:
    conn = connect(db_path)
    eq, trades, signals = _load(conn)
    if eq.empty:
        raise SystemExit("No replay output found — run the replay first.")
    bench = _benchmark(conn, eq["date"], start_cash)
    m = _metrics(eq, trades)

    fig = make_subplots(
        rows=3, cols=2,
        specs=[[{"colspan": 2}, None],
               [{}, {}],
               [{"colspan": 2}, None]],
        subplot_titles=(
            "Equity curve — strategy vs. equal-weight benchmark",
            "Drawdown", "Market exposure (# positions)",
            "Signal timeline — conviction over time (♦ = catalyst-confirmed)"),
        vertical_spacing=0.09, row_heights=[0.4, 0.28, 0.32])

    # 1) equity vs benchmark
    fig.add_trace(go.Scatter(x=eq["date"], y=eq["total"], name="Strategy",
                             line=dict(color="#1f9d55", width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=eq["date"], y=bench.values, name="Benchmark",
                             line=dict(color="#888", width=1.5, dash="dot")),
                  row=1, col=1)

    # 2) drawdown
    roll_max = eq["total"].cummax()
    dd = (eq["total"] - roll_max) / roll_max
    fig.add_trace(go.Scatter(x=eq["date"], y=dd, name="Drawdown", fill="tozeroy",
                             line=dict(color="#c0392b")), row=2, col=1)

    # 3) exposure
    fig.add_trace(go.Scatter(x=eq["date"], y=eq["n_positions"], name="# positions",
                             line=dict(color="#2980b9")), row=2, col=2)

    # 4) signal timeline
    buys = signals[signals["action"] == "BUY"]
    plain = buys[buys["has_catalyst"] == 0]
    cat = buys[buys["has_catalyst"] == 1]
    fig.add_trace(go.Scatter(
        x=plain["date"], y=plain["conviction"], mode="markers", name="BUY signal",
        marker=dict(color="#1f9d55", size=6, opacity=0.6),
        text=plain["ticker"], hovertemplate="%{text}<br>%{x|%Y-%m-%d}<br>conv=%{y:.2f}"),
        row=3, col=1)
    fig.add_trace(go.Scatter(
        x=cat["date"], y=cat["conviction"], mode="markers", name="+ catalyst",
        marker=dict(color="#e67e22", size=10, symbol="diamond"),
        text=cat["ticker"], hovertemplate="%{text}<br>%{x|%Y-%m-%d}<br>conv=%{y:.2f} ♦"),
        row=3, col=1)

    subtitle = (f"Total {m['total_return']*100:.1f}% &nbsp;|&nbsp; "
                f"CAGR {m['cagr']*100:.1f}% &nbsp;|&nbsp; "
                f"Sharpe {m['sharpe']:.2f} &nbsp;|&nbsp; "
                f"MaxDD {m['max_dd']*100:.1f}% &nbsp;|&nbsp; "
                f"{m['n_trades']} trades")
    fig.update_layout(
        title=dict(text="SmartMoney — point-in-time historical replay<br>"
                        f"<span style='font-size:13px;color:#555'>{subtitle}</span>"),
        template="plotly_white", height=1050, hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02))

    Path(out_html).parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(out_html, include_plotlyjs="cdn")

    # sector heatmap as a second file (where the smart money concentrates)
    _sector_heatmap(conn, str(Path(out_html).with_name("sector_flows.html")))
    conn.close()
    return m


def _sector_heatmap(conn, out_html: str):
    """Quarter x sector: net new smart-money value, normalized per quarter."""
    q = pd.read_sql_query(
        """SELECT f.period_of_report AS q, s.sector AS sector,
                  SUM(h.value_usd) AS val
           FROM holdings h
           JOIN filings f ON f.accession=h.accession
           JOIN securities s ON s.cusip=h.cusip
           WHERE f.form_type LIKE '13F%'
           GROUP BY f.period_of_report, s.sector""", conn)
    if q.empty:
        return
    pivot = q.pivot_table(index="sector", columns="q", values="val", fill_value=0)
    pivot = pivot.div(pivot.sum(axis=0), axis=1)  # share of quarter's dollars
    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=list(pivot.columns), y=list(pivot.index),
        colorscale="YlGnBu", colorbar=dict(title="share")))
    fig.update_layout(
        title="Where the smart money is concentrating — sector share of "
              "disclosed 13F dollars by quarter",
        template="plotly_white", height=480)
    fig.write_html(out_html, include_plotlyjs="cdn")
