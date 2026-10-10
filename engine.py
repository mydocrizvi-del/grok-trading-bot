from __future__ import annotations

import numpy as np
import pandas as pd

from config import Settings

REQUIRED = {"timestamp", "open", "high", "low", "close"}
PRICE_COLUMNS = ["open", "high", "low", "close"]


def validate_candles(df: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize minute candles without silently hiding bad rows."""
    missing = REQUIRED - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")
    if df.empty:
        raise ValueError("The CSV contains no candle rows.")

    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    for col in PRICE_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    if "spread" in out.columns:
        out["spread"] = pd.to_numeric(out["spread"], errors="coerce")
        bad_spread = out["spread"].isna() | ~np.isfinite(out["spread"]) | (out["spread"] < 0)
        if bad_spread.any():
            raise ValueError("Spread must be present, finite, and non-negative for every row; remove the column to use the configured assumption.")
    # Keep the column absent when the source has no spread; downstream code
    # then uses the configured assumption. Do not synthesize NaNs here because
    # indicators() validates the normalized frame again.
    
    invalid = out[["timestamp", *PRICE_COLUMNS]].isna().any(axis=1)
    if invalid.any():
        raise ValueError(f"{int(invalid.sum())} row(s) contain invalid timestamps or OHLC values.")
    if not np.isfinite(out[PRICE_COLUMNS].to_numpy(dtype=float)).all():
        raise ValueError("OHLC values must be finite numbers.")

    if out["timestamp"].duplicated().any():
        raise ValueError("Duplicate timestamps detected. De-duplicate the source CSV before backtesting.")
    if not out["timestamp"].is_monotonic_increasing:
        raise ValueError("Timestamps must be strictly increasing; sort the source CSV first.")
    if (out[PRICE_COLUMNS] <= 0).any().any():
        raise ValueError("OHLC prices must be positive.")
    if (out["high"] < out[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("Invalid candles: high is below another OHLC value.")
    if (out["low"] > out[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("Invalid candles: low is above another OHLC value.")
    return out.reset_index(drop=True)


def indicators(df: pd.DataFrame, s: Settings) -> pd.DataFrame:
    x = validate_candles(df)
    x["ema_fast"] = x["close"].ewm(span=s.fast_ema, adjust=False).mean()
    x["ema_slow"] = x["close"].ewm(span=s.slow_ema, adjust=False).mean()

    prev_close = x["close"].shift(1)
    true_range = pd.concat([
        x["high"] - x["low"],
        (x["high"] - prev_close).abs(),
        (x["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    x["atr"] = true_range.rolling(s.atr_period, min_periods=s.atr_period).mean()

    # Source timestamps are assumed to mark M1 candle OPEN times.
    # A candle stamped 00:04 covers [00:04, 00:05), so the completed M5
    # bucket ending at 00:05 must contain stamps 00:00 through 00:04 only.
    # left-closed/right-labelled buckets prevent the 00:05 candle from
    # leaking into the M5 trend available at 00:05.
    m5 = (
        x.set_index("timestamp")[PRICE_COLUMNS]
        .resample("5min", label="right", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    m5["trend_ema"] = m5["close"].ewm(span=s.trend_ema, adjust=False).mean()
    m5["trend_up"] = (m5["close"] > m5["trend_ema"]) & (m5["trend_ema"] > m5["trend_ema"].shift(1))
    m5["trend_down"] = (m5["close"] < m5["trend_ema"]) & (m5["trend_ema"] < m5["trend_ema"].shift(1))
    x = x.merge(m5[["trend_up", "trend_down"]], left_on="timestamp", right_index=True, how="left")
    x[["trend_up", "trend_down"]] = x[["trend_up", "trend_down"]].ffill().fillna(False)

    x["long_cross"] = (x["ema_fast"] > x["ema_slow"]) & (x["ema_fast"].shift(1) <= x["ema_slow"].shift(1))
    x["short_cross"] = (x["ema_fast"] < x["ema_slow"]) & (x["ema_fast"].shift(1) >= x["ema_slow"].shift(1))
    x["signal"] = "WAIT"
    x.loc[x["long_cross"] & x["trend_up"], "signal"] = "BUY"
    x.loc[x["short_cross"] & x["trend_down"], "signal"] = "SELL"
    return x


def backtest(df: pd.DataFrame, s: Settings) -> tuple[pd.DataFrame, dict]:
    """Conservative candle-based simulator; not a tick-accurate fill model."""
    if s.starting_balance <= 0:
        raise ValueError("Starting balance must be positive.")
    if not 0 < s.risk_fraction <= 0.01:
        raise ValueError("Risk per trade must be > 0 and <= 1%.")
    if not 0 < s.daily_loss_limit_fraction <= 0.05:
        raise ValueError("Daily loss cutoff must be > 0 and <= 5%.")
    if s.slippage_usd_per_oz < 0 or s.assumed_spread_usd_per_oz < 0 or s.max_spread_usd_per_oz < 0:
        raise ValueError("Spread and slippage settings cannot be negative.")
    if s.stop_atr_multiple <= 0 or s.reward_risk <= 0:
        raise ValueError("Stop ATR multiple and reward/risk must be positive.")

    x = indicators(df, s)
    balance = float(s.starting_balance)
    daily_opening_balance: dict[str, float] = {}
    daily_realized: dict[str, float] = {}
    trades: list[dict] = []
    position = None

    def close_position(row, exit_price: float, reason: str, day: str):
        nonlocal balance, position
        sign = 1 if position["side"] == "BUY" else -1
        gross = (exit_price - position["entry"]) * position["ounces"] * sign
        costs = ((position["entry_spread"] + position["spread"]) / 2
                 + 2 * s.slippage_usd_per_oz) * position["ounces"]
        pnl = gross - costs
        balance += pnl
        daily_realized[day] = daily_realized.get(day, 0.0) + pnl
        trades.append({
            **position, "exit_time": row["timestamp"], "exit": float(exit_price),
            "reason": reason, "gross_pnl": float(gross), "costs": float(costs),
            "pnl": float(pnl), "balance_after": float(balance),
        })
        position = None

    for i in range(1, len(x)):
        row, prev = x.iloc[i], x.iloc[i - 1]
        day = row["timestamp"].date().isoformat()
        daily_opening_balance.setdefault(day, balance)
        daily_realized.setdefault(day, 0.0)

        # This baseline closes at the first bar of a new UTC date instead of
        # silently carrying an overnight position across daily risk boundaries.
        if position and position["entry_time"].date().isoformat() != day:
            close_position(row, float(row["open"]), "DAY_END", day)

        if position:
            if position["side"] == "BUY":
                hit_stop, hit_target = row["low"] <= position["stop"], row["high"] >= position["target"]
            else:
                hit_stop, hit_target = row["high"] >= position["stop"], row["low"] <= position["target"]

            # Without tick data, assume stop first if both levels are touched.
            if hit_stop:
                if position["side"] == "BUY":
                    fill = min(float(position["stop"]), float(row["open"]))
                else:
                    fill = max(float(position["stop"]), float(row["open"]))
                close_position(row, fill, "STOP", day)
            elif hit_target:
                if position["side"] == "BUY":
                    fill = max(float(position["target"]), float(row["open"]))
                else:
                    fill = min(float(position["target"]), float(row["open"]))
                close_position(row, fill, "TARGET", day)

        day_limit = daily_opening_balance[day] * s.daily_loss_limit_fraction
        cutoff_hit = daily_realized[day] <= -day_limit
        if position is None and not cutoff_hit:
            sig, atr = prev["signal"], prev["atr"]
            if sig in ("BUY", "SELL") and pd.notna(atr) and atr > 0:
                spread_value = prev.get("spread", np.nan)
                spread = spread_value if pd.notna(spread_value) else s.assumed_spread_usd_per_oz
                if np.isfinite(spread) and 0 <= spread <= s.max_spread_usd_per_oz:
                    entry = float(row["open"])
                    side = str(sig)
                    stop_dist = float(atr) * s.stop_atr_multiple
                    stop = entry - stop_dist if side == "BUY" else entry + stop_dist
                    target = entry + stop_dist * s.reward_risk if side == "BUY" else entry - stop_dist * s.reward_risk
                    # Use the day opening balance and reserve an estimated cost allowance.
                    risk_cash = daily_opening_balance[day] * s.risk_fraction
                    cost_allowance = spread + 2 * s.slippage_usd_per_oz
                    ounces = risk_cash / (stop_dist + cost_allowance)
                    if ounces > 0 and np.isfinite(ounces):
                        position = {
                            "entry_time": row["timestamp"], "side": side, "entry": entry,
                            "stop": stop, "target": target, "ounces": float(ounces),
                            "lots_equivalent": float(ounces / s.contract_ounces_per_lot),
                            "entry_spread": float(spread), "spread": float(spread),
                            "atr": float(atr),
                        }

    # Record any open position at the final close so metrics include it.
    if position is not None and not x.empty:
        last = x.iloc[-1]
        final_day = last["timestamp"].date().isoformat()
        close_position(last, float(last["close"]), "DATA_END", final_day)

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        metrics = {"trades": 0, "ending_balance": balance, "net_pnl": 0.0,
                   "win_rate_pct": 0.0, "max_drawdown_pct": 0.0, "profit_factor": 0.0}
    else:
        equity = trades_df["balance_after"].astype(float)
        peaks = equity.cummax().clip(lower=s.starting_balance)
        dd = (peaks - equity) / peaks
        wins = float(trades_df.loc[trades_df.pnl > 0, "pnl"].sum())
        losses = float(-trades_df.loc[trades_df.pnl < 0, "pnl"].sum())
        metrics = {
            "trades": int(len(trades_df)), "ending_balance": float(balance),
            "net_pnl": float(trades_df.pnl.sum()),
            "win_rate_pct": float((trades_df.pnl > 0).mean() * 100),
            "max_drawdown_pct": float(dd.max() * 100),
            "profit_factor": float(wins / losses) if losses else (float("inf") if wins else 0.0),
        }
    return trades_df, metrics
