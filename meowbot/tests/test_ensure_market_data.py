from meowbot.core.domain.types import Bar
from meowbot.core.usecases.ensure_market_data import EnsureMarketDataUseCase
from meowbot.infra.memory.bars_repo import InMemoryBarsRepo
from meowbot.infra.memory.exchange import FakeExchange
from dataclasses import replace


def make_bar(i: int) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        tf="15m",
        open_time=i * 60_000,
        close_time=(i + 1) * 60_000,
        o=1, h=2, l=0.5, c=1.5, v=10,
        features_ok=False,
        features_ver="v1",
    )


def test_ensure_market_data_fetches_missing():
    exchange_bars = [make_bar(i) for i in range(10)]

    repo = InMemoryBarsRepo()
    exchange = FakeExchange(exchange_bars)

    uc = EnsureMarketDataUseCase(
        bars_repo=repo,
        exchange=exchange,
        features_ver="v1",
        required_tail=5,
    )

    uc.run("BTCUSDT", "15m")

    tail = repo.get_tail("BTCUSDT", "15m", 5, "v1")
    assert len(tail) == 5
    assert exchange.fetch_calls == 1
    assert all(b.features_ok for b in tail)


def test_ensure_market_data_no_fetch_if_enough():
    exchange_bars = [make_bar(i) for i in range(10)]

    repo = InMemoryBarsRepo()
    exchange = FakeExchange(exchange_bars)

    repo.upsert_many(
        [replace(b, features_ok=True) for b in exchange_bars]
    )

    uc = EnsureMarketDataUseCase(
        bars_repo=repo,
        exchange=exchange,
        features_ver="v1",
        required_tail=5,
    )

    uc.run("BTCUSDT", "15m")

    assert exchange.fetch_calls == 0
