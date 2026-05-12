from __future__ import annotations


class ModeAwareBroker:
    def __init__(self, *, sandbox_broker, live_entry_broker) -> None:
        self.sandbox_broker = sandbox_broker
        self.live_entry_broker = live_entry_broker

    def ping(self) -> bool:
        return True

    def open_position(self, trade):
        if str(getattr(trade, "mode", "")) == "live":
            return self.live_entry_broker.open_position(trade)
        return self.sandbox_broker.open_position(trade)

    def reduce_position(self, trade, qty_to_reduce: float, price: float, reason: str):
        return self.sandbox_broker.reduce_position(
            trade,
            qty_to_reduce=qty_to_reduce,
            price=price,
            reason=reason,
        )

    def close_position(self, trade, price: float, reason: str):
        return self.sandbox_broker.close_position(
            trade,
            price=price,
            reason=reason,
        )
