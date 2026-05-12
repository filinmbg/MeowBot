from __future__ import annotations

import time

from pymongo import ReturnDocument
from pymongo import UpdateOne
from pymongo.errors import DuplicateKeyError


class BotStateRepositoryMongoAsync:
    def __init__(self, db):
        self.col = db["bot_state"]

    async def get_int(self, key: str, default: int = 0) -> int:
        doc = await self.col.find_one({"key": key})
        if not doc:
            return default
        value = doc.get("value_int")
        return int(value) if value is not None else default

    async def set_int(self, key: str, value: int) -> None:
        await self.col.update_one(
            {"key": key},
            {"$set": {"key": key, "value_int": int(value)}},
            upsert=True,
        )

    async def set_many_ints(self, values: dict[str, int]) -> None:
        if not values:
            return
        operations = [
            UpdateOne(
                {"key": str(key)},
                {"$set": {"key": str(key), "value_int": int(value)}},
                upsert=True,
            )
            for key, value in values.items()
        ]
        await self.col.bulk_write(operations, ordered=False)

    async def list_ints(self) -> dict[str, int]:
        cursor = self.col.find(
            {"value_int": {"$exists": True}},
            projection={"key": 1, "value_int": 1},
        )
        rows: dict[str, int] = {}
        async for doc in cursor:
            key = doc.get("key")
            value = doc.get("value_int")
            if key is None or value is None:
                continue
            rows[str(key)] = int(value)
        return rows

    async def acquire_lock(self, *, key: str, owner: str, ttl_ms: int = 120_000) -> bool:
        now_ms = int(time.time() * 1000)
        expires_at_ms = now_ms + max(int(ttl_ms), 1)
        try:
            doc = await self.col.find_one_and_update(
                {
                    "key": key,
                    "$or": [
                        {"lock_expires_at_ms": {"$lt": now_ms}},
                        {"lock_expires_at_ms": {"$exists": False}},
                        {"lock_owner": owner},
                    ],
                },
                {
                    "$set": {
                        "key": key,
                        "lock_owner": owner,
                        "lock_acquired_at_ms": now_ms,
                        "lock_expires_at_ms": expires_at_ms,
                    }
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            return False

        return bool(doc and doc.get("lock_owner") == owner)

    async def release_lock(self, *, key: str, owner: str) -> None:
        await self.col.delete_one({"key": key, "lock_owner": owner})
