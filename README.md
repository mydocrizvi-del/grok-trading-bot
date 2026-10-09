# XAU/USD AI Scalping Desk — paper-only MVP

Local-first Python prototype for research and paper trading. It has **no broker integration and no live-order capability**.

## Features
- M1 entries with M5 trend confirmation
- Configurable paper balance, risk fraction and daily realized-loss cutoff
- Spread/slippage assumptions, stop/target simulation and SQLite paper ledger
- Streamlit dashboard, CSV candle import and historical backtests

## Historical data rules
Upload M1 candles with `timestamp,open,high,low,close`. Optional `spread` is in USD price units (e.g. 0.25 means $0.25 per ounce). Timestamps are parsed as UTC and must be unique, increasing and valid. OHLC values must be finite, positive and internally consistent. If a spread column is provided, every spread must be finite and non-negative. Omit the column to use the configured spread assumption.

The app cannot certify data source, timezone convention, candle completeness or executable prices. Verify source metadata and inspect missing-minute gaps. OHLC cannot tell which level was hit first when stop and target are both touched in the same candle; this baseline assumes the stop was hit first.

## Run locally on Windows
```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

## Strategy v0.1
- M5 trend uses close relative to EMA(20) plus EMA slope.
- M1 entry uses EMA(9)/EMA(21) crossover with matching trend.
- Initial stop: 1.2 × ATR(14); target: 1.5R.
- Skip excessive spreads and pause new entries once daily realized P&L breaches its cutoff relative to that UTC day's opening balance.
- Positions are simulated in ounces, not actual broker lots. Costs are estimated, not guaranteed.
- Entry is modelled on the next candle open. Positions remaining at the end of the dataset are closed at the last close for accounting.

## Limitations
This is a strategy hypothesis, not a proven edge or investment recommendation. Reliable research requires documented bid/ask data, cost sensitivity checks and out-of-sample validation. No broker connection or live order execution is implemented.

## India compliance note
Before any real-money XAU/USD activity, verify the exact product and route against current RBI/FEMA rules and relevant authorised-person/ETP lists. Do not assume an offshore leveraged XAU/USD CFD is permitted. Consider qualified legal/compliance advice. This is not legal or financial advice.
