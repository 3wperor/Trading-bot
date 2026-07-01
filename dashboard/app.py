"""Interactive dashboard for the home server.

    streamlit run dashboard/app.py

Shows the live equity curve, current holdings, the latest whale-confluence
signals, and where the smart money is concentrating by sector — the same
data the replay produced, browsable interactively.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smartmoney.config import Config
from smartmoney.db import connect

st.set_page_config(page_title="SmartMoney", layout="wide")
cfg = Config.load()
conn = connect(cfg.db_path)

st.title("🐋 SmartMoney — following the whales")
st.caption("Point-in-time historical replay · no lookahead bias")

eq = pd.read_sql_query("SELECT * FROM equity_curve ORDER BY date", conn,
                       parse_dates=["date"])
signals = pd.read_sql_query("SELECT * FROM signals ORDER BY date DESC", conn,
                            parse_dates=["date"])
trades = pd.read_sql_query("SELECT * FROM trades ORDER BY date DESC", conn,
                           parse_dates=["date"])

if eq.empty:
    st.warning("No replay data yet. Run:  python -m smartmoney all")
    st.stop()

# scorecard
ret = eq["total"].iloc[-1] / eq["total"].iloc[0] - 1
roll = eq["total"].cummax()
maxdd = ((eq["total"] - roll) / roll).min()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Final equity", f"${eq['total'].iloc[-1]:,.0f}")
c2.metric("Total return", f"{ret*100:.1f}%")
c3.metric("Max drawdown", f"{maxdd*100:.1f}%")
c4.metric("Open positions", int(eq["n_positions"].iloc[-1]))

fig = go.Figure()
fig.add_trace(go.Scatter(x=eq["date"], y=eq["total"], name="Equity",
                         line=dict(color="#1f9d55", width=2)))
fig.update_layout(template="plotly_white", height=420, title="Equity curve")
st.plotly_chart(fig, use_container_width=True)

left, right = st.columns(2)
with left:
    st.subheader("Latest whale-confluence signals")
    st.dataframe(signals[signals["action"] == "BUY"].head(25),
                 use_container_width=True, hide_index=True)
with right:
    st.subheader("Recent trades")
    st.dataframe(trades.head(25), use_container_width=True, hide_index=True)

st.subheader("Where the smart money is concentrating")
flows = pd.read_sql_query(
    """SELECT f.period_of_report q, s.sector, SUM(h.value_usd) val
       FROM holdings h JOIN filings f ON f.accession=h.accession
       JOIN securities s ON s.cusip=h.cusip
       WHERE f.form_type LIKE '13F%' GROUP BY f.period_of_report, s.sector""",
    conn)
if not flows.empty:
    pivot = flows.pivot_table(index="sector", columns="q", values="val", fill_value=0)
    pivot = pivot.div(pivot.sum(axis=0), axis=1)
    st.plotly_chart(go.Figure(go.Heatmap(
        z=pivot.values, x=list(pivot.columns), y=list(pivot.index),
        colorscale="YlGnBu")).update_layout(template="plotly_white", height=420),
        use_container_width=True)
