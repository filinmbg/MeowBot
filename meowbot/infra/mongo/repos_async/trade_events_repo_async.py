from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from pymongo.errors import BulkWriteError
from pymongo.errors import AutoReconnect, DuplicateKeyError, NetworkTimeout, OperationFailure, PyMongoError, ServerSelectionTimeoutError
from pymongo import ASCENDING, DESCENDING


log = logging.getLogger("meowbot")
MAX_EVENT_PAYLOAD_STRING_LENGTH = 2000
MAX_EVENT_PAYLOAD_LIST_ITEMS = 100
TERMINAL_NOTIFICATION_STATUSES = {
    "SENT",
    "FAILED_PERMANENT",
    "SKIPPED",
    "SKIPPED_DEBUG",
    "SKIPPED_INVALID_CHAT",
}
RISK_EVENT_TYPES = {
    "ENTRY_BLOCKED_SUBSCRIPTION",
    "ENTRY_BLOCKED_RISK",
    "ENTRY_WARNING_RISK",
    "ENTRY_BLOCKED_COOLDOWN",
}
TRANSIENT_MONGO_ERRORS = (
    NetworkTimeout,
    ServerSelectionTimeoutError,
    AutoReconnect,
    PyMongoError,
)


class TradeEventsRepositoryMongoAsync:
    def __init__(self, db) -> None:
        self.col = db["trade_events"]

    async def ensure_indexes(self) -> None:
        await self._ensure_index([("trade_id", ASCENDING), ("ts", DESCENDING)], name="idx_trade_ts_desc")
        await self._ensure_index([("event_type", ASCENDING), ("ts", DESCENDING)], name="idx_event_type_ts_desc")
        await self._ensure_index([("user_id", ASCENDING), ("ts", DESCENDING)], name="idx_user_ts_desc")
        await self._ensure_index([("symbol", ASCENDING), ("ts", DESCENDING)], name="idx_symbol_ts_desc")
        await self._ensure_index([("sent", ASCENDING), ("ts", ASCENDING)], name="idx_sent_ts")
        await self._ensure_index(
            [("notification_status", ASCENDING), ("next_retry_at", ASCENDING), ("ts", ASCENDING)],
            name="idx_notification_status_retry",
        )
        await self._ensure_index(
            [("idempotency_key", ASCENDING)],
            unique=True,
            sparse=True,
            name="idempotency_key_unique",
        )
        await self._ensure_index(
            [("event_type", ASCENDING), ("ts", DESCENDING), ("delivered_channels", ASCENDING)],
            name="idx_risk_delivery",
        )

    async def _ensure_index(self, keys, *, name: str, **options) -> None:
        unique = bool(options.get("unique", False))
        sparse = bool(options.get("sparse", False))
        if await self._has_compatible_index(keys, unique=unique, sparse=sparse):
            return
        try:
            await self.col.create_index(keys, name=name, **options)
        except OperationFailure as exc:
            if getattr(exc, "code", None) == 85 and await self._has_compatible_index(
                keys,
                unique=unique,
                sparse=sparse,
            ):
                return
            raise

    async def _has_compatible_index(self, keys, *, unique: bool = False, sparse: bool = False) -> bool:
        expected_keys = [(str(field), int(direction)) for field, direction in keys]
        info = await self.col.index_information()
        for spec in info.values():
            existing_keys = [
                (str(field), int(direction))
                for field, direction in spec.get("key", [])
            ]
            if existing_keys != expected_keys:
                continue
            if bool(spec.get("unique", False)) != bool(unique):
                continue
            if bool(spec.get("sparse", False)) != bool(sparse):
                continue
            return True
        return False

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
        doc = self.build_event_doc(
            trade_id=trade_id,
            event_type=event_type,
            ts=ts,
            symbol=symbol,
            user_id=user_id,
            mode=mode,
            payload=payload,
        )
        try:
            result = await self.col.insert_one(doc)
            return str(result.inserted_id)
        except TRANSIENT_MONGO_ERRORS as exc:
            log.warning(
                "[mongo] trade_events.add_event skipped event_type=%s user_id=%s symbol=%s error=%s:%s",
                event_type,
                user_id,
                symbol,
                type(exc).__name__,
                exc,
            )
            return ""

    async def add_event_once(
        self,
        *,
        idempotency_key: str,
        trade_id: str,
        event_type: str,
        ts: int,
        symbol: str,
        user_id: str,
        mode: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[str | None, bool]:
        doc = self.build_event_doc(
            trade_id=trade_id,
            event_type=event_type,
            ts=ts,
            symbol=symbol,
            user_id=user_id,
            mode=mode,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        try:
            result = await self.col.insert_one(doc)
            return str(result.inserted_id), True
        except DuplicateKeyError:
            existing = await self.col.find_one({"idempotency_key": idempotency_key}, {"_id": 1})
            return (str(existing["_id"]) if existing and existing.get("_id") else None), False
        except TRANSIENT_MONGO_ERRORS as exc:
            log.warning(
                "[mongo] trade_events.add_event_once skipped event_type=%s user_id=%s symbol=%s idempotency_key=%s error=%s:%s",
                event_type,
                user_id,
                symbol,
                idempotency_key,
                type(exc).__name__,
                exc,
            )
            return None, False

    async def get_events_for_trade(self, trade_id: str, limit: int = 100) -> list[dict[str, Any]]:
        cursor = (
            self.col.find({"trade_id": trade_id})
            .sort("ts", DESCENDING)
            .limit(int(limit))
        )
        rows = await cursor.to_list(length=int(limit))
        return [self._normalize_doc(row) for row in rows]

    async def get_unsent_events(
        self,
        limit: int = 100,
        *,
        event_types: list[str] | tuple[str, ...] | set[str] | None = None,
        exclude_event_types: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        query: dict[str, Any] = {
            "sent": {"$ne": True},
            "$and": [
                {
                    "$or": [
                        {"notification_status": {"$exists": False}},
                        {"notification_status": None},
                        {"notification_status": {"$in": ["PENDING", "FAILED_TEMP"]}},
                    ]
                },
                {
                    "$or": [
                        {"next_retry_at": {"$exists": False}},
                        {"next_retry_at": None},
                        {"next_retry_at": {"$lte": now}},
                    ]
                },
            ],
        }
        if event_types:
            query["event_type"] = {"$in": [str(item) for item in event_types]}
        elif exclude_event_types:
            query["event_type"] = {"$nin": [str(item) for item in exclude_event_types]}
        cursor = (
            self.col.find(query)
            .sort("ts", ASCENDING)
            .limit(int(limit))
        )
        rows = await cursor.to_list(length=int(limit))
        return [self._normalize_doc(row, keep_legacy_id=True) for row in rows]

    async def claim_unsent_events(
        self,
        limit: int = 100,
        *,
        worker_id: str,
        event_types: list[str] | tuple[str, ...] | set[str] | None = None,
        exclude_event_types: list[str] | tuple[str, ...] | set[str] | None = None,
        lock_ttl_ms: int = 120_000,
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        lock_expired_before = now - timedelta(milliseconds=max(1, int(lock_ttl_ms)))
        query: dict[str, Any] = {
            "sent": {"$ne": True},
            "$and": [
                {
                    "$or": [
                        {"notification_status": {"$exists": False}},
                        {"notification_status": None},
                        {"notification_status": {"$in": ["PENDING", "FAILED_TEMP"]}},
                        {
                            "$and": [
                                {"notification_status": "PROCESSING"},
                                {"locked_at": {"$lte": lock_expired_before}},
                            ]
                        },
                    ]
                },
                {
                    "$or": [
                        {"next_retry_at": {"$exists": False}},
                        {"next_retry_at": None},
                        {"next_retry_at": {"$lte": now}},
                    ]
                },
            ],
        }
        if event_types:
            query["event_type"] = {"$in": [str(item) for item in event_types]}
        elif exclude_event_types:
            query["event_type"] = {"$nin": [str(item) for item in exclude_event_types]}

        candidates = await self.col.find(query).sort("ts", ASCENDING).limit(int(limit)).to_list(length=int(limit))
        claimed: list[dict[str, Any]] = []
        for doc in candidates:
            result = await self.col.update_one(
                {
                    "_id": doc["_id"],
                    "sent": {"$ne": True},
                    "$or": [
                        {"notification_status": {"$exists": False}},
                        {"notification_status": None},
                        {"notification_status": {"$in": ["PENDING", "FAILED_TEMP"]}},
                        {
                            "$and": [
                                {"notification_status": "PROCESSING"},
                                {"locked_at": {"$lte": lock_expired_before}},
                            ]
                        },
                    ],
                },
                {
                    "$set": {
                        "notification_status": "PROCESSING",
                        "locked_at": now,
                        "locked_by": worker_id,
                        "updated_at": now,
                    }
                },
            )
            if result.modified_count:
                doc["notification_status"] = "PROCESSING"
                doc["locked_at"] = now
                doc["locked_by"] = worker_id
                claimed.append(self._normalize_doc(doc, keep_legacy_id=True))
        return claimed

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
                    "notification_status": "SENT",
                    "retryable": False,
                    "locked_at": None,
                    "locked_by": None,
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
                    "notification_status": "SENT",
                    "retryable": False,
                    "locked_at": None,
                    "locked_by": None,
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
        now = datetime.now(timezone.utc)
        query = {
            "event_type": {"$in": list(RISK_EVENT_TYPES)},
            "delivered_channels": {"$ne": channel},
            "$and": [
                {
                    "$or": [
                        {"notification_status": {"$exists": False}},
                        {"notification_status": None},
                        {"notification_status": {"$in": ["PENDING", "FAILED_TEMP"]}},
                    ]
                },
                {
                    "$or": [
                        {"next_retry_at": {"$exists": False}},
                        {"next_retry_at": None},
                        {"next_retry_at": {"$lte": now}},
                    ]
                },
            ],
        }
        cursor = (
            self.col.find(query)
            .sort("ts", ASCENDING)
            .limit(int(limit))
        )
        rows = await cursor.to_list(length=int(limit))
        return [self._normalize_doc(row) for row in rows]

    async def claim_pending_risk_events(
        self,
        *,
        channel: str = "telegram_risk",
        limit: int = 100,
        worker_id: str,
        lock_ttl_ms: int = 120_000,
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        lock_expired_before = now - timedelta(milliseconds=max(1, int(lock_ttl_ms)))
        query = {
            "event_type": {"$in": list(RISK_EVENT_TYPES)},
            "delivered_channels": {"$ne": channel},
            "$and": [
                {
                    "$or": [
                        {"notification_status": {"$exists": False}},
                        {"notification_status": None},
                        {"notification_status": {"$in": ["PENDING", "FAILED_TEMP"]}},
                        {
                            "$and": [
                                {"notification_status": "PROCESSING"},
                                {"locked_at": {"$lte": lock_expired_before}},
                            ]
                        },
                    ]
                },
                {
                    "$or": [
                        {"next_retry_at": {"$exists": False}},
                        {"next_retry_at": None},
                        {"next_retry_at": {"$lte": now}},
                    ]
                },
            ],
        }
        candidates = await self.col.find(query).sort("ts", ASCENDING).limit(int(limit)).to_list(length=int(limit))
        claimed: list[dict[str, Any]] = []
        for doc in candidates:
            result = await self.col.update_one(
                {
                    "_id": doc["_id"],
                    "delivered_channels": {"$ne": channel},
                    "$or": [
                        {"notification_status": {"$exists": False}},
                        {"notification_status": None},
                        {"notification_status": {"$in": ["PENDING", "FAILED_TEMP"]}},
                        {
                            "$and": [
                                {"notification_status": "PROCESSING"},
                                {"locked_at": {"$lte": lock_expired_before}},
                            ]
                        },
                    ],
                },
                {
                    "$set": {
                        "notification_status": "PROCESSING",
                        "locked_at": now,
                        "locked_by": worker_id,
                        "updated_at": now,
                    }
                },
            )
            if result.modified_count:
                doc["notification_status"] = "PROCESSING"
                doc["locked_at"] = now
                doc["locked_by"] = worker_id
                claimed.append(self._normalize_doc(doc))
        return claimed

    async def get_recent_risk_events(
        self,
        *,
        limit: int = 20,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {
            "event_type": {"$in": list(RISK_EVENT_TYPES)},
        }
        if user_id is not None:
            query["user_id"] = user_id

        cursor = (
            self.col.find(query)
            .sort("ts", DESCENDING)
            .limit(int(limit))
        )
        rows = await cursor.to_list(length=int(limit))
        return [self._normalize_doc(row) for row in rows]

    async def count_risk_events_by_user_ids(
        self,
        *,
        user_ids: list[str],
        limit_hours: int = 3,
    ) -> dict[str, int]:
        if not user_ids:
            return {}

        threshold = int(
            (datetime.now(timezone.utc).timestamp() - (int(limit_hours) * 3600))
            * 1000
        )
        pipeline = [
            {
                "$match": {
                    "event_type": {"$in": list(RISK_EVENT_TYPES)},
                    "user_id": {"$in": user_ids},
                    "ts": {"$gte": threshold},
                }
            },
            {
                "$group": {
                    "_id": "$user_id",
                    "count": {"$sum": 1},
                }
            },
        ]
        rows = await self.col.aggregate(pipeline).to_list(length=len(user_ids))
        return {
            str(row["_id"]): int(row["count"])
            for row in rows
            if row.get("_id") is not None
        }

    async def mark_event_delivered(self, event_id: str, *, channel: str) -> None:
        from bson import ObjectId

        await self.col.update_one(
            {"_id": ObjectId(event_id)},
            {
                "$addToSet": {"delivered_channels": channel},
                "$set": {
                    "notification_status": "SENT",
                    "retryable": False,
                    "locked_at": None,
                    "locked_by": None,
                    "updated_at": datetime.now(timezone.utc),
                },
            },
        )

    async def mark_event_skipped(self, event_id: str, *, reason: str, channel: str | None = None) -> None:
        from bson import ObjectId

        status = (
            "SKIPPED_DEBUG"
            if reason == "debug_event"
            else "SKIPPED_INVALID_CHAT"
            if reason == "invalid_chat"
            else "SKIPPED"
        )
        update: dict[str, Any] = {
            "$set": {
                "notification_status": status,
                "skip_reason": reason,
                "retryable": False,
                "locked_at": None,
                "locked_by": None,
                "updated_at": datetime.now(timezone.utc),
            }
        }
        if channel:
            update["$addToSet"] = {"delivered_channels": channel}
        await self.col.update_one({"_id": ObjectId(event_id)}, update)

    async def mark_event_failed_permanent(
        self,
        event_id: str,
        *,
        reason: str,
        channel: str | None = None,
    ) -> None:
        from bson import ObjectId

        now = datetime.now(timezone.utc)
        update: dict[str, Any] = {
            "$inc": {"send_attempt_count": 1},
            "$set": {
                "notification_status": "FAILED_PERMANENT",
                "last_error": reason,
                "failed_at": now,
                "retryable": False,
                "locked_at": None,
                "locked_by": None,
                "updated_at": now,
            },
        }
        if channel:
            update["$addToSet"] = {"delivered_channels": channel}
        await self.col.update_one({"_id": ObjectId(event_id)}, update)

    async def mark_event_failed_temp(self, event_id: str, *, reason: str, next_retry_at: int) -> None:
        from bson import ObjectId

        await self.col.update_one(
            {"_id": ObjectId(event_id)},
            {
                "$inc": {"send_attempt_count": 1},
                "$set": {
                    "notification_status": "FAILED_TEMP",
                    "last_error": reason,
                    "retryable": True,
                    "next_retry_at": datetime.fromtimestamp(int(next_retry_at) / 1000, tz=timezone.utc),
                    "locked_at": None,
                    "locked_by": None,
                    "updated_at": datetime.now(timezone.utc),
                },
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
                "$set": {
                    "notification_status": "SENT",
                    "retryable": False,
                    "locked_at": None,
                    "locked_by": None,
                    "updated_at": datetime.now(timezone.utc),
                },
            },
        )

    async def delete_events_by_user(self, *, user_id: str, mode: str | None = None) -> int:
        query: dict[str, Any] = {"user_id": user_id}
        if mode is not None:
            query["mode"] = mode
        result = await self.col.delete_many(query)
        return int(result.deleted_count)

    def build_event_doc(
        self,
        *,
        trade_id: str,
        event_type: str,
        ts: int,
        symbol: str,
        user_id: str,
        mode: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        doc = {
            "trade_id": trade_id,
            "event_type": event_type,
            "ts": int(ts),
            "symbol": symbol,
            "user_id": user_id,
            "mode": mode,
            "payload": self._compact_payload(payload or {}),
            "sent": False,
            "sent_at": None,
            "delivered_channels": [],
            "notification_status": "PENDING",
            "retryable": True,
            "send_attempt_count": 0,
            "next_retry_at": None,
            "locked_at": None,
            "locked_by": None,
            "created_at": now,
            "updated_at": now,
        }
        if idempotency_key:
            doc["idempotency_key"] = idempotency_key
        return doc

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

    async def insert_event_docs(
        self,
        docs: list[dict[str, Any]],
        *,
        ignore_duplicate_idempotency: bool = False,
    ) -> int:
        if not docs:
            return 0
        try:
            result = await self.col.insert_many(docs, ordered=False)
            return len(result.inserted_ids)
        except BulkWriteError as exc:
            if not ignore_duplicate_idempotency:
                raise
            write_errors = list(exc.details.get("writeErrors", [])) if isinstance(exc.details, dict) else []
            if any(int(item.get("code", 0)) != 11000 for item in write_errors):
                raise
            inserted = exc.details.get("nInserted", 0) if isinstance(exc.details, dict) else 0
            return int(inserted)

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
        row.setdefault("notification_status", "PENDING")
        row.setdefault("retryable", True)
        row.setdefault("send_attempt_count", 0)
        row.setdefault("next_retry_at", None)
        return row
