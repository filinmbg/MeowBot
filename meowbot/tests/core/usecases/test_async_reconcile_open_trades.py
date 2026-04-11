from __future__ import annotations

import pytest

from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Bar, Trade
from meowbot.core.usecases.async_reconcile_open_trades import AsyncReconcileOpenTradesUseCase
from meowbot.infra.broker.paper import PaperBroker


class FakeTradesRepoAsync:
    def __init__(self, trades: list[Trade]) -> None:
        self.trades = trades

    async def get_open_trades(self) -> list[Trade]:
        return [
            t for t in self.trades
            if (t.status.value if hasattr(t.status, "value") else str(t.status)) == "OPEN"
        ]

    async def update_trade(self, trade: Trade) -> None:
        for idx, row in enumerate(self.trades):
            if row.trade_id == trade.trade_id:
                self.trades[idx] = trade
                return
        self.trades.append(trade)


class FakeExchangeAsync:
    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars

    async def fetch_klines(self, *, symbol: str, tf: str, start_ms, end_ms, limit: int = 1500):
        rows = [b for b in self.bars if b.symbol == symbol and b.tf == tf]
        rows.sort(key=lambda x: x.close_time)
        if start_ms is not None:
            rows = [b for b in rows if b.close_time > start_ms]
        if end_ms is not None:
            rows = [b for b in rows if b.close_time <= end_ms]
        return rows[-limit:]


class FakeTradeEventsRepoAsync:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def add_event(self, **kwargs) -> None:
        self.events.append(kwargs)


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


def _make_bar(i: int, high: float, low: float, close: float) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        tf="1m",
        open_time=i * 60_000,
        close_time=(i + 1) * 60_000 - 1,
        o=100.0,
        h=high,
        l=low,
        c=close,
        v=1000.0,
        features=None,
        features_ok=False,
        features_ver="raw",
    )


@pytest.mark.anyio
async def test_async_reconcile_hits_tp3_and_closes() -> None:
    trade = _make_trade()

    bars = [
        _make_bar(1, high=100.6, low=100.1, close=100.5),  # TP1
        _make_bar(2, high=101.1, low=100.6, close=101.0),  # TP2
        _make_bar(3, high=101.6, low=101.0, close=101.5),  # TP3
    ]

    trades_repo = FakeTradesRepoAsync([trade])
    exchange = FakeExchangeAsync(bars)
    events_repo = FakeTradeEventsRepoAsync()

    uc = AsyncReconcileOpenTradesUseCase(
        trades_repo=trades_repo,
        exchange=exchange,
        broker=PaperBroker(),
        trade_events_repo=events_repo,
        max_concurrency=4,
    )

    await uc.run(now_ms=bars[-1].close_time)

    updated = trades_repo.trades[0]
    assert updated.status == TradeStatus.CLOSED
    assert updated.tp_hit_count == 3
    assert updated.qty_remaining == 0.0
    assert updated.realized_pnl_usd > 0

    event_types = [e["event_type"] for e in events_repo.events]
    assert "TP_HIT" in event_types
    assert "CLOSED" in event_types