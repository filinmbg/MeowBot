import pytest

from meowbot.infra.mongo.client import MongoConn, MongoConfig
from meowbot.infra.mongo.migrations import apply_migrations, bars_collection_name
from meowbot.infra.mongo.repos.bars_repo import BarsRepositoryMongo

from meowbot.infra.memory.exchange import FakeExchange
from meowbot.core.domain.types import Bar
from meowbot.core.usecases.ensure_market_data import EnsureMarketDataUseCase


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


@pytest.mark.integration
def test_ensure_market_data_with_mongo_bars_repo():
    conn = MongoConn(MongoConfig(uri="mongodb://localhost:27017", db_name="meowbot_test"))
    if not conn.ping():
        pytest.skip("MongoDB is not running")

    apply_migrations(conn.db, timeframes=("15m",))

    # чистимо колекцію bars_15m
    conn.db[bars_collection_name("15m")].delete_many({})

    exchange_bars = [make_bar(i) for i in range(10)]
    exchange = FakeExchange(exchange_bars)

    bars_repo = BarsRepositoryMongo(conn.db)

    uc = EnsureMarketDataUseCase(
        bars_repo=bars_repo,
        exchange=exchange,
        features_ver="v1",
        required_tail=5,
    )

    # 1) перший прогін має догрузити
    uc.run("BTCUSDT", "15m")
    assert exchange.fetch_calls == 1

    tail = bars_repo.get_tail("BTCUSDT", "15m", 5, "v1")
    assert len(tail) == 5
    assert all(b.features_ok for b in tail)

    # перевіримо що в базі реально є 10 барів
    count = conn.db[bars_collection_name("15m")].count_documents({"symbol": "BTCUSDT", "features_ver": "v1"})
    assert count == 10

    # 2) другий прогін не повинен робити fetch (даних вже досить)
    uc.run("BTCUSDT", "15m")
    assert exchange.fetch_calls == 1