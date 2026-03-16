import pytest

from meowbot.infra.mongo.client import MongoConn, MongoConfig
from meowbot.infra.mongo.migrations import apply_migrations


@pytest.mark.integration
def test_mongo_ping_and_migrations():
    conn = MongoConn(MongoConfig(uri="mongodb://localhost:27017", db_name="meowbot_test"))
    if not conn.ping():
        pytest.skip("MongoDB is not running on localhost:27017")

    # migrations повинні застосовуватись без помилок і повторно
    apply_migrations(conn.db, timeframes=("1m", "15m"))
    apply_migrations(conn.db, timeframes=("1m", "15m"))