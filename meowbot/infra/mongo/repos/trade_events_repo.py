from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

from meowbot.core.ports.trade_events_repo import TradeEventsRepository

MAX_EVENT_PAYLOAD_STRING_LENGTH = 2000
MAX_EVENT_PAYLOAD_LIST_ITEMS = 100


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
                "payload": self._compact_payload(payload or {}),
                "created_at": datetime.now(timezone.utc),
            }
        )

    def _compact_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): self._compact_payload(item)
                for key, item in value.items()
                if key != "_id"
            }
        if isinstance(value, list):
            items = [self._compact_payload(item) for item in value[:MAX_EVENT_PAYLOAD_LIST_ITEMS]]
            if len(value) > MAX_EVENT_PAYLOAD_LIST_ITEMS:
                items.append({"truncated_items": len(value) - MAX_EVENT_PAYLOAD_LIST_ITEMS})
            return items
        if isinstance(value, tuple):
            return self._compact_payload(list(value))
        if isinstance(value, bytes):
            return f"<bytes:{len(value)}>"
        if isinstance(value, str) and len(value) > MAX_EVENT_PAYLOAD_STRING_LENGTH:
            return f"{value[:MAX_EVENT_PAYLOAD_STRING_LENGTH]}...<truncated:{len(value) - MAX_EVENT_PAYLOAD_STRING_LENGTH}>"
        return value
