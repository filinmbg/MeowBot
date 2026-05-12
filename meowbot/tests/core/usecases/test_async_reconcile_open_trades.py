from __future__ import annotations

import logging

import pytest
import anyio

from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Bar, Trade
from meowbot.core.services.runtime.active_trades_cache import ActiveTradesCache
from meowbot.core.usecases.async_reconcile_open_trades import AsyncReconcileOpenTradesUseCase
from meowbot.infra.broker.paper import PaperBroker


class FakeTradesRepoAsync:
    def __init__(self, trades: list[Trade]) -> None:
        self.trades = trades

    async def get_open_trades(self) -> list[Trade]:
        return [
            trade
            for trade in self.trades
            if (trade.status.value if hasattr(trade.status, "value") else str(trade.status)) == "OPEN"
        ]

    async def update_trade(self, trade: Trade) -> None:
        for index, row in enumerate(self.trades):
            if row.trade_id == trade.trade_id:
                self.trades[index] = trade
                return
        self.trades.append(trade)


class FakeTradeEventsRepoAsync:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def add_event(self, **kwargs) -> None:
        self.events.append(kwargs)


class FakePriceProvider:
    def __init__(self, price: float) -> None:
        self.price = price

    def get_price(self, symbol: str) -> float | None:
        return self.price


class FakeBarsRepoAsync:
    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars
        self.calls: list[dict] = []

    async def get_tail(self, **kwargs) -> list[Bar]:
        self.calls.append(kwargs)
        return list(self.bars)


class SlowTradesRepoAsync(FakeTradesRepoAsync):
    async def update_trade(self, trade: Trade) -> None:
        await anyio.sleep(0.02)
        await super().update_trade(trade)


class FailingExchange:
    async def get_klines(self, *args, **kwargs):
        raise AssertionError("sandbox replay must not call Binance/exchange klines")


def _make_trade() -> Trade:
    return Trade(
        trade_id="demo_user:BTCUSDT:1h:1:RSI_REBOUND_ST_124",
        user_id="demo_user",
        symbol="BTCUSDT",
        side=Side.LONG,
        status=TradeStatus.OPEN,
        opened_at=1,
        entry_price=100.0,
        qty=10.0,
        leverage=20,
        stake_usd=50.0,
        tf_entry="1h",
        model_id="RSI_REBOUND_ST_124",
        entry_bar_close_time=1,
        sl_price=98.0,
        mode="sandbox",
        tp_hit_count=0,
        remaining_pct=1.0,
        exit_last_check_at=1,
        qty_remaining=10.0,
        realized_pnl_usd=0.0,
    )


@pytest.mark.anyio
async def test_async_reconcile_hits_tp3_and_closes() -> None:
    trade = _make_trade()
    trades_repo = FakeTradesRepoAsync([trade])
    events_repo = FakeTradeEventsRepoAsync()
    price_provider = FakePriceProvider(101.6)
    active_trades_cache = ActiveTradesCache()
    active_trades_cache.load_open_trades([trade], source="test")

    uc = AsyncReconcileOpenTradesUseCase(
        trades_repo=trades_repo,
        broker=PaperBroker(),
        trade_events_repo=events_repo,
        price_provider=price_provider,
        max_concurrency=4,
        active_trades_cache=active_trades_cache,
    )

    await uc.run(now_ms=180_000)

    updated = trades_repo.trades[0]
    assert updated.status == TradeStatus.CLOSED
    assert updated.tp_hit_count == 3
    assert updated.qty_remaining == 0.0
    assert updated.realized_pnl_usd > 0
    assert active_trades_cache.get_open_trade(user_id=trade.user_id, symbol=trade.symbol, mode=trade.mode) is None

    event_types = [event["event_type"] for event in events_repo.events]
    assert event_types.count("TP_HIT") == 2
    assert "CLOSED" in event_types


@pytest.mark.anyio
async def test_async_reconcile_quarantines_invalid_symbol_without_price_or_exchange() -> None:
    trade = _make_trade()
    trade.symbol = "\u5e01\u5b89\u4eba\u751fUSDT"
    trades_repo = FakeTradesRepoAsync([trade])
    events_repo = FakeTradeEventsRepoAsync()
    price_provider = FakePriceProvider(101.6)
    active_trades_cache = ActiveTradesCache()
    active_trades_cache.load_open_trades([trade], source="test")

    uc = AsyncReconcileOpenTradesUseCase(
        trades_repo=trades_repo,
        broker=PaperBroker(),
        trade_events_repo=events_repo,
        price_provider=price_provider,
        max_concurrency=4,
        active_trades_cache=active_trades_cache,
    )

    await uc.run(now_ms=180_000)

    updated = trades_repo.trades[0]
    assert updated.status == TradeStatus.ERROR_INVALID_SYMBOL
    assert updated.exit_reason == "ERROR_INVALID_SYMBOL"
    assert active_trades_cache.get_all_open_trades() == []
    invalid_events = [event for event in events_repo.events if event["event_type"] == "INVALID_SYMBOL_SKIPPED"]
    assert invalid_events
    assert invalid_events[0]["payload"]["action"] == "quarantined_without_binance_call"


