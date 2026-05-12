from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from pymongo.errors import OperationFailure

from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Bar, Trade
from meowbot.core.services.mongo_write_queue import MongoWriteQueue


class FakeBarsRepo:
    def __init__(self) -> None:
        self.batches: list[list[Bar]] = []

    async def upsert_many(self, bars: list[Bar]) -> None:
        self.batches.append(list(bars))


class FakeBotStateRepo:
    def __init__(self) -> None:
        self.values: list[dict[str, int]] = []

    async def set_many_ints(self, values: dict[str, int]) -> None:
        self.values.append(dict(values))


class FakeTradesRepo:
    def __init__(self) -> None:
        self.batches: list[list[Trade]] = []

    async def bulk_upsert_trades(self, trades: list[Trade]) -> None:
        self.batches.append(list(trades))


class QuotaFailingTradesRepo:
    async def bulk_upsert_trades(self, trades: list[Trade]) -> None:
        raise OperationFailure("you are over your space quota, using 512 MB of 512 MB")


class FakeTradeEventsRepo:
    def __init__(self) -> None:
        self.docs: list[list[dict]] = []

    def build_event_doc(self, **kwargs):
        return dict(kwargs)

    async def insert_event_docs(self, docs: list[dict], *, ignore_duplicate_idempotency: bool = False) -> int:
        self.docs.append(list(docs))
        return len(docs)


class FakeNotifier:
    enabled = True

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, text: str, *, chat_id: str | None = None) -> None:
        self.messages.append(text)


class FakeAdminAlertService:
    def __init__(self) -> None:
        self.alerts: list[dict] = []

    async def send_alert(self, **kwargs) -> None:
        self.alerts.append(dict(kwargs))


def _make_trade(trade_id: str = "trade-1") -> Trade:
    return Trade(
        trade_id=trade_id,
        user_id="tg:1",
        symbol="BTCUSDT",
        side=Side.LONG,
        status=TradeStatus.OPEN,
        opened_at=1,
        entry_price=100.0,
        qty=1.0,
        leverage=5,
        stake_usd=10.0,
        tf_entry="15m",
        model_id="RSI_REBOUND_ST_124",
        entry_bar_close_time=1,
        sl_price=98.0,
        mode="sandbox",
    )


def _make_bar(close_time: int) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        tf="15m",
        open_time=close_time - 60_000,
        close_time=close_time,
        o=100.0,
        h=101.0,
        l=99.0,
        c=100.5,
        v=10.0,
        features={},
        features_ok=True,
        features_ver="v2_core",
    )


def test_mongo_write_queue_dedupes_bot_state_to_latest_value() -> None:
    asyncio.run(_run_mongo_write_queue_dedupes_bot_state_to_latest_value())


async def _run_mongo_write_queue_dedupes_bot_state_to_latest_value() -> None:
    queue = MongoWriteQueue(
        bars_repo=FakeBarsRepo(),
        bot_state_repo=FakeBotStateRepo(),
        trades_repo=FakeTradesRepo(),
        trade_events_repo=FakeTradeEventsRepo(),
        max_queue_size=100,
        high_watermark=80,
        flush_interval_seconds=0.05,
        health_interval_seconds=10,
    )
    await queue.start()

    queue.enqueue_bot_state_set("entry_cursor:BTCUSDT:15m", 100)
    queue.enqueue_bot_state_set("entry_cursor:BTCUSDT:15m", 200)

    await asyncio.sleep(0.2)
    await queue.stop(flush_timeout_seconds=1)

    assert queue.bot_state_repo.values
    assert queue.bot_state_repo.values[-1] == {"entry_cursor:BTCUSDT:15m": 200}


def test_mongo_write_queue_persists_critical_trade_updates() -> None:
    asyncio.run(_run_mongo_write_queue_persists_critical_trade_updates())


async def _run_mongo_write_queue_persists_critical_trade_updates() -> None:
    trades_repo = FakeTradesRepo()
    queue = MongoWriteQueue(
        bars_repo=FakeBarsRepo(),
        bot_state_repo=FakeBotStateRepo(),
        trades_repo=trades_repo,
        trade_events_repo=FakeTradeEventsRepo(),
        max_queue_size=100,
        high_watermark=80,
        flush_interval_seconds=0.05,
        health_interval_seconds=10,
    )
    await queue.start()

    trade = _make_trade()
    queue.enqueue_trade_upsert(trade)
    queue.enqueue_trade_upsert(replace(trade, realized_pnl_usd=1.25))

    await asyncio.sleep(0.2)
    await queue.stop(flush_timeout_seconds=1)

    assert trades_repo.batches
    assert sum(len(batch) for batch in trades_repo.batches) == 1
    persisted_trade = trades_repo.batches[-1][-1]
    assert persisted_trade.realized_pnl_usd == 1.25


