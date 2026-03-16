from __future__ import annotations

from meowbot.core.domain.types import Signal
from meowbot.core.domain.enums import SignalAction
from meowbot.core.ports.trades_repo import TradesRepository
from meowbot.core.services.execution.risk.limits import RiskLimits


class PortfolioGate:
    """
    Правила:
    - якщо HOLD -> не відкривати
    - max_open_trades_total
    - max_open_trades_per_symbol
    """
    def __init__(self, trades_repo: TradesRepository, limits: RiskLimits | None = None):
        self.trades_repo = trades_repo
        self.limits = limits or RiskLimits()

    def allow(self, symbol: str, signal: Signal) -> bool:
        if signal.action == SignalAction.HOLD:
            return False

        open_trades = self.trades_repo.get_open_trades()

        if len(open_trades) >= self.limits.max_open_trades_total:
            return False

        per_symbol = sum(1 for t in open_trades if t.symbol == symbol)
        if per_symbol >= self.limits.max_open_trades_per_symbol:
            return False

        return True