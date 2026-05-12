from __future__ import annotations

import anyio
import pytest

from meowbot.core.domain.types import Bar
from meowbot.core.services.runtime.bars_cache import BarsCache, CacheBackedBarsRepositoryAsync


pytestmark = pytest.mark.anyio("asyncio")


class FakePersistenceRepo:
    def __init__(self) -> None:
        self.upsert_batches: list[list[Bar]] = []

    async def upsert_many(self, bars: list[Bar]) -> None:
        self.upsert_batches.append(list(bars))


def _make_bar(i: int, *, features_ver: str = "v2_core", features_ok: bool = True) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        tf="15m",
        open_time=i * 900_000,
        close_time=(i + 1) * 900_000 - 1,
        o=100 + i,
        h=101 + i,
        l=99 + i,
        c=100.5 + i,
        v=1000 + i,
        features={"rsi14": 50.0 + i},
        features_ok=features_ok,
        features_ver=features_ver,
    )


async def test_cache_backed_bars_repo_reads_from_memory_and_persists_in_background() -> None:
    cache = BarsCache(maxlen=3)
    persistence = FakePersistenceRepo()
    repo = CacheBackedBarsRepositoryAsync(cache=cache, persistence_repo=persistence)

    await repo.upsert_many([_make_bar(i) for i in range(5)])

    tail = await repo.get_tail(
        symbol="BTCUSDT",
        tf="15m",
        n=5,
        features_ver="v2_core",
        require_features_ok=True,
    )
    last_close = await repo.get_last_close_time("BTCUSDT", "15m", "v2_core")

    assert [bar.close_time for bar in tail] == [_make_bar(i).close_time for i in range(2, 5)]
    assert last_close == _make_bar(4).close_time

    await anyio.sleep(0)
    assert len(persistence.upsert_batches) == 1
    assert len(persistence.upsert_batches[0]) == 5


async def test_bars_cache_filters_features_version_and_features_ok() -> None:
    cache = BarsCache(maxlen=10)
    repo = CacheBackedBarsRepositoryAsync(cache=cache, persist_writes=False)
    await repo.upsert_many(
        [
            _make_bar(1, features_ver="raw", features_ok=False),
            _make_bar(2, features_ver="v2_core", features_ok=False),
            _make_bar(3, features_ver="v2_core", features_ok=True),
        ]
    )

    ready = await repo.get_tail(
        symbol="BTCUSDT",
        tf="15m",
        n=10,
        features_ver="v2_core",
        require_features_ok=True,
    )
    all_v2 = await repo.get_tail(
        symbol="BTCUSDT",
        tf="15m",
        n=10,
        features_ver="v2_core",
        require_features_ok=False,
    )

    assert [bar.close_time for bar in ready] == [_make_bar(3).close_time]
    assert [bar.close_time for bar in all_v2] == [_make_bar(2).close_time, _make_bar(3).close_time]
