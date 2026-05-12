from __future__ import annotations

import asyncio

from meowbot.infra.mongo import client as sync_client
from meowbot.infra.mongo import async_client
from meowbot.infra.mongo.async_client import AsyncMongoConfig
from meowbot.infra.mongo.client import MongoConfig, MongoConn


class FakeAdmin:
    def __init__(self) -> None:
        self.commands: list[str] = []

    async def command(self, name: str) -> dict:
        self.commands.append(name)
        return {"ok": 1}


class FakeMotorClient:
    instances: list["FakeMotorClient"] = []

    def __init__(self, uri: str, **kwargs) -> None:
        self.uri = uri
        self.kwargs = kwargs
        self.admin = FakeAdmin()
        self.closed = False
        FakeMotorClient.instances.append(self)

    def __getitem__(self, db_name: str) -> dict:
        return {"db_name": db_name}

    def close(self) -> None:
        self.closed = True


class FakeSyncAdmin:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, name: str) -> dict:
        self.commands.append(name)
        return {"ok": 1}


class FakeSyncClient:
    instances: list["FakeSyncClient"] = []

    def __init__(self, uri: str, **kwargs) -> None:
        self.uri = uri
        self.kwargs = kwargs
        self.admin = FakeSyncAdmin()
        self.closed = False
        FakeSyncClient.instances.append(self)

    def __getitem__(self, db_name: str) -> dict:
        return {"db_name": db_name}

    def close(self) -> None:
        self.closed = True


def test_get_async_mongo_client_reuses_process_client(monkeypatch):
    asyncio.run(_run_shared_client_check(monkeypatch))


async def _run_shared_client_check(monkeypatch):
    await async_client.close_async_mongo_client()
    FakeMotorClient.instances.clear()
    monkeypatch.setattr(async_client, "AsyncIOMotorClient", FakeMotorClient)

    config = AsyncMongoConfig(
        uri="mongodb://example",
        db_name="meowbot_test",
        server_selection_timeout_ms=20000,
        connect_timeout_ms=20000,
        socket_timeout_ms=30000,
        max_pool_size=5,
        min_pool_size=0,
        max_idle_time_ms=30000,
        retry_reads=True,
        retry_writes=True,
        app_name="MeowBot",
    )

    first = await async_client.get_async_mongo_client(config)
    second = await async_client.get_async_mongo_client(config)
    db = await async_client.get_async_mongo_db(config)

    assert first is second
    assert db == {"db_name": "meowbot_test"}
    assert len(FakeMotorClient.instances) == 1
    assert first.kwargs["serverSelectionTimeoutMS"] == 20000
    assert first.kwargs["connectTimeoutMS"] == 20000
    assert first.kwargs["socketTimeoutMS"] == 30000
    assert first.kwargs["maxPoolSize"] == 5
    assert first.kwargs["minPoolSize"] == 0
    assert first.kwargs["maxIdleTimeMS"] == 30000
    assert first.kwargs["retryReads"] is True
    assert first.kwargs["retryWrites"] is True
    assert first.kwargs["appName"] == "MeowBot"

    await async_client.close_async_mongo_client()
    assert first.closed is True


def test_sync_mongo_client_uses_runtime_safe_config(monkeypatch):
    FakeSyncClient.instances.clear()
    monkeypatch.setattr(sync_client, "MongoClient", FakeSyncClient)

    config = MongoConfig(
        uri="mongodb://example",
        db_name="meowbot_test",
        server_selection_timeout_ms=20000,
        connect_timeout_ms=20000,
        socket_timeout_ms=30000,
        max_pool_size=5,
        min_pool_size=0,
        max_idle_time_ms=30000,
        retry_reads=True,
        retry_writes=True,
        app_name="MeowBot",
    )
    conn = MongoConn(config)
    conn.connect()

    assert conn.db == {"db_name": "meowbot_test"}
    assert len(FakeSyncClient.instances) == 1
    first = FakeSyncClient.instances[0]
    assert first.kwargs["serverSelectionTimeoutMS"] == 20000
    assert first.kwargs["connectTimeoutMS"] == 20000
    assert first.kwargs["socketTimeoutMS"] == 30000
    assert first.kwargs["maxPoolSize"] == 5
    assert first.kwargs["minPoolSize"] == 0
    assert first.kwargs["maxIdleTimeMS"] == 30000
    assert first.kwargs["retryReads"] is True
    assert first.kwargs["retryWrites"] is True
    assert first.kwargs["appName"] == "MeowBot"

    conn.close()
    assert first.closed is True
