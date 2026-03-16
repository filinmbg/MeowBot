from __future__ import annotations

from pymongo.database import Database
from meowbot.core.ports.bot_state_repo import BotStateRepository


class BotStateRepositoryMongo(BotStateRepository):
    def __init__(self, db: Database, collection_name: str = "bot_state"):
        self.col = db[collection_name]

    def get_int(self, key: str, default: int = 0) -> int:
        doc = self.col.find_one({"key": key})
        if not doc:
            return default
        return int(doc.get("value", default))

    def set_int(self, key: str, value: int) -> None:
        self.col.update_one(
            {"key": key},
            {"$set": {"value": int(value)}},
            upsert=True,
        )