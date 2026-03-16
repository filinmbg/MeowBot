from meowbot.core.domain.types import Trade
from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.infra.broker.paper import PaperBroker


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
        exit_last_check_at=0,
    )


def test_paper_broker_open_position():
    broker = PaperBroker()
    t = make_trade()
    opened = broker.open_position(t)

    assert opened.status == TradeStatus.OPEN
    assert opened.qty_remaining == 1.0


def test_paper_broker_reduce_position():
    broker = PaperBroker()
    t = make_trade()

    reduced = broker.reduce_position(t, qty_to_reduce=0.25, price=101.0, reason="TP1")
    assert reduced.qty_remaining == 0.75
    assert reduced.remaining_pct == 0.75
    assert reduced.realized_pnl_usd > 0


def test_paper_broker_close_position():
    broker = PaperBroker()
    t = make_trade()

    closed = broker.close_position(t, price=102.0, reason="TP4")
    assert closed.status == TradeStatus.CLOSED
    assert closed.qty_remaining == 0.0
    assert closed.remaining_pct == 0.0
    assert closed.close_price == 102.0