@pytest.mark.anyio
async def test_async_reconcile_limits_sandbox_replay_batch_per_tick() -> None:
    trades = []
    for index, symbol in enumerate(["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"], start=1):
        trade = _make_trade()
        trade.trade_id = f"demo_user:{symbol}:1h:{index}:RSI_REBOUND_ST_124"
        trade.symbol = symbol
        trades.append(trade)

    trades_repo = FakeTradesRepoAsync(list(trades))
    events_repo = FakeTradeEventsRepoAsync()
    price_provider = FakePriceProvider(100.2)
    active_trades_cache = ActiveTradesCache()
    active_trades_cache.load_open_trades(trades, source="test")

    uc = AsyncReconcileOpenTradesUseCase(
        trades_repo=trades_repo,
        broker=PaperBroker(),
        trade_events_repo=events_repo,
        price_provider=price_provider,
        max_concurrency=4,
        active_trades_cache=active_trades_cache,
        max_replay_trades_per_tick=2,
    )

    await uc.run(now_ms=180_000)

    assert uc.last_metrics["replay_candidates"] == 5
    assert uc.last_metrics["replay_processed"] == 2
    assert uc.last_metrics["replay_skipped"] == 3
    assert sum(1 for trade in trades_repo.trades if trade.exit_last_check_at == 180_000) == 0


@pytest.mark.anyio
async def test_async_reconcile_sandbox_replay_uses_cached_bars_not_exchange() -> None:
    trade = _make_trade()
    bars_repo = FakeBarsRepoAsync(
        [
            Bar(
                symbol="BTCUSDT",
                tf="1m",
                open_time=60_000,
                close_time=120_000,
                o=100.0,
                h=101.6,
                l=99.0,
                c=100.5,
                v=1.0,
                features_ver="v2_core",
            )
        ]
    )
    trades_repo = FakeTradesRepoAsync([trade])
    events_repo = FakeTradeEventsRepoAsync()
    active_trades_cache = ActiveTradesCache()
    active_trades_cache.load_open_trades([trade], source="test")

    uc = AsyncReconcileOpenTradesUseCase(
        trades_repo=trades_repo,
        broker=PaperBroker(),
        trade_events_repo=events_repo,
        price_provider=FakePriceProvider(100.2),
        active_trades_cache=active_trades_cache,
        bars_repo=bars_repo,
        exchange=FailingExchange(),
        reconcile_tf="1m",
    )

    await uc.run(now_ms=180_000)

    updated = trades_repo.trades[0]
    assert updated.status == TradeStatus.CLOSED
    assert bars_repo.calls
    assert bars_repo.calls[0]["symbol"] == "BTCUSDT"
    assert bars_repo.calls[0]["tf"] == "1m"
    assert uc.last_metrics["replay_processed"] == 1


@pytest.mark.anyio
async def test_async_reconcile_hard_stops_replay_when_budget_exhausted() -> None:
    trades = []
    for index, symbol in enumerate(["BTCUSDT", "ETHUSDT", "BNBUSDT"], start=1):
        trade = _make_trade()
        trade.trade_id = f"demo_user:{symbol}:1h:{index}:RSI_REBOUND_ST_124"
        trade.symbol = symbol
        trades.append(trade)

    trades_repo = SlowTradesRepoAsync(list(trades))
    events_repo = FakeTradeEventsRepoAsync()
    active_trades_cache = ActiveTradesCache()
    active_trades_cache.load_open_trades(trades, source="test")

    uc = AsyncReconcileOpenTradesUseCase(
        trades_repo=trades_repo,
        broker=PaperBroker(),
        trade_events_repo=events_repo,
        price_provider=FakePriceProvider(101.6),
        active_trades_cache=active_trades_cache,
        max_replay_trades_per_tick=3,
        max_replay_runtime_ms=1,
    )

    await uc.run(now_ms=180_000)

    assert uc.last_metrics["replay_processed"] == 1
    assert uc.last_metrics["replay_budget_exhausted"] is True
    assert uc.last_metrics["replay_skipped"] == 2


@pytest.mark.anyio
async def test_async_reconcile_hides_noop_trade_metrics_by_default(caplog) -> None:
    trade = _make_trade()
    trades_repo = FakeTradesRepoAsync([trade])
    events_repo = FakeTradeEventsRepoAsync()
    active_trades_cache = ActiveTradesCache()
    active_trades_cache.load_open_trades([trade], source="test")

    uc = AsyncReconcileOpenTradesUseCase(
        trades_repo=trades_repo,
        broker=PaperBroker(),
        trade_events_repo=events_repo,
        price_provider=FakePriceProvider(100.2),
        active_trades_cache=active_trades_cache,
    )

    caplog.set_level(logging.INFO, logger="meowbot")
    await uc.run(now_ms=180_000)

    assert "exit-replay-trade-metrics" not in caplog.text
    assert trades_repo.trades[0].exit_last_check_at == 1
