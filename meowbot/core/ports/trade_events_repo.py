from __future__ import annotations

from typing import Protocol, Optional


class TradeEventsRepository(Protocol):
    def add_event(
        self,
        trade_id: str,
        event_type: str,
        ts: int,
        symbol: str,
        user_id: str,
        mode: str,
        payload: Optional[dict] = None,
    ) -> None:
        ...