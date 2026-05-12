from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio("asyncio")

from meowbot.core.domain.types import Bar
from meowbot.core.usecases.async_ensure_market_data import AsyncEnsureMarketDataUseCase


class FakeBarsRepoAsync:
    def __init__(self) -> None:
        self.storage: list[Bar] = []

    async def get_last_close_time(self, symbol: str, tf: str, features_ver: str):
        rows = [x for x in self.storage if x.symbol == symbol and x.tf == tf and x.features_ver == features_ver]
        if not rows:
            return None
        return max(x.close_time for x in rows)

    async def get_tail(self, *, symbol: str, tf: str, n: int, features_ver: str, require_features_ok: bool = True):
        rows = [x for x in self.storage if x.symbol == symbol and x.tf == tf and x.features_ver == features_ver]
        if require_features_ok:
            rows = [x for x in rows if x.features_ok]
        rows.sort(key=lambda x: x.close_time)
        return rows[-n:]

    async def upsert_many(self, bars: list[Bar]) -> None:
        by_close = {x.close_time: x for x in self.storage}
        for bar in bars:
            by_close[bar.close_time] = bar
        self.storage = sorted(by_close.values(), key=lambda x: x.close_time)


class FakeExchangeAsync:
    def __init__(self, bars: list[Bar], *, last_closed_time: int | None = None) -> None:
        self.bars = bars
        self.fetch_calls = 0
        self.last_closed_time = last_closed_time

    async def get_last_closed_time(self, symbol: str, tf: str):
        if self.last_closed_time is not None:
            return self.last_closed_time
        rows = [x for x in self.bars if x.symbol == symbol and x.tf == tf]
        if not rows:
            return None
        return max(x.close_time for x in rows)

    async def fetch_klines(self, *, symbol: str, tf: str, start_ms, end_ms, limit: int = 500):
        self.fetch_calls += 1
        rows = [x for x in self.bars if x.symbol == symbol and x.tf == tf]
        rows.sort(key=lambda x: x.close_time)

        if start_ms is not None:
            rows = [x for x in rows if x.close_time > start_ms]
        if end_ms is not None:
            rows = [x for x in rows if x.close_time <= end_ms]

        return rows[-limit:]


def _make_bar(i: int) -> Bar:
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
        features=None,
        features_ok=False,
        features_ver="raw",
    )


@pytest.mark.anyio
async def test_async_warmup_and_sync() -> None:
    source = [_make_bar(i) for i in range(520)]

    bars_repo = FakeBarsRepoAsync()
    exchange = FakeExchangeAsync(source)

    uc = AsyncEnsureMarketDataUseCase(
        bars_repo=bars_repo,
        exchange=exchange,
        features_ver="v2_core",
        startup_history_bars=500,
        runtime_window_bars=300,
    )

    warmed_close = await uc.warmup("BTCUSDT", "15m")
    assert warmed_close is not None

    bar_ready, last_close, reason = await uc.sync_if_new_bar("BTCUSDT", "15m")
    assert isinstance(bar_ready, bool)
    assert last_close is not None
    assert reason in {"no_new_bar", "new_bar_synced", "warmup_created"}

    ready_rows = [x for x in bars_repo.storage if x.features_ok]
    assert ready_rows


@pytest.mark.anyio
async def test_async_warmup_rebuilds_existing_rows_missing_v2_runtime_features() -> None:
    source = [_make_bar(i) for i in range(520)]

    bars_repo = FakeBarsRepoAsync()
    stale = source[-1]
    bars_repo.storage = [
        Bar(
            symbol=stale.symbol,
            tf=stale.tf,
            open_time=stale.open_time,
            close_time=stale.close_time,
            o=stale.o,
            h=stale.h,
            l=stale.l,
            c=stale.c,
            v=stale.v,
            features={"rsi14": 50.0},
            features_ok=True,
            features_ver="v2_core",
        )
    ]
    exchange = FakeExchangeAsync(source)

    uc = AsyncEnsureMarketDataUseCase(
        bars_repo=bars_repo,
        exchange=exchange,
        features_ver="v2_core",
        startup_history_bars=500,
        runtime_window_bars=300,
    )

    warmed_close = await uc.warmup("BTCUSDT", "15m")

    assert warmed_close == source[-1].close_time
    assert exchange.fetch_calls == 1
    assert len(bars_repo.storage) > 1
    assert bars_repo.storage[-1].features_ok is True
    assert "adx_14" in bars_repo.storage[-1].features
    assert "vol_peak_offset_10" in bars_repo.storage[-1].features


@pytest.mark.anyio
async def test_async_warmup_fetches_only_closed_bars() -> None:
    source = [_make_bar(i) for i in range(520)]
    partial = Bar(
        symbol="BTCUSDT",
        tf="15m",
        open_time=520 * 900_000,
        close_time=(521 * 900_000) - 1,
        o=620,
        h=621,
        l=619,
        c=620.5,
        v=1.0,
        features=None,
        features_ok=False,
        features_ver="raw",
    )

    bars_repo = FakeBarsRepoAsync()
    exchange = FakeExchangeAsync(source + [partial], last_closed_time=source[-1].close_time)

    uc = AsyncEnsureMarketDataUseCase(
        bars_repo=bars_repo,
        exchange=exchange,
        features_ver="v2_core",
        startup_history_bars=500,
        runtime_window_bars=300,
    )

    warmed_close = await uc.warmup("BTCUSDT", "15m")

    assert warmed_close == source[-1].close_time
    assert bars_repo.storage[-1].close_time == source[-1].close_time
    assert all(row.close_time != partial.close_time for row in bars_repo.storage)
