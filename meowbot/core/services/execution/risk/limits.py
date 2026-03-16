from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    max_open_trades_total: int = 10
    max_open_trades_per_symbol: int = 1