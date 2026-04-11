from __future__ import annotations

from dataclasses import replace

from meowbot.core.domain.types import Bar
from meowbot.core.usecases.ensure_market_data import EnsureMarketDataUseCase


class FakeBarsRepo:
    def __init__(self) -> None:
        self.storage: list[Bar] = []
        self.upsert_calls = 0

    def ping(self) -> bool:
        return True

    def get_last_close_time(self, symbol: str, tf: str, features_ver: str):
        filtered = [
            x for x in self.storage
            if x.symbol == symbol and x.tf == tf and x.features_ver == features_ver
        ]
        if not filtered:
            return None
        return max(x.close_time for x in filtered)

    def get_tail(
        self,
        symbol: str,
        tf: str,
        n: int,
        features_ver: str,
        require_features_ok: bool = True,
    ):
        filtered = [
            x for x in self.storage
            if x.symbol == symbol and x.tf == tf and x.features_ver == features_ver
        ]
        if require_features_ok:
            filtered = [x for x in filtered if x.features_ok]
        filtered.sort(key=lambda item: item.close_time)
        return filtered[-n:]

    def upsert_many(self, bars):
        self.upsert_calls += 1
        by_close: dict[int, Bar] = {x.close_time: x for x in self.storage}
        for bar in bars:
            by_close[bar.close_time] = bar
        self.storage = sorted(by_close.values(), key=lambda item: item.close_time)


class FakeExchange:
    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars

    def ping(self) -> bool:
        return True

    def get_last_closed_time(self, symbol: str, tf: str):
        filtered = [x for x in self.bars if x.symbol == symbol and x.tf == tf]
        if not filtered:
            return None
        return max(x.close_time for x in filtered)

    def fetch_klines(self, symbol: str, tf: str, start_ms: int, end_ms: int):
        return [
            x for x in self.bars
            if x.symbol == symbol
            and x.tf == tf
            and start_ms < x.close_time <= end_ms
        ]


def _make_bar(index: int, close_price: float) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        tf="1h",
        open_time=index * 3_600_000,
        close_time=(index + 1) * 3_600_000 - 1,
        o=close_price - 1,
        h=close_price + 2,
        l=close_price - 2,
        c=close_price,
        v=1000 + index,
        features=None,
        features_ok=False,
        features_ver="raw",
    )


def test_ensure_market_data_builds_and_saves_features() -> None:
    source_bars = [_make_bar(i, 100 + i * 0.25) for i in range(260)]

    bars_repo = FakeBarsRepo()
    exchange = FakeExchange(source_bars)

    uc = EnsureMarketDataUseCase(
        bars_repo=bars_repo,
        exchange=exchange,
        features_ver="v2_core",
        required_tail=150,
    )

    uc.run("BTCUSDT", "1h")

    assert bars_repo.upsert_calls == 1
    assert len(bars_repo.storage) > 0
    assert bars_repo.storage[-1].features_ok is True
    assert bars_repo.storage[-1].features is not None
    assert "rsi14" in bars_repo.storage[-1].features
    assert "supertrend_bullish_10_3_0" in bars_repo.storage[-1].features