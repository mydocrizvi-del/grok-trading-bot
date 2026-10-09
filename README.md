# XAU/USD AI Scalping Desk — paper-only MVP

This is a local-first Python prototype for research and paper trading. It has **no broker integration and no live-order capability**.

## Features
- M1 entries with M5 trend confirmation
- Configurable paper balance ($10,000 default), 0.25% risk/trade, 1% daily loss cutoff
- Spread and slippage cost assumptions
- Stop/target simulation and SQLite paper ledger
- Streamlit dashboard and CSV candle import
- Historical backtest over imported OHLC data

## Important limitations
- Strategy is a baseline hypothesis, not a proven edge.
- The CSV must contain timestamp, open, high, low, close; optional `spread` column is in **USD price units** (e.g. 0.25 means $0.25 per oz). Timestamps should be UTC or consistently timezone-aware.
- For credible XAU/USD costs, replace assumed spread/slippage with broker-independent market data or clearly documented executable bid/ask data. Candle-only data cannot reconstruct true fills.
- Contract size is configurable. Default 100 troy ounces per standard lot is a common CFD convention, **not universal**. This app simulates ounces directly and does not submit orders.
- Historical data and simulated P&L are not evidence of future performance.

## Run locally (free)
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Input data
Upload an M1 CSV with columns:
`timestamp,open,high,low,close`
Optional: `spread` (USD/oz). A sample synthetic CSV can be generated in the app for a UI smoke test; synthetic data is not market data and must not be used to judge profitability.

## Strategy v0.1
- M5 trend: close above EMA(20) and EMA(20) rising => long bias; below and falling => short bias.
- M1 entry: EMA(9)/EMA(21) crossover in the same direction.
- Initial stop: 1.2 × ATR(14); target: 1.5R.
- Skip when spread exceeds configured max; pause new entries after daily realized loss reaches 1%.
- Simulated risk budget: 0.25% of starting paper balance per entry.
- Signal uses completed bars only; execution is modeled on the next bar open to reduce look-ahead bias.

## Compliance note for Indian residents
This tool only simulates prices. It does not connect to a broker or route orders. Before any real-money activity, verify the exact product and route against current RBI/FEMA rules and the RBI lists of authorised persons/ETPs; do not assume an offshore leveraged XAU/USD CFD is permitted. Seek qualified legal/compliance advice. This is not legal or financial advice.
