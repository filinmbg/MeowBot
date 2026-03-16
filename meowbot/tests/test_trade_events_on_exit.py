import pytest

from meowbot.infra.mongo.client import MongoConn, MongoConfig
from meowbot.infra.mongo.migrations import apply_migrations
from meowbot.infra.mongo.repos.trade_events_repo import TradeEventsRepositoryMongo
from meowbot.infra.broker.paper import PaperBroker

from meowbot.core.domain.types import Bar, Trade
from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.services.execution.exit.tp_sl_cascade import apply_tp_sl_cascade


def make_bar(i: int, low: float, high: float, close: float) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        tf="1m",
        open_time=i * 60_000,
        close_time=(i + 1) * 60_000,
        o=close,
        h=high,
        l=low,
        c=close,
        v=1.0,
        features_ok=False,
        features_ver="v1",
    )


def make_trade() -> Trade:
    return Trade(
        trade_id="t1",
        user_id="u1",
        mode="sandbox",
        symbol="BTCUSDT",
        side=Side.LONG,
        status=TradeStatus.OPEN,
        opened_at=0,
        entry_price=100.0,
        qty=1.0,
        leverage=20,
        stake_usd=1.0,
        tf_entry="15m",
        model_id="m1",
        entry_bar_close_time=0,
        sl_price=99.0,
    )


@pytest.mark.integration
def test_exit_events_tp1_and_sl_moved():
    conn = MongoConn(MongoConfig(db_name="meowbot_test"))
    if not conn.ping():
        pytest.skip("Mongo not running")

    apply_migrations(conn.db)
    conn.db["trade_events"].delete_many({})

    repo = TradeEventsRepositoryMongo(conn.db)
    broker = PaperBroker()
    trade = make_trade()

    bars = [make_bar(0, low=100.0, high=100.6, close=100.5)]
    updated = apply_tp_sl_cascade(trade, bars, broker, repo)

    events = list(
        conn.db["trade_events"]
        .find({"trade_id": "t1"}, {"_id": 0, "event_type": 1})
        .sort("ts", 1)
    )

    event_types = [e["event_type"] for e in events]

    assert updated.tp_hit_count == 1
    assert "TP1_HIT" in event_types
    assert "SL_MOVED" in event_types