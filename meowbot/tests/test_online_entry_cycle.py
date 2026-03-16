import pytest

from meowbot.infra.mongo.client import MongoConn, MongoConfig
from meowbot.infra.mongo.migrations import apply_migrations
from meowbot.infra.mongo.repos.bars_repo import BarsRepositoryMongo
from meowbot.infra.mongo.repos.trades_repo import TradesRepositoryMongo
from meowbot.infra.mongo.repos.bot_state_repo import BotStateRepositoryMongo

from meowbot.infra.memory.exchange import FakeExchange
from meowbot.core.services.execution.entry.portfolio_gate import PortfolioGate
from meowbot.core.domain.types import Bar, Signal
from meowbot.core.domain.enums import SignalAction
from meowbot.core.usecases.online_entry_cycle import OnlineEntryCycleUseCase
from meowbot.infra.mongo.repos.trade_events_repo import TradeEventsRepositoryMongo
from meowbot.infra.broker.paper import PaperBroker

broker = PaperBroker()

class AlwaysLong:
    def decide(self, bar: Bar) -> Signal:
        return Signal(action=SignalAction.LONG, score=1.0)


def make_bar(i: int) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        tf="15m",
        open_time=i * 60_000,
        close_time=(i + 1) * 60_000,
        o=1, h=1, l=1, c=1, v=1,
        features_ok=False,
        features_ver="v1",
    )


@pytest.mark.integration
def test_online_entry_only_on_new_bars():
    conn = MongoConn(MongoConfig(db_name="meowbot_test"))
    if not conn.ping():
        pytest.skip("Mongo not running")

    apply_migrations(conn.db, timeframes=("15m",))
    conn.db["bot_state"].delete_many({})
    conn.db["bars_15m"].delete_many({})
    conn.db["trades"].delete_many({})

    exchange = FakeExchange([make_bar(i) for i in range(3)])

    bars_repo = BarsRepositoryMongo(conn.db)
    trades_repo = TradesRepositoryMongo(conn.db)
    bot_state_repo = BotStateRepositoryMongo(conn.db)
    trade_events_repo = TradeEventsRepositoryMongo(conn.db)
    broker = PaperBroker()

    gate = PortfolioGate(trades_repo)

    uc = OnlineEntryCycleUseCase(
        bars_repo=bars_repo,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        exchange=exchange,
        strategy=AlwaysLong(),
        gate=gate,
        broker=broker,
        trade_events_repo=trade_events_repo,
        features_ver="v1",
    )
    # перший запуск
    uc.run("BTCUSDT", "15m", now_ms=999999)

    cursor = bot_state_repo.get_int("entry_cursor:BTCUSDT:15m:v1")
    assert cursor > 0

    # другий запуск без нових барів
    uc.run("BTCUSDT", "15m", now_ms=999999)

    cursor2 = bot_state_repo.get_int("entry_cursor:BTCUSDT:15m:v1")
    assert cursor == cursor2