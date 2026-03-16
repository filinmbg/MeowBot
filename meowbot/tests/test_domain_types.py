from meowbot.core.domain.types import Bar, Trade
from meowbot.core.domain.enums import Side, TradeStatus


def test_bar_is_frozen():
    b = Bar(
        symbol="BTCUSDT",
        tf="15m",
        open_time=0,
        close_time=60_000,
        o=1.0, h=2.0, l=0.5, c=1.5, v=10.0,
        features={"rsi": 50.0},
        features_ok=True,
        features_ver="v12",
    )
    assert b.features_ok is True
    assert b.features_ver == "v12"

    # dataclass(frozen=True) => змінювати поля не можна
    try:
        # type: ignore[attr-defined]
        b.c = 999.0
        assert False, "Bar must be frozen"
    except Exception:
        assert True


def test_trade_defaults():
    t = Trade(
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
        sl_price=95.0,
        exit_last_check_at=0,
    )
    assert t.remaining_pct == 1.0
    assert t.tp_hit_count == 0
    assert t.status == TradeStatus.OPEN
