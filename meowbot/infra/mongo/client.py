from __future__ import annotations

import os
import logging
import multiprocessing
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import Request
from pymongo import MongoClient
from pymongo.database import Database


load_dotenv()
log = logging.getLogger("meowbot")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _mongo_uri_hosts_only(uri: str) -> str:
    parsed = urlparse(uri)
    netloc = parsed.netloc or uri
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    return netloc.split("/", 1)[0] or "unknown"


@dataclass(frozen=True)
class MongoConfig:
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


class MongoConn:
    def __init__(self, config: MongoConfig):
        self.config = config
        self.client: MongoClient | None = None
        self.db: Database | None = None

    def connect(self) -> None:
        if self.client is not None and self.db is not None:
            return

        self.client = MongoClient(
            self.config.uri,
            serverSelectionTimeoutMS=self.config.server_selection_timeout_ms,
            connectTimeoutMS=self.config.connect_timeout_ms,
            socketTimeoutMS=self.config.socket_timeout_ms,
            maxPoolSize=self.config.max_pool_size,
            minPoolSize=self.config.min_pool_size,
            maxIdleTimeMS=self.config.max_idle_time_ms,
            waitQueueTimeoutMS=self.config.wait_queue_timeout_ms,
            retryReads=self.config.retry_reads,
            retryWrites=self.config.retry_writes,
            appName=self.config.app_name,
        )
        log.info(
            "[mongo] client config connectTimeoutMS=%s socketTimeoutMS=%s "
            "serverSelectionTimeoutMS=%s maxPoolSize=%s minPoolSize=%s "
            "maxIdleTimeMS=%s retryReads=%s retryWrites=%s appName=%s",
            self.config.connect_timeout_ms,
            self.config.socket_timeout_ms,
            self.config.server_selection_timeout_ms,
            self.config.max_pool_size,
            self.config.min_pool_size,
            self.config.max_idle_time_ms,
            self.config.retry_reads,
            self.config.retry_writes,
            self.config.app_name,
        )
        log.info(
            "[mongo] creating sync client pid=%s process=%s client_id=%s host=%s maxPoolSize=%s minPoolSize=%s maxIdleTimeMS=%s",
            os.getpid(),
            multiprocessing.current_process().name,
            id(self.client),
            _mongo_uri_hosts_only(self.config.uri),
            self.config.max_pool_size,
            self.config.min_pool_size,
            self.config.max_idle_time_ms,
        )
        self.client.admin.command("ping")
        self.db = self.client[self.config.db_name]

    def close(self) -> None:
        if self.client is not None:
            client_id = id(self.client)
            self.client.close()
            self.client = None
            self.db = None
            log.info("[mongo] sync client closed pid=%s client_id=%s", os.getpid(), client_id)

    def ping(self) -> bool:
        try:
            if self.client is None:
                return False
            self.client.admin.command("ping")
            return True
        except Exception:
            return False


def get_mongo_conn_from_request(request: Request) -> MongoConn:
    mongo = getattr(request.app.state, "mongo", None)
    if mongo is None:
        raise RuntimeError("Mongo is not initialized in app.state.mongo")
    return mongo


def get_mongo_db(request: Request):
    mongo = get_mongo_conn_from_request(request)
    if mongo.db is None:
        raise RuntimeError("Mongo database is not initialized")
    return mongo.db
