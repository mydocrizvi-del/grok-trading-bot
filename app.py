import io
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
from config import Settings
from engine import validate_candles, indicators, backtest
from ledger import save_trades, load_trades

st.set_page_config(page_title="XAU/USD AI Scalping Desk", page_icon="🟡", layout="wide")
st.title("🟡 XAU/USD Scalping Desk")
st.caption("Paper-only research MVP · no broker connection · no live orders")

with st.sidebar:
    st.header("Risk & cost settings")
    starting_balance = st.number_input("Virtual balance ($)", min_value=100.0, value=10000.0, step=500.0)
    risk_pct = st.number_input("Risk per trade (%)", min_value=0.05, max_value=1.0, value=0.25, step=0.05)
    daily_cutoff_pct = st.number_input("Daily loss cutoff (%)", min_value=0.25, max_value=5.0, value=1.0, step=0.25)
    spread = st.number_input("Assumed spread ($/oz)", min_value=0.0, value=0.25, step=0.05)
    slippage = st.number_input("Slippage ($/oz)", min_value=0.0, value=0.05, step=0.01)
    max_spread = st.number_input("Maximum allowed spread ($/oz)", min_value=0.0, value=0.50, step=0.05)
    contract_oz = st.number_input("Ounces per standard lot (reference only)", min_value=1.0, value=100.0, step=1.0)
    st.caption("Contract size varies by provider. This simulator sizes in ounces and does not submit orders.")

s = Settings(starting_balance=starting_balance, risk_fraction=risk_pct/100,
             daily_loss_limit_fraction=daily_cutoff_pct/100,
             assumed_spread_usd_per_oz=spread, slippage_usd_per_oz=slippage,
             max_spread_usd_per_oz=max_spread, contract_ounces_per_lot=contract_oz)

uploaded = st.file_uploader("Upload historical XAU/USD M1 CSV", type=["csv"])
if uploaded:
    try:
        candles = validate_candles(pd.read_csv(uploaded))
        st.success(f"Loaded {len(candles):,} candles · {candles.timestamp.min()} to {candles.timestamp.max()}")
        with st.expander("Preview candles"):
            st.dataframe(candles.head(20), use_container_width=True)
        signals = indicators(candles, s)
        trades, metrics = backtest(candles, s)
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Paper trades", metrics["trades"])
        c2.metric("Net P&L", f"${metrics['net_pnl']:,.2f}")
        c3.metric("Win rate", f"{metrics['win_rate_pct']:.1f}%")
        c4.metric("Max drawdown", f"{metrics['max_drawdown_pct']:.2f}%")
        st.subheader("Price and signal context")
        fig = px.line(signals.tail(1500), x="timestamp", y="close", title="XAU/USD close (latest 1,500 rows)")
        st.plotly_chart(fig, use_container_width=True)
        st.subheader("Backtest trades")
        if not trades.empty:
            st.dataframe(trades, use_container_width=True)
            st.download_button("Download backtest ledger CSV", trades.to_csv(index=False).encode(), "xauusd_paper_trades.csv", "text/csv")
            if st.button("Save these trades to persistent SQLite ledger"):
                n = save_trades(trades)
                st.success(f"Saved {n} trades to data/paper_ledger.sqlite3")
        else:
            st.info("No trades met the rules in this dataset. Do not loosen rules just to force trades.")
    except Exception as e:
        st.error(f"Could not process CSV: {e}")
else:
    st.info("Upload real historical M1 candles to run a meaningful backtest. Synthetic examples are intentionally not treated as performance evidence.")
    if st.button("Load synthetic UI demo (not market data)"):
        rng = np.random.default_rng(7)
        n = 3000
        ts = pd.date_range("2026-01-05", periods=n, freq="min", tz="UTC")
        close = 2650 + np.cumsum(rng.normal(0, 0.65, n))
        op = np.r_[close[0], close[:-1]]
        high = np.maximum(op, close) + rng.uniform(0.02, 0.35, n)
        low = np.minimum(op, close) - rng.uniform(0.02, 0.35, n)
        demo = pd.DataFrame({"timestamp":ts, "open":op, "high":high, "low":low, "close":close})
        st.dataframe(indicators(demo, s).tail(20), use_container_width=True)
        st.warning("Synthetic data is only for checking the interface and is not suitable for assessing strategy performance.")

st.divider()
st.subheader("Persistent paper ledger")
ledger = load_trades()
if ledger.empty:
    st.caption("No trades saved yet.")
else:
    st.dataframe(ledger, use_container_width=True)
    st.download_button("Export saved ledger CSV", ledger.to_csv(index=False).encode(), "xauusd_saved_ledger.csv", "text/csv")

st.divider()
st.caption("Risk warning: this prototype is experimental. Backtests can be misleading due to data quality, fill assumptions, and overfitting. No live execution is included.")
