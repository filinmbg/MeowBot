from __future__ import annotations


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