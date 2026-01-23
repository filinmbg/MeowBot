from __future__ import annotations

from typing import List, Optional
from meowbot.core.domain.types import Trade
from meowbot.core.domain.enums import TradeStatus
from meowbot.core.ports.trades_repo import TradesRepository


class InMemoryTradesRepo(TradesRepository):
    def __init__(self):
        self._trades: dict[str, Trade] = {}

    def ping(self) -> bool:
        return True

    def get_open_trades(self) -> List[Trade]:
        return [t for t in self._trades.values() if t.status == TradeStatus.OPEN]

    def get_open_trade_by_symbol(self, symbol: str) -> Optional[Trade]:
        for t in self._trades.values():
            if t.symbol == symbol and t.status == TradeStatus.OPEN:
                return t
        return None

    def create_trade(self, trade: Trade) -> None:
        self._trades[trade.trade_id] = trade

    def update_trade(self, trade: Trade) -> None:
        self._trades[trade.trade_id] = trade

    def close_trade(
        self,
        trade_id: str,
        closed_at: int,
        close_price: float,
        reason: str,
        pnl_usd: float,
    ) -> None:
        t = self._trades[trade_id]
        t.status = TradeStatus.CLOSED
        t.closed_at = closed_at
        t.close_price = close_price
        t.exit_reason = reason
        t.realized_pnl_usd = pnl_usd
        self._trades[trade_id] = t

    def was_entry_bar_used(self, symbol: str, tf: str, close_time: int) -> bool:
        for t in self._trades.values():
            if t.symbol == symbol and t.tf_entry == tf and t.entry_bar_close_time == close_time:
                return True
        return False
