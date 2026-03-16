import pytest

from meowbot.infra.mongo.client import MongoConn, MongoConfig
from meowbot.infra.mongo.migrations import apply_migrations
from meowbot.infra.mongo.repos.trades_repo import TradesRepositoryMongo

from meowbot.core.domain.types import Trade, Bar, Signal
from meowbot.core.domain.enums import Side, TradeStatus, SignalAction
from meowbot.core.usecases.run_entry_cycle import RunEntryCycleUseCase
from meowbot.core.usecases.reconcile_trades import ReconcileOpenTradesUseCase
from meowbot.core.services.execution.entry.portfolio_gate import PortfolioGate
from meowbot.infra.memory.bars_repo import InMemoryBarsRepo
from meowbot.infra.memory.broker import InMemoryBroker
from meowbot.infra.broker.paper import PaperBroker


class AlwaysLongStrategy:
    def decide(self, bar: Bar) -> Signal:
        return Signal(action=SignalAction.LONG, score=1.0)


def make_bar(i: int, close: float) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        tf="15m",
        open_time=i * 60_000,
        close_time=(i + 1) * 60_000,
        o=close, h=close, l=close, c=close, v=1.0,
        features_ok=True,
        features_ver="v1",
    )


def make_m1_bar(i: int, low: float, high: float, close: float) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        tf="1m",
        open_time=i * 60_000,
        close_time=(i + 1) * 60_000,
        o=close, h=high, l=low, c=close, v=1.0,
        features_ok=False,
        features_ver="v1",
    )


@pytest.mark.integration
def test_mongo_trades_repo_entry_and_dedupe_and_reconcile():
    conn = MongoConn(MongoConfig(uri="mongodb://localhost:27017", db_name="meowbot_test"))
    if not conn.ping():
        pytest.skip("MongoDB is not running")

    apply_migrations(conn.db, timeframes=("1m", "15m"))

    # clean
    conn.db["trades"].delete_many({})

    trades_repo = TradesRepositoryMongo(conn.db)
    gate = PortfolioGate(trades_repo)
    broker = InMemoryBroker()

    # bars (in-memory ok for this integration)
    bars_repo = InMemoryBarsRepo()
    bars_repo.upsert_many([make_bar(0, 110)])

    uc_entry = RunEntryCycleUseCase(
        bars_repo=bars_repo,
        trades_repo=trades_repo,
        broker=broker,
        entry_strategy=AlwaysLongStrategy(),
        gate=gate,
        features_ver="v1",
    )

    tid1 = uc_entry.run("BTCUSDT", "15m", now_ms=999_000, sl_price=100.0)
    assert tid1 is not None

    # антидубль по тому ж бару має спрацювати
    tid2 = uc_entry.run("BTCUSDT", "15m", now_ms=999_500, sl_price=100.0)
    assert tid2 is None

    # reconcile: зробимо фейкову біржу з 1m барами, які б'ють SL
    from meowbot.infra.memory.exchange import FakeExchange
    ex = FakeExchange([
        make_m1_bar(0, low=105, high=112, close=110),
        make_m1_bar(1, low=99, high=111, close=100),   # SL=100 hit
    ])

    broker = PaperBroker()
    uc_exit = ReconcileOpenTradesUseCase(trades_repo, ex, broker)
    uc_exit.run(now_ms=2 * 60_000 + 5_000)

    assert trades_repo.get_open_trade_by_symbol("BTCUSDT") is None