from __future__ import annotations

import anyio
import pytest

from meowbot.core.services.runtime.bot_state_cache import BotStateCache, CacheBackedBotStateRepositoryAsync


pytestmark = pytest.mark.anyio("asyncio")


class FakeBotStatePersistenceRepo:
    def __init__(self) -> None:
        self.rows = {"entry_cursor:BTCUSDT:15m:v2_core": 123}
        self.set_calls: list[tuple[str, int]] = []
        self.get_calls = 0

    async def list_ints(self) -> dict[str, int]:
        self.get_calls += 1
        return dict(self.rows)

    async def set_int(self, key: str, value: int) -> None:
        self.set_calls.append((key, value))
        self.rows[key] = value


async def test_bot_state_cache_preloads_once_and_reads_from_memory() -> None:
    persistence = FakeBotStatePersistenceRepo()
    repo = CacheBackedBotStateRepositoryAsync(
        cache=BotStateCache(),
        persistence_repo=persistence,
    )

    await repo.preload_from_persistence()
    assert await repo.get_int("entry_cursor:BTCUSDT:15m:v2_core", default=0) == 123

    persistence.rows["entry_cursor:BTCUSDT:15m:v2_core"] = 999
    assert await repo.get_int("entry_cursor:BTCUSDT:15m:v2_core", default=0) == 123
    assert persistence.get_calls == 1

    await repo.set_int("entry_cursor:BTCUSDT:15m:v2_core", 456)
    assert await repo.get_int("entry_cursor:BTCUSDT:15m:v2_core", default=0) == 456

    await anyio.sleep(0)
    assert persistence.set_calls == [("entry_cursor:BTCUSDT:15m:v2_core", 456)]


async def test_bot_state_cache_locks_are_in_memory() -> None:
    repo = CacheBackedBotStateRepositoryAsync(cache=BotStateCache(), persistence_repo=None)

    assert await repo.acquire_lock(key="entry_lock:tg:1:BTCUSDT", owner="a", ttl_ms=60_000) is True
    assert await repo.acquire_lock(key="entry_lock:tg:1:BTCUSDT", owner="b", ttl_ms=60_000) is False

    await repo.release_lock(key="entry_lock:tg:1:BTCUSDT", owner="a")
    assert await repo.acquire_lock(key="entry_lock:tg:1:BTCUSDT", owner="b", ttl_ms=60_000) is True
