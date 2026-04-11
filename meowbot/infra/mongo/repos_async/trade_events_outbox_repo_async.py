from __future__ import annotations

from typing import Any


class TradeEventsOutboxRepositoryMongoAsync:
    def __init__(self, db):
        self.col = db["trade_events"]

    async def get_unsent_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        cursor = (
            self.col.find(
                {
                    "$or": [
                        {"telegram_sent": {"$exists": False}},
                        {"telegram_sent": False},
                    ]
                }
            )
            .sort("ts", 1)
            .limit(limit)
        )
        return await cursor.to_list(length=limit)

    async def mark_sent(self, event_id) -> None:
        await self.col.update_one(
            {"_id": event_id},
            {"$set": {"telegram_sent": True}},
        )

    async def mark_failed(self, event_id, error_text: str) -> None:
        await self.col.update_one(
            {"_id": event_id},
            {
                "$set": {
                    "telegram_sent": False,
                    "telegram_error": error_text[:500],
                }
            },
        )