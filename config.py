from dataclasses import dataclass

@dataclass
class Settings:
    starting_balance: float = 10_000.0
    risk_fraction: float = 0.0025
    daily_loss_limit_fraction: float = 0.01
    assumed_spread_usd_per_oz: float = 0.25
    slippage_usd_per_oz: float = 0.05
    max_spread_usd_per_oz: float = 0.50
    contract_ounces_per_lot: float = 100.0  # common convention; varies by provider
    max_open_positions: int = 1
    atr_period: int = 14
    fast_ema: int = 9
    slow_ema: int = 21
    trend_ema: int = 20
    stop_atr_multiple: float = 1.2
    reward_risk: float = 1.5
