from __future__ import annotations

from meowbot.core.domain.types import Signal
from meowbot.core.domain.enums import SignalAction
from meowbot.core.ports.trades_repo import TradesRepository


class PortfolioGate:
    """
    На старті: максимум 1 відкритий трейд на символ.
    Пізніше додамо max_total, top-K і т.д.
    """
    def __init__(self, trades_repo: TradesRepository):
        self.trades_repo = trades_repo

    def allow(self, symbol: str, signal: Signal) -> bool:
        if signal.action == SignalAction.HOLD:
            return False
        existing = self.trades_repo.get_open_trade_by_symbol(symbol)
        return existing is None
