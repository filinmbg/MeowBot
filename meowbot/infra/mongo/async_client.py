from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from fastapi import Request

try:
    from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "motor is not installed. Install it with: pip install motor"
    ) from exc


load_dotenv()


@dataclass(frozen=True)
class AsyncMongoConfig:
    uri: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    db_name: str = os.getenv("MONGO_DB_NAME", "meowbot")

    server_selection_timeout_ms: int = int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000"))
    connect_timeout_ms: int = int(os.getenv("MONGO_CONNECT_TIMEOUT_MS", "5000"))
    socket_timeout_ms: int = int(os.getenv("MONGO_SOCKET_TIMEOUT_MS", "10000"))

    max_pool_size: int = int(os.getenv("MONGO_MAX_POOL_SIZE", "30"))
    min_pool_size: int = int(os.getenv("MONGO_MIN_POOL_SIZE", "5"))
    wait_queue_timeout_ms: int = int(os.getenv("MONGO_WAIT_QUEUE_TIMEOUT_MS", "5000"))


class AsyncMongoConn:
    def __init__(self, config: AsyncMongoConfig):
        self.config = config
        self.client: AsyncIOMotorClient | None = None
        self.db: AsyncIOMotorDatabase | None = None

    async def connect(self) -> None:
        if self.client is not None and self.db is not None:
            return

        self.client = AsyncIOMotorClient(
            self.config.uri,
            serverSelectionTimeoutMS=self.config.server_selection_timeout_ms,
            connectTimeoutMS=self.config.connect_timeout_ms,
            socketTimeoutMS=self.config.socket_timeout_ms,
            maxPoolSize=self.config.max_pool_size,
            minPoolSize=self.config.min_pool_size,
            waitQueueTimeoutMS=self.config.wait_queue_timeout_ms,
        )
        await self.client.admin.command("ping")
        self.db = self.client[self.config.db_name]

    async def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
            self.db = None

    async def ping(self) -> bool:
        try:
            if self.client is None:
                return False
            await self.client.admin.command("ping")
            return True
        except Exception:
            return False


def get_async_mongo_conn_from_request(request: Request) -> AsyncMongoConn:
    mongo = getattr(request.app.state, "mongo_async", None)
    if mongo is None:
        raise RuntimeError("Async Mongo is not initialized in app.state.mongo_async")
    return mongo


def get_async_mongo_db_from_request(request: Request):
    mongo = get_async_mongo_conn_from_request(request)
    if mongo.db is None:
        raise RuntimeError("Async Mongo database is not initialized")
    return mongo.db