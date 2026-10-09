import pandas as pd
from config import Settings
from engine import validate_candles, indicators

def test_validation_and_indicators():
    ts = pd.date_range("2026-01-01", periods=500, freq="min", tz="UTC")
    close = [2600 + i*0.01 for i in range(500)]
    df = pd.DataFrame({"timestamp":ts, "open":close, "high":[v+0.1 for v in close],
                       "low":[v-0.1 for v in close], "close":close})
    clean = validate_candles(df)
    result = indicators(clean, Settings())
    assert len(result) == 500
    assert "signal" in result.columns
