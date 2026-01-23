from __future__ import annotations

from typing import Protocol, Optional, List

from ..domain.types import Trade
from ..domain.enums import TradeStatus


class TradesRepository(Protocol):
    def ping(self) -> bool:
        ...

    def get_open_trades(self) -> List[Trade]:
        ...

    def get_open_trade_by_symbol(self, symbol: str) -> Optional[Trade]:
        ...

    def create_trade(self, trade: Trade) -> None:
        ...

    def update_trade(self, trade: Trade) -> None:
        ...

    def close_trade(
        self,
        trade_id: str,
        closed_at: int,
        close_price: float,
        reason: str,
        pnl_usd: float,
    ) -> None:
        ...

    def was_entry_bar_used(self, symbol: str, tf: str, close_time: int) -> bool:
        ...
