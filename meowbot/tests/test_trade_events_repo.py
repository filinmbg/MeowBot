import pytest

from meowbot.infra.mongo.client import MongoConn, MongoConfig
from meowbot.infra.mongo.migrations import apply_migrations
from meowbot.infra.mongo.repos.trade_events_repo import TradeEventsRepositoryMongo


@pytest.mark.integration
def test_trade_events_repo_add_event():
    conn = MongoConn(MongoConfig(db_name="meowbot_test"))
    if not conn.ping():
        pytest.skip("Mongo not running")

    apply_migrations(conn.db)
    conn.db["trade_events"].delete_many({})

    repo = TradeEventsRepositoryMongo(conn.db)
    repo.add_event(
        trade_id="t1",
        event_type="OPENED",
        ts=123,
        symbol="BTCUSDT",
        user_id="u1",
        mode="sandbox",
        payload={"entry_price": 100.0},
    )

    doc = conn.db["trade_events"].find_one({"trade_id": "t1", "event_type": "OPENED"})
    assert doc is not None
    assert doc["symbol"] == "BTCUSDT"
    assert doc["payload"]["entry_price"] == 100.0