def test_mongo_write_queue_drops_low_priority_bars_under_backpressure() -> None:
    asyncio.run(_run_mongo_write_queue_drops_low_priority_bars_under_backpressure())


async def _run_mongo_write_queue_drops_low_priority_bars_under_backpressure() -> None:
    queue = MongoWriteQueue(
        bars_repo=FakeBarsRepo(),
        bot_state_repo=FakeBotStateRepo(),
        trades_repo=FakeTradesRepo(),
        trade_events_repo=FakeTradeEventsRepo(),
        max_queue_size=2,
        high_watermark=1,
        flush_interval_seconds=5,
        health_interval_seconds=10,
        persist_bars=True,
    )

    kept = queue.enqueue_bars_upsert_many([_make_bar(100_000)])
    dropped = queue.enqueue_bars_upsert_many([_make_bar(200_000)])

    assert kept is True
    assert dropped is False
    assert queue._stats.dropped_low_priority >= 1


def test_mongo_write_queue_job_type_counters_include_pending_buffers() -> None:
    queue = MongoWriteQueue(
        bars_repo=FakeBarsRepo(),
        bot_state_repo=FakeBotStateRepo(),
        trades_repo=FakeTradesRepo(),
        trade_events_repo=FakeTradeEventsRepo(),
        max_queue_size=100,
        high_watermark=80,
        flush_interval_seconds=5,
        health_interval_seconds=10,
        persist_bars=True,
    )

    queue.enqueue_trade_upsert(_make_trade("trade-1"))
    queue.enqueue_trade_event(
        trade_id="trade-1",
        event_type="OPENED",
        ts=1,
        symbol="BTCUSDT",
        user_id="tg:1",
        mode="sandbox",
        payload={},
        priority="critical",
    )
    queue.enqueue_bot_state_set("cursor", 1)
    queue.enqueue_bars_upsert_many([_make_bar(100_000)])

    counts = queue._job_type_counts()

    assert counts["trade_upsert"] == 1
    assert counts["trade_event_insert"] == 1
    assert counts["bot_state_set"] == 1
    assert counts["bars_upsert_many"] == 1


def test_mongo_write_queue_skips_bars_when_persistence_disabled() -> None:
    queue = MongoWriteQueue(
        bars_repo=FakeBarsRepo(),
        bot_state_repo=FakeBotStateRepo(),
        trades_repo=FakeTradesRepo(),
        trade_events_repo=FakeTradeEventsRepo(),
        max_queue_size=100,
        high_watermark=80,
        flush_interval_seconds=5,
        health_interval_seconds=10,
        persist_bars=False,
    )

    assert queue.enqueue_bars_upsert_many([_make_bar(100_000)]) is True
    assert queue.pending_jobs == 0
    assert queue._job_type_counts()["bars_upsert_many"] == 0


def test_mongo_write_queue_writes_quota_failed_critical_jobs_to_jsonl(tmp_path) -> None:
    asyncio.run(_run_mongo_write_queue_writes_quota_failed_critical_jobs_to_jsonl(tmp_path))


async def _run_mongo_write_queue_writes_quota_failed_critical_jobs_to_jsonl(tmp_path) -> None:
    failed_path = tmp_path / "failed_writes.jsonl"
    alerts = FakeAdminAlertService()
    queue = MongoWriteQueue(
        bars_repo=FakeBarsRepo(),
        bot_state_repo=FakeBotStateRepo(),
        trades_repo=QuotaFailingTradesRepo(),
        trade_events_repo=FakeTradeEventsRepo(),
        admin_alert_service=alerts,
        max_queue_size=100,
        high_watermark=80,
        flush_interval_seconds=0.05,
        health_interval_seconds=10,
        emergency_failed_writes_path=failed_path,
    )
    await queue.start()

    queue.enqueue_trade_upsert(_make_trade("quota-trade-1"))
    queue.enqueue_trade_upsert(_make_trade("quota-trade-2"))

    await asyncio.sleep(0.2)
    await queue.stop(flush_timeout_seconds=1)

    rows = [json.loads(line) for line in failed_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[0]["operation_type"] == "trade_upsert"
    assert rows[0]["dedupe_key"] == "quota-trade-1"
    assert rows[0]["payload"]["trade_id"] == "quota-trade-1"
    assert rows[1]["dedupe_key"] == "quota-trade-2"
    assert alerts.alerts
    assert alerts.alerts[-1]["alert_key"] == "mongo_quota_full"
