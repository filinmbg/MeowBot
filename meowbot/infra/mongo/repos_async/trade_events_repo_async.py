from __future__ import annotations

from typing import Any
from time import time

from bson import ObjectId


class TradeEventsRepositoryMongoAsync:
    def __init__(self, db):
        self.col = db["trade_events"]

    async def add_event(
        self,
        *,
        trade_id: str,
        event_type: str,
        ts: int,
        symbol: str,
        user_id: str,
        mode: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self.col.insert_one(
            {
                "trade_id": trade_id,
                "event_type": event_type,
                "ts": int(ts),
                "symbol": symbol,
                "user_id": user_id,
                "mode": mode,
                "payload": payload or {},
                "sent_to_telegram": False,
                "created_at": int(time() * 1000),
            }
        )

    async def get_unsent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        cursor = (
            self.col.find({"sent_to_telegram": False})
            .sort("created_at", 1)
            .limit(limit)
        )
        return await cursor.to_list(length=limit)

    async def mark_sent(self, event_id: ObjectId) -> None:
        await self.col.update_one(
            {"_id": event_id},
            {"$set": {"sent_to_telegram": True}},
        )