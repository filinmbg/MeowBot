from meowbot.core.domain.types import Bar, Trade
from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.services.execution.exit.tp_sl_cascade import apply_tp_sl_cascade
from meowbot.infra.broker.paper import PaperBroker


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


def test_tp1_hit_moves_sl():
    trade = make_trade()
    broker = PaperBroker()

    bars = [make_bar(0, low=100.0, high=100.6, close=100.5)]
    updated = apply_tp_sl_cascade(trade, bars, broker)

    assert updated.tp_hit_count == 1
    assert updated.qty_remaining == 0.75
    assert updated.sl_price == 100.0


def test_tp1_then_sl_closes_profitably():
    trade = make_trade()
    broker = PaperBroker()

    bars = [
        make_bar(0, low=100.0, high=100.6, close=100.5),
        make_bar(1, low=99.9, high=100.1, close=100.0),
    ]
    updated = apply_tp_sl_cascade(trade, bars, broker)

    assert updated.status == TradeStatus.CLOSED
    assert updated.qty_remaining == 0.0
    assert updated.realized_pnl_usd >= 0.0