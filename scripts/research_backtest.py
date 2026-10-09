"""Run chronological holdout and spread/slippage sensitivity research.

Paper/research only. This script never connects to a broker or places orders.
Example:
    python scripts/research_backtest.py path/to/candles.csv --output-dir research_results
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from config import Settings
from engine import backtest, validate_candles


SCENARIOS = (
    ("low_cost", 0.10, 0.02),
    ("baseline", 0.25, 0.05),
    ("high_cost", 0.50, 0.10),
)


def summarize(name: str, period: str, trades: pd.DataFrame, metrics: dict,
              spread: float, slippage: float) -> dict:
    gross = float(trades["gross_pnl"].sum()) if not trades.empty else 0.0
    costs = float(trades["costs"].sum()) if not trades.empty else 0.0
    return {
        "period": period,
        "scenario": name,
        "spread_assumption_usd_per_oz": spread,
        "slippage_usd_per_oz": slippage,
        "trades": int(metrics["trades"]),
        "win_rate_pct": float(metrics["win_rate_pct"]),
        "gross_pnl": gross,
        "estimated_costs": costs,
        "net_pnl": float(metrics["net_pnl"]),
        "ending_balance": float(metrics["ending_balance"]),
        "max_drawdown_pct": float(metrics["max_drawdown_pct"]),
        "profit_factor": float(metrics["profit_factor"]),
    }


def run_research(csv_path: Path, output_dir: Path, train_fraction: float = 0.70) -> pd.DataFrame:
    candles = validate_candles(pd.read_csv(csv_path))
    if not 0.5 <= train_fraction <= 0.9:
        raise ValueError("train_fraction must be between 0.5 and 0.9.")
    if len(candles) < 500:
        raise ValueError("At least 500 candles are required for a useful split.")

    split = int(len(candles) * train_fraction)
    train = candles.iloc[:split].reset_index(drop=True)
    test = candles.iloc[split:].reset_index(drop=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    # Baseline in-sample is diagnostic only; do not use it to claim future edge.
    base = Settings()
    trades, metrics = backtest(train, base)
    trades.to_csv(output_dir / "train_baseline_trades.csv", index=False)
    rows.append(summarize("baseline", "in_sample", trades, metrics,
                          base.assumed_spread_usd_per_oz, base.slippage_usd_per_oz))

    for name, spread, slippage in SCENARIOS:
        settings = Settings(
            assumed_spread_usd_per_oz=spread,
            slippage_usd_per_oz=slippage,
            max_spread_usd_per_oz=max(0.50, spread),
        )
        trades, metrics = backtest(test, settings)
        trades.to_csv(output_dir / f"test_{name}_trades.csv", index=False)
        rows.append(summarize(name, "out_of_sample", trades, metrics, spread, slippage))

    summary = pd.DataFrame(rows)
    summary.to_csv(output_dir / "research_summary.csv", index=False)
    print(f"Candles: {len(candles):,}")
    print(f"Train: {len(train):,} ({train.timestamp.min()} to {train.timestamp.max()})")
    print(f"Test:  {len(test):,} ({test.timestamp.min()} to {test.timestamp.max()})")
    if "spread" in candles.columns:
        print("NOTE: CSV has a spread column; per-candle source spreads take precedence over assumptions.")
    else:
        print("NOTE: CSV has no historical spread; scenarios use assumed spread values, not observed broker spreads.")
    print("\nResearch summary:")
    print(summary.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
    print(f"\nSaved outputs to: {output_dir.resolve()}")
    print("Holdout indicators are recalculated within the test slice; allow for indicator warm-up at its start.")
    print("Research only: this is not evidence of live profitability.")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="CSV with timestamp,open,high,low,close columns")
    parser.add_argument("--output-dir", type=Path, default=Path("research_results"))
    parser.add_argument("--train-fraction", type=float, default=0.70)
    args = parser.parse_args()
    run_research(args.csv, args.output_dir, args.train_fraction)


if __name__ == "__main__":
    main()
