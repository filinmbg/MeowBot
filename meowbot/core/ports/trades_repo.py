from __future__ import annotations

from typing import Protocol

from meowbot.core.domain.types import Trade


class TradesRepository(Protocol):
    def create_trade(self, trade: Trade) -> None:
        ...

    def update_trade(self, trade: Trade) -> None:
        ...

    def get_open_trades(self) -> list[Trade]:
        ...

    def get_trade_by_id(self, trade_id: str) -> Trade | None:
        ...

    def get_trades(
        self,
        *,
        user_id: str | None = None,
        symbol: str | None = None,
        tf: str | None = None,
        mode: str | None = None,
        model_id: str | None = None,
        limit: int = 1000,
    ) -> list[Trade]:
        ...