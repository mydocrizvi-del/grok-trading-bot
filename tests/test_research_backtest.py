import pandas as pd

from scripts.research_backtest import run_research


def test_research_runner_writes_summary_and_splits_chronologically(tmp_path):
    n = 600
    close = [2600 + (i % 30) * 0.2 + i * 0.002 for i in range(n)]
    ts = pd.date_range("2026-01-01T00:00:00Z", periods=n, freq="min")
    candles = pd.DataFrame({
        "timestamp": ts,
        "open": close,
        "high": [x + 0.2 for x in close],
        "low": [x - 0.2 for x in close],
        "close": close,
    })
    source = tmp_path / "candles.csv"
    output = tmp_path / "out"
    candles.to_csv(source, index=False)

    summary = run_research(source, output)

    assert len(summary) == 4
    assert set(summary["period"]) == {"in_sample", "out_of_sample"}
    assert (output / "research_summary.csv").exists()
    assert (output / "train_baseline_trades.csv").exists()
    assert (output / "test_low_cost_trades.csv").exists()
    assert (output / "test_baseline_trades.csv").exists()
    assert (output / "test_high_cost_trades.csv").exists()
