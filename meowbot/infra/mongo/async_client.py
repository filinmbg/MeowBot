from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import Request

try:
    from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "motor is not installed. Install it with: pip install motor"
    ) from exc


load_dotenv()
log = logging.getLogger("meowbot")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AsyncMongoConfig:
    uri: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    db_name: str = os.getenv("MONGO_DB_NAME", "meowbot")

    server_selection_timeout_ms: int = int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "20000"))
    connect_timeout_ms: int = int(os.getenv("MONGO_CONNECT_TIMEOUT_MS", "20000"))
    socket_timeout_ms: int = int(os.getenv("MONGO_SOCKET_TIMEOUT_MS", "30000"))

    max_pool_size: int = int(os.getenv("MONGO_MAX_POOL_SIZE", "5"))
    min_pool_size: int = int(os.getenv("MONGO_MIN_POOL_SIZE", "0"))
    max_idle_time_ms: int = int(os.getenv("MONGO_MAX_IDLE_TIME_MS", "30000"))
    wait_queue_timeout_ms: int = int(os.getenv("MONGO_WAIT_QUEUE_TIMEOUT_MS", "10000"))
    retry_reads: bool = _env_bool("MONGO_RETRY_READS", True)
    retry_writes: bool = _env_bool("MONGO_RETRY_WRITES", True)
    app_name: str = os.getenv("MONGO_APP_NAME", "MeowBot")


_shared_async_mongo_client: AsyncIOMotorClient | None = None
_shared_async_mongo_config: AsyncMongoConfig | None = None
_shared_async_mongo_lock: asyncio.Lock | None = None


def _get_shared_lock() -> asyncio.Lock:
    global _shared_async_mongo_lock
    if _shared_async_mongo_lock is None:
        _shared_async_mongo_lock = asyncio.Lock()
    return _shared_async_mongo_lock


def _mongo_uri_hosts_only(uri: str) -> str:
    parsed = urlparse(uri)
    netloc = parsed.netloc or uri
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    return netloc.split("/", 1)[0] or "unknown"


def _build_async_mongo_client(config: AsyncMongoConfig) -> AsyncIOMotorClient:
    return AsyncIOMotorClient(
        config.uri,
        serverSelectionTimeoutMS=config.server_selection_timeout_ms,
        connectTimeoutMS=config.connect_timeout_ms,
        socketTimeoutMS=config.socket_timeout_ms,
        maxPoolSize=config.max_pool_size,
        minPoolSize=config.min_pool_size,
        maxIdleTimeMS=config.max_idle_time_ms,
        waitQueueTimeoutMS=config.wait_queue_timeout_ms,
        retryReads=config.retry_reads,
        retryWrites=config.retry_writes,
        appName=config.app_name,
    )


async def get_async_mongo_client(config: AsyncMongoConfig | None = None) -> AsyncIOMotorClient:
    """Return the process-wide async Mongo client.

    Motor clients own connection pools. Keeping one client per process avoids
    multiplying Atlas connections from Telegram handlers and background tasks.
    """
    global _shared_async_mongo_client, _shared_async_mongo_config

    config = config or AsyncMongoConfig()
    if _shared_async_mongo_client is not None:
        if _shared_async_mongo_config and _shared_async_mongo_config.uri != config.uri:
            log.warning("[mongo] shared async client already initialized; ignoring different uri")
        log.debug(
            "[mongo] reusing async client pid=%s client_id=%s",
            os.getpid(),
            id(_shared_async_mongo_client),
        )
        return _shared_async_mongo_client

    async with _get_shared_lock():
        if _shared_async_mongo_client is not None:
            log.debug(
                "[mongo] reusing async client pid=%s client_id=%s",
                os.getpid(),
                id(_shared_async_mongo_client),
            )
            return _shared_async_mongo_client

        client = _build_async_mongo_client(config)
        log.info(
            "[mongo] client config connectTimeoutMS=%s socketTimeoutMS=%s "
            "serverSelectionTimeoutMS=%s maxPoolSize=%s minPoolSize=%s "
            "maxIdleTimeMS=%s retryReads=%s retryWrites=%s appName=%s",
            config.connect_timeout_ms,
            config.socket_timeout_ms,
            config.server_selection_timeout_ms,
            config.max_pool_size,
            config.min_pool_size,
            config.max_idle_time_ms,
            config.retry_reads,
            config.retry_writes,
            config.app_name,
        )
        log.info(
            "[mongo] creating async client pid=%s process=%s client_id=%s appName=%s host=%s maxPoolSize=%s minPoolSize=%s maxIdleTimeMS=%s",
            os.getpid(),
            multiprocessing.current_process().name,
            id(client),
            config.app_name,
            _mongo_uri_hosts_only(config.uri),
            config.max_pool_size,
            config.min_pool_size,
            config.max_idle_time_ms,
        )
        try:
            await client.admin.command("ping")
        except Exception:
            client.close()
            raise

        _shared_async_mongo_client = client
        _shared_async_mongo_config = config
        log.info(
            "[mongo] async client connected pid=%s client_id=%s db=%s maxPoolSize=%s minPoolSize=%s maxIdleTimeMS=%s socketTimeoutMS=%s",
            os.getpid(),
            id(client),
            config.db_name,
            config.max_pool_size,
            config.min_pool_size,
            config.max_idle_time_ms,
            config.socket_timeout_ms,
        )
        return client


async def get_async_mongo_db(config: AsyncMongoConfig | None = None) -> AsyncIOMotorDatabase:
    config = config or AsyncMongoConfig()
    client = await get_async_mongo_client(config)
    return client[config.db_name]


async def close_async_mongo_client() -> None:
    global _shared_async_mongo_client, _shared_async_mongo_config

    async with _get_shared_lock():
        if _shared_async_mongo_client is None:
            return
        client_id = id(_shared_async_mongo_client)
        _shared_async_mongo_client.close()
        _shared_async_mongo_client = None
        _shared_async_mongo_config = None
        log.info("[mongo] async client closed pid=%s client_id=%s", os.getpid(), client_id)


class AsyncMongoConn:
    def __init__(self, config: AsyncMongoConfig):
        self.config = config
        self.client: AsyncIOMotorClient | None = None
        self.db: AsyncIOMotorDatabase | None = None

    async def connect(self) -> None:
        if self.client is not None and self.db is not None:
            return

        self.client = await get_async_mongo_client(self.config)
        self.db = self.client[self.config.db_name]

    async def close(self) -> None:
        if self.client is not None:
            await close_async_mongo_client()
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
