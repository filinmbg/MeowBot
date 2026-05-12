from __future__ import annotations

import pytest
from pymongo.errors import DuplicateKeyError

from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.infra.mongo.repos.trades_repo import TradesRepositoryMongo
from meowbot.infra.mongo.repos_async.trades_repo_async import TradesRepositoryMongoAsync


def _trade_doc_with_extra_fields() -> dict:
    return {
        "_id": "mongo-id",
        "trade_id": "tg:1:BTCUSDT:15m:1:rule",
        "user_id": "tg:1",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "status": "OPEN",
        "opened_at": 1,
        "entry_price": 100.0,
        "qty": 1.0,
        "leverage": 5,
        "stake_usd": 20.0,
        "tf_entry": "15m",
        "model_id": "rule",
        "entry_bar_close_time": 1,
        "sl_price": 98.0,
        "updated_at": 123,
        "created_at": 100,
        "is_open": True,
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "closed": False,
        "legacy_field": "ignored",
    }


def test_async_trades_repo_ignores_unknown_mongo_fields() -> None:
    repo = TradesRepositoryMongoAsync({"trades": None})

    trade = repo._from_doc(_trade_doc_with_extra_fields())

    assert trade.trade_id == "tg:1:BTCUSDT:15m:1:rule"
    assert trade.side == Side.LONG
    assert trade.status == TradeStatus.OPEN


def test_sync_trades_repo_ignores_unknown_mongo_fields() -> None:
    repo = TradesRepositoryMongo({"trades": None})

    trade = repo._from_doc(_trade_doc_with_extra_fields())

    assert trade.trade_id == "tg:1:BTCUSDT:15m:1:rule"
    assert trade.side == Side.LONG
    assert trade.status == TradeStatus.OPEN


class _AsyncCursor:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def sort(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    async def to_list(self, length: int | None = None):
        return list(self.rows if length is None else self.rows[:length])


class _AsyncInsertResult:
    inserted_id = "id"


class _AsyncUpdateResult:
    def __init__(self, modified_count: int) -> None:
        self.modified_count = modified_count


class _AsyncAggregate:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def to_list(self, length: int | None = None):
        return list(self.rows if length is None else self.rows[:length])


class _FakeTradesCollection:
    def __init__(self, docs: list[dict] | None = None, *, duplicate_on_insert: bool = False) -> None:
        self.docs = list(docs or [])
        self.duplicate_on_insert = duplicate_on_insert
        self.created_indexes: list[tuple] = []

    async def create_index(self, keys, **kwargs):
        self.created_indexes.append((list(keys), dict(kwargs)))
        return kwargs.get("name")

    async def insert_one(self, doc: dict):
        if self.duplicate_on_insert:
            raise DuplicateKeyError("duplicate key")
        self.docs.append(dict(doc))
        return _AsyncInsertResult()

    def aggregate(self, pipeline):
        groups: dict[tuple[str, str], list[str]] = {}
        for doc in self.docs:
            if doc.get("status") != "OPEN":
                continue
            key = (str(doc.get("user_id")), str(doc.get("symbol")))
            groups.setdefault(key, []).append(str(doc.get("trade_id")))
        rows = [
            {"_id": {"user_id": user_id, "symbol": symbol}, "count": len(ids), "trade_ids": ids}
            for (user_id, symbol), ids in groups.items()
            if len(ids) > 1
        ]
        return _AsyncAggregate(rows)

    def find(self, query: dict):
        rows = []
        for doc in self.docs:
            match = True
            for key, expected in query.items():
                if isinstance(expected, dict) and "$ne" in expected:
                    if doc.get(key) == expected["$ne"]:
                        match = False
                        break
                elif doc.get(key) != expected:
                    match = False
                    break
            if match:
                rows.append(doc)
        return _AsyncCursor(rows)

    async def update_many(self, query: dict, update: dict):
        ids = set(query.get("trade_id", {}).get("$in", []))
        user_id = query.get("user_id")
        status = query.get("status")
        modified = 0
        for doc in self.docs:
            if ids and doc.get("trade_id") not in ids:
                continue
            if user_id is not None and doc.get("user_id") != user_id:
                continue
            if status is not None and doc.get("status") != status:
                continue
            doc.update(update.get("$set", {}))
            modified += 1
        return _AsyncUpdateResult(modified)


@pytest.mark.anyio("asyncio")
async def test_async_trades_repo_creates_unique_open_trade_index() -> None:
    col = _FakeTradesCollection()
    repo = TradesRepositoryMongoAsync({"trades": col})

    await repo.ensure_indexes()

    assert any(
        kwargs.get("name") == "uniq_open_trade_user_symbol"
        and kwargs.get("unique") is True
        and kwargs.get("partialFilterExpression") == {"status": "OPEN"}
        for _, kwargs in col.created_indexes
    )


@pytest.mark.anyio("asyncio")
async def test_duplicate_open_insert_is_blocked_by_repo() -> None:
    col = _FakeTradesCollection(duplicate_on_insert=True)
    repo = TradesRepositoryMongoAsync({"trades": col})

    with pytest.raises(RuntimeError, match="duplicate_open_trade"):
        await repo.create_trade(repo._from_doc(_trade_doc_with_extra_fields()))


@pytest.mark.anyio("asyncio")
async def test_startup_duplicate_cleanup_keeps_canonical_trade() -> None:
    docs = [
        {
            **_trade_doc_with_extra_fields(),
            "trade_id": "old",
            "opened_at": 100,
            "created_at": 100,
            "mode": "live",
            "exchange_position_amt": 0.0,
        },
        {
            **_trade_doc_with_extra_fields(),
            "trade_id": "canonical",
            "opened_at": 200,
            "created_at": 200,
            "mode": "live",
            "exchange_position_amt": 1.0,
            "exchange_entry_order_id": "order-1",
        },
        {
            **_trade_doc_with_extra_fields(),
            "trade_id": "newer_but_unconfirmed",
            "opened_at": 300,
            "created_at": 300,
            "mode": "live",
            "exchange_position_amt": 0.0,
        },
    ]
    col = _FakeTradesCollection(docs)
    repo = TradesRepositoryMongoAsync({"trades": col})

    summaries = await repo.cleanup_duplicate_open_trades(now_ms=999)

    assert summaries == [
        {
            "user_id": "tg:1",
            "symbol": "BTCUSDT",
            "duplicate_count": 3,
            "canonical_trade_id": "canonical",
            "superseded_trade_ids": ["old", "newer_but_unconfirmed"],
        }
    ]
    statuses = {doc["trade_id"]: doc["status"] for doc in col.docs}
    assert statuses["canonical"] == "OPEN"
    assert statuses["old"] == "DUPLICATE_SUPERSEDED"
    assert statuses["newer_but_unconfirmed"] == "DUPLICATE_SUPERSEDED"


@pytest.mark.anyio("asyncio")
async def test_placeholder_debug_open_trades_are_suspended() -> None:
    docs = [
        {**_trade_doc_with_extra_fields(), "trade_id": "debug", "user_id": "tg:123456789", "status": "OPEN"},
        {**_trade_doc_with_extra_fields(), "trade_id": "real", "user_id": "tg:1", "status": "OPEN"},
    ]
    col = _FakeTradesCollection(docs)
    repo = TradesRepositoryMongoAsync({"trades": col})

    count = await repo.suspend_placeholder_open_trades(now_ms=999)

    assert count == 1
    statuses = {doc["trade_id"]: doc["status"] for doc in col.docs}
    assert statuses["debug"] == "DUPLICATE_SUPERSEDED"
    assert statuses["real"] == "OPEN"
