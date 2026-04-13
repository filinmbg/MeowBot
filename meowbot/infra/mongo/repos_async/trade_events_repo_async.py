from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING


RISK_EVENT_TYPES = {
    "ENTRY_BLOCKED_SUBSCRIPTION",
    "ENTRY_BLOCKED_RISK",
    "ENTRY_WARNING_RISK",
    "ENTRY_BLOCKED_COOLDOWN",
}


class TradeEventsRepositoryMongoAsync:
    def __init__(self, db) -> None:
        self.col = db["trade_events"]

    async def ensure_indexes(self) -> None:
        await self.col.create_index([("trade_id", ASCENDING), ("ts", DESCENDING)])
        await self.col.create_index([("event_type", ASCENDING), ("ts", DESCENDING)])
        await self.col.create_index([("user_id", ASCENDING), ("ts", DESCENDING)])
        await self.col.create_index([("symbol", ASCENDING), ("ts", DESCENDING)])
        await self.col.create_index([("sent", ASCENDING), ("ts", ASCENDING)], name="idx_sent_ts")
        await self.col.create_index(
            [("event_type", ASCENDING), ("ts", DESCENDING), ("delivered_channels", ASCENDING)],
            name="idx_risk_delivery",
        )

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
    ) -> str:
        now = datetime.now(timezone.utc)
        doc = {
            "trade_id": trade_id,
            "event_type": event_type,
            "ts": int(ts),
            "symbol": symbol,
            "user_id": user_id,
            "mode": mode,
            "payload": payload or {},
            "sent": False,
            "sent_at": None,
            "delivered_channels": [],
            "created_at": now,
            "updated_at": now,
        }
        result = await self.col.insert_one(doc)
        return str(result.inserted_id)

    async def get_events_for_trade(self, trade_id: str, limit: int = 100) -> list[dict[str, Any]]:
        cursor = (
            self.col.find({"trade_id": trade_id})
            .sort("ts", DESCENDING)
            .limit(int(limit))
        )
        rows = await cursor.to_list(length=int(limit))
        return [self._normalize_doc(row) for row in rows]

    async def get_unsent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        cursor = (
            self.col.find({"sent": {"$ne": True}})
            .sort("ts", ASCENDING)
            .limit(int(limit))
        )
        rows = await cursor.to_list(length=int(limit))
        return [self._normalize_doc(row, keep_legacy_id=True) for row in rows]

    async def mark_sent(self, event_id: str) -> None:
        await self.mark_event_sent(event_id)

    async def mark_event_sent(self, event_id: str) -> None:
        from bson import ObjectId

        now = datetime.now(timezone.utc)
        await self.col.update_one(
            {"_id": ObjectId(event_id)},
            {
                "$set": {
                    "sent": True,
                    "sent_at": now,
                    "updated_at": now,
                }
            },
        )

    async def mark_many_events_sent(self, event_ids: list[str]) -> None:
        if not event_ids:
            return

        from bson import ObjectId

        now = datetime.now(timezone.utc)
        object_ids = [ObjectId(x) for x in event_ids]
        await self.col.update_many(
            {"_id": {"$in": object_ids}},
            {
                "$set": {
                    "sent": True,
                    "sent_at": now,
                    "updated_at": now,
                }
            },
        )

    async def get_pending_risk_events(
        self,
        *,
        channel: str = "telegram_risk",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = {
            "event_type": {"$in": list(RISK_EVENT_TYPES)},
            "delivered_channels": {"$ne": channel},
        }
        cursor = (
            self.col.find(query)
            .sort("ts", ASCENDING)
            .limit(int(limit))
        )
        rows = await cursor.to_list(length=int(limit))
        return [self._normalize_doc(row) for row in rows]

    async def mark_event_delivered(self, event_id: str, *, channel: str) -> None:
        from bson import ObjectId

        await self.col.update_one(
            {"_id": ObjectId(event_id)},
            {
                "$addToSet": {"delivered_channels": channel},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
        )

    async def mark_many_events_delivered(self, event_ids: list[str], *, channel: str) -> None:
        if not event_ids:
            return

        from bson import ObjectId

        object_ids = [ObjectId(x) for x in event_ids]
        await self.col.update_many(
            {"_id": {"$in": object_ids}},
            {
                "$addToSet": {"delivered_channels": channel},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
        )

    def _normalize_doc(
        self,
        doc: dict[str, Any],
        *,
        keep_legacy_id: bool = False,
    ) -> dict[str, Any]:
        row = dict(doc)
        object_id = row.get("_id")
        if object_id is not None:
            object_id_str = str(object_id)
            row["id"] = object_id_str
            if keep_legacy_id:
                row["_id"] = object_id_str
            else:
                row.pop("_id", None)

        row.setdefault("payload", {})
        row.setdefault("delivered_channels", [])
        row.setdefault("sent", False)
        row.setdefault("sent_at", None)
        return row
