from meowbot.core.domain.types import Bar, Trade
from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.usecases.reconcile_trades import ReconcileOpenTradesUseCase, floor_to_1m
from meowbot.infra.memory.trades_repo import InMemoryTradesRepo
from meowbot.infra.memory.exchange import FakeExchange
from meowbot.infra.broker.paper import PaperBroker

def make_m1_bar(i: int, low: float, high: float, close: float) -> Bar:
    # 1m бар: open_time=i*60_000, close_time=(i+1)*60_000
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


def test_trade_closes_on_sl_long():
    # Бар 2 пробиває SL по low
    ex_bars = [
        make_m1_bar(0, low=100, high=100.4, close=100.2),
        make_m1_bar(1, low=99, high=100.3, close=100.0),
        make_m1_bar(2, low=95, high=100.2, close=98),
    ]
    exchange = FakeExchange(ex_bars)

    repo = InMemoryTradesRepo()
    repo.create_trade(
        Trade(
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
            model_id="model_x",
            entry_bar_close_time=0,
            sl_price=97.0,
            exit_last_check_at=0,

        )
    )

    broker = PaperBroker()
    uc = ReconcileOpenTradesUseCase(repo, exchange, broker)
    uc.run(now_ms=3 * 60_000 + 10_000)  # трошки після 3х хвилин

    t = repo.get_open_trade_by_symbol("BTCUSDT")
    assert t is None  # вже закрито

    # перевіримо закритий трейд
    closed = [x for x in repo._trades.values() if x.trade_id == "t1"][0]
    assert closed.status == TradeStatus.CLOSED
    assert closed.exit_reason == "SL_HIT"
    assert closed.close_price == 97.0


def test_trade_not_processed_twice():
    # SL не чіпаємо, просто курсор має рухатись вперед
    ex_bars = [
        make_m1_bar(0, low=100.0, high=100.4, close=100.2),
        make_m1_bar(1, low=100.0, high=100.4, close=100.2),
        make_m1_bar(2, low=100.0, high=100.4, close=100.2),
        make_m1_bar(3, low=100.0, high=100.4, close=100.2),
    ]
    exchange = FakeExchange(ex_bars)
    repo = InMemoryTradesRepo()

    trade = Trade(
        trade_id="t2",
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
        model_id="model_x",
        entry_bar_close_time=0,
        sl_price=50.0,  # далеко, не спрацює
        exit_last_check_at=0,

    )
    repo.create_trade(trade)

    broker = PaperBroker()
    uc = ReconcileOpenTradesUseCase(repo, exchange, broker)

    now1 = 4 * 60_000 + 5_000
    uc.run(now_ms=now1)

    updated = repo.get_open_trade_by_symbol("BTCUSDT")
    assert updated is not None
    assert updated.exit_last_check_at == floor_to_1m(now1)

    calls_after_first = exchange.fetch_calls

    # Другий запуск без зміни часу -> не має робити fetch повторно
    uc.run(now_ms=now1)
    assert exchange.fetch_calls == calls_after_first
