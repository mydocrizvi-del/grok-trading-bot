from __future__ import annotations
import pandas as pd
import numpy as np
from config import Settings

REQUIRED = {"timestamp", "open", "high", "low", "close"}

def validate_candles(df: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    for c in ["open", "high", "low", "close"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    if "spread" in out:
        out["spread"] = pd.to_numeric(out["spread"], errors="coerce")
    else:
        out["spread"] = np.nan
    out = out.dropna(subset=["timestamp", "open", "high", "low", "close"])
    out = out.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    if (out[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive.")
    if (out["high"] < out[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("Invalid candles: high below an OHLC value.")
    if (out["low"] > out[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("Invalid candles: low above an OHLC value.")
    return out

def indicators(df: pd.DataFrame, s: Settings) -> pd.DataFrame:
    x = validate_candles(df)
    x["ema_fast"] = x["close"].ewm(span=s.fast_ema, adjust=False).mean()
    x["ema_slow"] = x["close"].ewm(span=s.slow_ema, adjust=False).mean()
    prev_close = x["close"].shift(1)
    tr = pd.concat([
        x["high"] - x["low"],
        (x["high"] - prev_close).abs(),
        (x["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    x["atr"] = tr.rolling(s.atr_period, min_periods=s.atr_period).mean()

    # Resample completed M1 candles into M5; map each completed M5 trend
    # back to the next M1 bar, never to a bar inside the unfinished M5 candle.
    m5 = (x.set_index("timestamp")[["open", "high", "low", "close"]]
          .resample("5min", label="right", closed="right")
          .agg({"open":"first", "high":"max", "low":"min", "close":"last"})
          .dropna())
    m5["trend_ema"] = m5["close"].ewm(span=s.trend_ema, adjust=False).mean()
    m5["trend_up"] = (m5["close"] > m5["trend_ema"]) & (m5["trend_ema"] > m5["trend_ema"].shift(1))
    m5["trend_down"] = (m5["close"] < m5["trend_ema"]) & (m5["trend_ema"] < m5["trend_ema"].shift(1))
    # A right-labelled 5-min bar is only known at its timestamp.
    x = x.merge(m5[["trend_up", "trend_down"]], left_on="timestamp", right_index=True, how="left")
    x[["trend_up", "trend_down"]] = x[["trend_up", "trend_down"]].ffill().fillna(False)
    x["long_cross"] = (x["ema_fast"] > x["ema_slow"]) & (x["ema_fast"].shift(1) <= x["ema_slow"].shift(1))
    x["short_cross"] = (x["ema_fast"] < x["ema_slow"]) & (x["ema_fast"].shift(1) >= x["ema_slow"].shift(1))
    x["signal"] = "WAIT"
    x.loc[x["long_cross"] & x["trend_up"], "signal"] = "BUY"
    x.loc[x["short_cross"] & x["trend_down"], "signal"] = "SELL"
    return x

def backtest(df: pd.DataFrame, s: Settings) -> tuple[pd.DataFrame, dict]:
    x = indicators(df, s)
    balance = s.starting_balance
    day_realized = {}
    trades = []
    position = None
    # Enter at next candle open, costs are modeled conservatively in USD/oz.
    for i in range(1, len(x)):
        row = x.iloc[i]
        prev = x.iloc[i-1]
        day = row["timestamp"].date().isoformat()
        day_realized.setdefault(day, 0.0)

        if position:
            exit_price, reason = None, None
            if position["side"] == "BUY":
                if row["low"] <= position["stop"]:
                    exit_price, reason = position["stop"], "STOP"
                elif row["high"] >= position["target"]:
                    exit_price, reason = position["target"], "TARGET"
            else:
                if row["high"] >= position["stop"]:
                    exit_price, reason = position["stop"], "STOP"
                elif row["low"] <= position["target"]:
                    exit_price, reason = position["target"], "TARGET"
            if exit_price is not None:
                raw = (exit_price - position["entry"]) * position["ounces"] * (1 if position["side"] == "BUY" else -1)
                costs = (position["spread"] + s.slippage_usd_per_oz) * position["ounces"]
                pnl = raw - costs
                balance += pnl
                day_realized[day] += pnl
                trades.append({**position, "exit_time": row["timestamp"], "exit": exit_price,
                               "reason": reason, "gross_pnl": raw, "costs": costs, "pnl": pnl,
                               "balance_after": balance})
                position = None

        if position is None and day_realized[day] > -s.starting_balance*s.daily_loss_limit_fraction:
            sig = prev["signal"]
            atr = prev["atr"]
            if sig in ("BUY", "SELL") and pd.notna(atr) and atr > 0:
                spread = prev["spread"] if pd.notna(prev["spread"]) else s.assumed_spread_usd_per_oz
                if spread <= s.max_spread_usd_per_oz:
                    entry = float(row["open"])
                    side = sig
                    stop_dist = float(atr) * s.stop_atr_multiple
                    stop = entry - stop_dist if side == "BUY" else entry + stop_dist
                    target = entry + stop_dist*s.reward_risk if side == "BUY" else entry - stop_dist*s.reward_risk
                    risk_cash = s.starting_balance*s.risk_fraction
                    # Position sized in ounces, not broker lots. P&L model is USD/oz.
                    ounces = risk_cash / stop_dist
                    # Keep entry costs from being silently ignored; stop sizing is gross-risk based.
                    position = {"entry_time": row["timestamp"], "side": side, "entry": entry,
                                "stop": stop, "target": target, "ounces": ounces,
                                "lots_equivalent": ounces / s.contract_ounces_per_lot,
                                "spread": float(spread), "atr": float(atr)}
    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        metrics = {"trades": 0, "ending_balance": balance, "net_pnl": 0.0,
                   "win_rate_pct": 0.0, "max_drawdown_pct": 0.0, "profit_factor": 0.0}
    else:
        eq = trades_df["balance_after"]
        peaks = eq.cummax().clip(lower=s.starting_balance)
        dd = (peaks - eq) / peaks
        wins = trades_df.loc[trades_df.pnl > 0, "pnl"].sum()
        losses = -trades_df.loc[trades_df.pnl < 0, "pnl"].sum()
        metrics = {"trades": int(len(trades_df)), "ending_balance": float(balance),
                   "net_pnl": float(trades_df.pnl.sum()),
                   "win_rate_pct": float((trades_df.pnl > 0).mean()*100),
                   "max_drawdown_pct": float(dd.max()*100),
                   "profit_factor": float(wins/losses) if losses else float("inf") if wins else 0.0}
    return trades_df, metrics
