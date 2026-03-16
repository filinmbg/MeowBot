from __future__ import annotations

from typing import Protocol

from meowbot.core.domain.types import Trade


class Broker(Protocol):
    def ping(self) -> bool:
        ...

    def open_position(self, trade: Trade) -> Trade:
        """
        Відкрити позицію.
        Для sandbox просто повертає trade.
        Для futures/spot потім буде реальний ордер.
        """
        ...

    def reduce_position(self, trade: Trade, qty_to_reduce: float, price: float, reason: str) -> Trade:
        """
        Частково зменшити позицію.
        """
        ...

    def close_position(self, trade: Trade, price: float, reason: str) -> Trade:
        """
        Повністю закрити позицію.
        """
        ...