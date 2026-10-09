import pandas as pd
import pytest

from config import Settings
from engine import backtest, indicators, validate_candles


def candles(n=600):
    ts = pd.date_range("2026-01-01T00:00:00Z", periods=n, freq="min")
    close = [2600 + i * 0.01 for i in range(n)]
    return pd.DataFrame({
        "timestamp": ts, "open": close,
        "high": [v + 0.15 for v in close],
        "low": [v - 0.15 for v in close], "close": close,
    })


def test_validation_and_indicators():
    df = candles()
    result = indicators(validate_candles(df), Settings())
    assert len(result) == len(df)
    assert {"signal", "atr", "trend_up", "trend_down"} <= set(result.columns)


def test_rejects_duplicate_timestamp():
    df = candles(3)
    df.loc[2, "timestamp"] = df.loc[1, "timestamp"]
    with pytest.raises(ValueError, match="Duplicate timestamps"):
        validate_candles(df)


def test_rejects_out_of_order_timestamp():
    df = candles(3)
    df.loc[[1, 2], "timestamp"] = df.loc[[2, 1], "timestamp"].to_numpy()
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_candles(df)


def test_rejects_invalid_spread():
    df = candles(3)
    df["spread"] = [0.2, -0.1, 0.2]
    with pytest.raises(ValueError, match="Spread must"):
        validate_candles(df)


def test_rejects_bad_ohlc():
    df = candles(3)
    df.loc[1, "high"] = df.loc[1, "low"] - 1
    with pytest.raises(ValueError, match="high is below"):
        validate_candles(df)


def test_backtest_metrics_reconcile():
    trades, metrics = backtest(candles(), Settings())
    assert metrics["trades"] == len(trades)
    if not trades.empty:
        assert abs(metrics["net_pnl"] - trades["pnl"].sum()) < 1e-8
        assert abs(metrics["ending_balance"] - (Settings().starting_balance + metrics["net_pnl"])) < 1e-8


def test_empty_data_rejected():
    with pytest.raises(ValueError, match="no candle rows"):
        validate_candles(pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"]))
