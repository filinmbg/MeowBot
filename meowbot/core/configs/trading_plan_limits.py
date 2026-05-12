from __future__ import annotations

from dataclasses import dataclass


RISK_MAX_ACTIVE_TRADES = 5

FREE_MAX_TRADES = 1
BASIC_MAX_TRADES = 5
PRO_MAX_TRADES = 20
VIP_MAX_TRADES = None

FREE_MAX_SYMBOLS = 10
BASIC_MAX_SYMBOLS = 50
PRO_MAX_SYMBOLS = 100
VIP_MAX_SYMBOLS = None

MAX_OPEN_TRADES_PER_SYMBOL = 1


@dataclass(frozen=True)
class PlanTradeLimits:
    max_symbols: int | None
    max_open_trades_total: int | None
    max_open_trades_per_symbol: int = MAX_OPEN_TRADES_PER_SYMBOL


PLAN_TRADE_LIMITS: dict[str, PlanTradeLimits] = {
    "free": PlanTradeLimits(
        max_symbols=FREE_MAX_SYMBOLS,
        max_open_trades_total=FREE_MAX_TRADES,
    ),
    "basic": PlanTradeLimits(
        max_symbols=BASIC_MAX_SYMBOLS,
        max_open_trades_total=BASIC_MAX_TRADES,
    ),
    "pro": PlanTradeLimits(
        max_symbols=PRO_MAX_SYMBOLS,
        max_open_trades_total=PRO_MAX_TRADES,
    ),
    "vip": PlanTradeLimits(
        max_symbols=VIP_MAX_SYMBOLS,
        max_open_trades_total=VIP_MAX_TRADES,
    ),
}


def normalize_plan_code(plan_code: str | None) -> str:
    normalized = str(plan_code or "").strip().lower()
    if normalized.endswith("_v2"):
        return normalized[:-3]
    return normalized
