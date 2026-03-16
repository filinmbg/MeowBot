from __future__ import annotations

from pymongo.database import Database

from meowbot.core.ports.trade_events_repo import TradeEventsRepository


class TradeEventsRepositoryMongo(TradeEventsRepository):
    def __init__(self, db: Database, collection_name: str = "trade_events"):
        self.col = db[collection_name]

    def add_event(
        self,
        trade_id: str,
        event_type: str,
        ts: int,
        symbol: str,
        user_id: str,
        mode: str,
        payload: dict | None = None,
    ) -> None:
        self.col.insert_one(
            {
                "trade_id": trade_id,
                "event_type": event_type,
                "ts": ts,
                "symbol": symbol,
                "user_id": user_id,
                "mode": mode,
                "payload": payload or {},
            }
        )