from __future__ import annotations
from meowbot.core.domain.types import Trade
from meowbot.core.ports.broker import Broker


class InMemoryBroker(Broker):
    def ping(self) -> bool:
        return True

    def open_trade(self, trade: Trade) -> Trade:
        return trade

    def close_trade(self, trade: Trade) -> Trade:
        return trade
