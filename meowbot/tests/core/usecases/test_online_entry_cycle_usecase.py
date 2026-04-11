from __future__ import annotations

from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Bar, Trade
from meowbot.core.usecases.online_entry_cycle import OnlineEntryCycleUseCase


class FakeBarsRepo:
    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars

    def get_tail(
        self,
        symbol: str,
        tf: str,
        n: int,
        features_ver: str,
        require_features_ok: bool = True,
    ):
        result = [
            b for b in self.bars
            if b.symbol == symbol and b.tf == tf and b.features_ver == features_ver
        ]
        if require_features_ok:
            result = [b for b in result if b.features_ok]
        result.sort(key=lambda x: x.close_time)
        return result[-n:]


class FakeTradesRepo:
    def __init__(self) -> None:
        self.created: list[Trade] = []
        self.open_trades: list[Trade] = []

    def create_trade(self, trade: Trade) -> None:
        self.created.append(trade)
        self.open_trades.append(trade)

    def get_open_trades(self):
        return [
            t for t in self.open_trades
            if (t.status.value if hasattr(t.status, "value") else str(t.status)) == "OPEN"
        ]


class FakeBotStateRepo:
    def __init__(self, initial: dict[str, int] | None = None) -> None:
        self.data = initial or {}

    def get_int(self, key: str, default: int = 0) -> int:
        return self.data.get(key, default)

    def set_int(self, key: str, value: int) -> None:
        self.data[key] = value


class FakeExchange:
    def __init__(self, last_closed_time: int) -> None:
        self.last_closed_time = last_closed_time

    def get_last_closed_time(self, symbol: str, tf: str):
        return self.last_closed_time


class FakeStrategy:
    pass


class FakeGate:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def allow(self, symbol, signal) -> bool:
        return self.allowed


class FakeBroker:
    def open_position(self, trade: Trade) -> Trade:
        return trade


class FakeTradeEventsRepo:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def add_event(self, **kwargs) -> None:
        self.events.append(kwargs)


def _make_bar(
    *,
    idx: int,
    rsi14: float,
    supertrend: int,
) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        tf="30m",
        open_time=idx * 1000,
        close_time=idx * 1000 + 999,
        o=100.0,
        h=101.0,
        l=99.0,
        c=100.0 + idx,
        v=1000.0,
        features={
            "rsi14": float(rsi14),
            "supertrend_bullish_10_3_0": float(supertrend),
        },
        features_ok=True,
        features_ver="v2_core",
    )


def test_online_entry_cycle_creates_trade_for_demo_user_signal() -> None:
    bars = [_make_bar(idx=i, rsi14=40.0, supertrend=1) for i in range(10)]
    bars.append(_make_bar(idx=10, rsi14=52.0, supertrend=1))

    bars_repo = FakeBarsRepo(bars)
    trades_repo = FakeTradesRepo()
    bot_state_repo = FakeBotStateRepo({
        "entry_cursor:BTCUSDT:30m:v2_core": 5000,
    })
    exchange = FakeExchange(last_closed_time=10 * 1000 + 999)
    gate = FakeGate(allowed=True)
    broker = FakeBroker()
    events_repo = FakeTradeEventsRepo()

    uc = OnlineEntryCycleUseCase(
        bars_repo=bars_repo,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        exchange=exchange,
        strategy=FakeStrategy(),
        gate=gate,
        broker=broker,
        trade_events_repo=events_repo,
        features_ver="v2_core",
    )

    uc.run("BTCUSDT", "30m", now_ms=999999)

    assert len(trades_repo.created) == 1
    trade = trades_repo.created[0]

    assert trade.user_id == "demo_user"
    assert trade.symbol == "BTCUSDT"
    assert trade.tf_entry == "30m"
    assert trade.model_id == "RSI_REBOUND_ST_124"
    assert trade.status == TradeStatus.OPEN
    assert trade.side == Side.LONG

    assert len(events_repo.events) == 1
    event = events_repo.events[0]
    assert event["event_type"] == "OPENED"
    assert event["payload"]["rule_id"] == "RSI_REBOUND_ST_124"


def test_online_entry_cycle_policy_blocks_duplicate_demo_user_trade() -> None:
    bars = [_make_bar(idx=i, rsi14=40.0, supertrend=1) for i in range(10)]
    bars.append(_make_bar(idx=10, rsi14=52.0, supertrend=1))

    existing_trade = Trade(
        trade_id="existing",
        user_id="demo_user",
        symbol="BTCUSDT",
        side=Side.LONG,
        status=TradeStatus.OPEN,
        opened_at=1,
        entry_price=100.0,
        qty=1.0,
        leverage=10,
        stake_usd=10.0,
        tf_entry="30m",
        model_id="RSI_REBOUND_ST_124",
        entry_bar_close_time=1,
        sl_price=98.0,
        mode="sandbox",
        tp_hit_count=0,
        remaining_pct=1.0,
        exit_last_check_at=1,
        qty_remaining=1.0,
        realized_pnl_usd=0.0,
    )

    bars_repo = FakeBarsRepo(bars)
    trades_repo = FakeTradesRepo()
    trades_repo.open_trades.append(existing_trade)

    bot_state_repo = FakeBotStateRepo({
        "entry_cursor:BTCUSDT:30m:v2_core": 5000,
    })
    exchange = FakeExchange(last_closed_time=10 * 1000 + 999)
    gate = FakeGate(allowed=True)
    broker = FakeBroker()
    events_repo = FakeTradeEventsRepo()

    uc = OnlineEntryCycleUseCase(
        bars_repo=bars_repo,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        exchange=exchange,
        strategy=FakeStrategy(),
        gate=gate,
        broker=broker,
        trade_events_repo=events_repo,
        features_ver="v2_core",
    )

    uc.run("BTCUSDT", "30m", now_ms=999999)

    assert len(trades_repo.created) == 0
    assert len(events_repo.events) == 0