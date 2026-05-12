from __future__ import annotations

from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Trade
from meowbot.core.services.trade_manager import TradeManager


def _make_trade(
    *,
    user_id: str,
    symbol: str,
    status: TradeStatus = TradeStatus.OPEN,
    tp_hit_count: int = 0,
) -> Trade:
    return Trade(
        trade_id=f"{user_id}:{symbol}:{tp_hit_count}",
        user_id=user_id,
        symbol=symbol,
        side=Side.LONG,
        status=status,
        opened_at=1,
        entry_price=100.0,
        qty=1.0,
        leverage=5,
        stake_usd=10.0,
        tf_entry="1h",
        model_id="rule",
        entry_bar_close_time=1,
        sl_price=98.0,
        mode="sandbox",
        tp_hit_count=tp_hit_count,
        remaining_pct=1.0,
        exit_last_check_at=1,
        qty_remaining=1.0,
        realized_pnl_usd=0.0,
    )


def test_trade_manager_blocks_symbol_outside_plan_whitelist() -> None:
    manager = TradeManager()

    result = manager.evaluate_new_trade(
        user_id="tg:1",
        symbol="DOGEUSDT",
        open_trades=[],
        plan_code="free",
        allowed_symbols=["BTCUSDT", "ETHUSDT"],
    )

    assert result.allowed is False
    assert result.reason == "symbol_not_allowed"


def test_trade_manager_allows_vip_all_symbols_feature() -> None:
    manager = TradeManager()

    result = manager.evaluate_new_trade(
        user_id="tg:1",
        symbol="DOGEUSDT",
        open_trades=[],
        plan_code="vip_v2",
        plan_features={"can_trade_all_symbols": True, "allowed_symbols": "all"},
        allowed_symbols=["BTCUSDT", "ETHUSDT"],
    )

    assert result.allowed is True
    assert result.reason == "ok"


def test_trade_manager_blocks_second_open_trade_for_same_symbol() -> None:
    manager = TradeManager()
    open_trades = [_make_trade(user_id="tg:1", symbol="BTCUSDT")]

    result = manager.evaluate_new_trade(
        user_id="tg:1",
        symbol="BTCUSDT",
        open_trades=open_trades,
        plan_code="basic",
        allowed_symbols=["BTCUSDT", "ETHUSDT"],
    )

    assert result.allowed is False
    assert result.reason == "open_trade_exists_for_symbol"


def test_trade_manager_blocks_when_plan_total_limit_reached() -> None:
    manager = TradeManager()
    open_trades = [
        _make_trade(user_id="tg:1", symbol="BTCUSDT"),
        _make_trade(user_id="tg:1", symbol="ETHUSDT"),
        _make_trade(user_id="tg:1", symbol="SOLUSDT"),
        _make_trade(user_id="tg:1", symbol="XRPUSDT"),
        _make_trade(user_id="tg:1", symbol="ADAUSDT"),
    ]

    result = manager.evaluate_new_trade(
        user_id="tg:1",
        symbol="LINKUSDT",
        open_trades=open_trades,
        plan_code="basic",
        allowed_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "LINKUSDT"],
    )

    assert result.allowed is False
    assert result.reason == "max_open_trades_total_exceeded"
    assert result.max_open_trades_total == 5


def test_trade_manager_counts_only_pre_tp1_trades_as_risky() -> None:
    manager = TradeManager()
    open_trades = [
        _make_trade(user_id="tg:1", symbol="BTCUSDT", tp_hit_count=0),
        _make_trade(user_id="tg:1", symbol="ETHUSDT", tp_hit_count=0),
        _make_trade(user_id="tg:1", symbol="SOLUSDT", tp_hit_count=0),
        _make_trade(user_id="tg:1", symbol="XRPUSDT", tp_hit_count=0),
        _make_trade(user_id="tg:1", symbol="ADAUSDT", tp_hit_count=0),
        _make_trade(user_id="tg:1", symbol="DOGEUSDT", tp_hit_count=1),
    ]

    result = manager.evaluate_new_trade(
        user_id="tg:1",
        symbol="LINKUSDT",
        open_trades=open_trades,
        plan_code="vip",
        allowed_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT"],
    )

    assert result.allowed is False
    assert result.reason == "risk_limit_reached"
    assert result.current_risk_trades == 5


def test_trade_manager_allows_new_trade_after_tp1_reduces_risk_count() -> None:
    manager = TradeManager()
    open_trades = [
        _make_trade(user_id="tg:1", symbol="BTCUSDT", tp_hit_count=1),
        _make_trade(user_id="tg:1", symbol="ETHUSDT", tp_hit_count=1),
        _make_trade(user_id="tg:1", symbol="SOLUSDT", tp_hit_count=1),
        _make_trade(user_id="tg:1", symbol="XRPUSDT", tp_hit_count=1),
        _make_trade(user_id="tg:1", symbol="ADAUSDT", tp_hit_count=0),
    ]

    result = manager.evaluate_new_trade(
        user_id="tg:1",
        symbol="LINKUSDT",
        open_trades=open_trades,
        plan_code="vip",
        allowed_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "LINKUSDT"],
    )

    assert result.allowed is True
    assert result.current_risk_trades == 1
