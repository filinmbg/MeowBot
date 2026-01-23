from __future__ import annotations

from typing import Protocol, Any, Dict

from ..domain.types import Trade


class Broker(Protocol):
    def ping(self) -> bool:
        ...

    def open_trade(self, trade: Trade) -> Trade:
        """Paper/live: може повернути trade з фактичними qty/price."""
        ...

    def close_trade(self, trade: Trade) -> Trade:
        ...
