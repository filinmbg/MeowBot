from __future__ import annotations

from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Trade
from meowbot.core.services.entry.entry_policy_service import EntryPolicyService


def _make_open_trade(
    *,
    trade_id: str,
    user_id: str,
    symbol: str,
    tf: str,
    rule_id: str,
) -> Trade:
    return Trade(
        trade_id=trade_id,
        user_id=user_id,
        symbol=symbol,
        side=Side.LONG,
        status=TradeStatus.OPEN,
        opened_at=1,
        entry_price=100.0,
        qty=1.0,
        leverage=10,
        stake_usd=10.0,
        tf_entry=tf,
        model_id=rule_id,
        entry_bar_close_time=1,
        sl_price=98.0,
        mode="sandbox",
        tp_hit_count=0,
        remaining_pct=1.0,
        exit_last_check_at=1,
        qty_remaining=1.0,
        realized_pnl_usd=0.0,
    )


def test_regular_user_blocked_by_same_symbol() -> None:
    service = EntryPolicyService(test_user_ids={"demo_user"})

    open_trades = [
        _make_open_trade(
            trade_id="t1",
            user_id="user_1",
            symbol="BTCUSDT",
            tf="15m",
            rule_id="RULE_A",
        )
    ]

    decision = service.can_open_trade(
        user_id="user_1",
        symbol="BTCUSDT",
        tf="1h",
        rule_id="RULE_B",
        open_trades=open_trades,
    )

    assert decision.allowed is False
    assert decision.reason == "regular_user_has_open_trade_for_symbol"
    assert decision.conflict_trade_id == "t1"


def test_regular_user_allowed_on_other_symbol() -> None:
    service = EntryPolicyService(test_user_ids={"demo_user"})

    open_trades = [
        _make_open_trade(
            trade_id="t1",
            user_id="user_1",
            symbol="ETHUSDT",
            tf="15m",
            rule_id="RULE_A",
        )
    ]

    decision = service.can_open_trade(
        user_id="user_1",
        symbol="BTCUSDT",
        tf="1h",
        rule_id="RULE_B",
        open_trades=open_trades,
    )

    assert decision.allowed is True
    assert decision.reason == "allowed"
    assert decision.conflict_trade_id is None


def test_test_user_blocked_on_same_symbol_tf_rule() -> None:
    service = EntryPolicyService(test_user_ids={"demo_user"})

    open_trades = [
        _make_open_trade(
            trade_id="t1",
            user_id="demo_user",
            symbol="BTCUSDT",
            tf="30m",
            rule_id="RSI_REBOUND_ST_124",
        )
    ]

    decision = service.can_open_trade(
        user_id="demo_user",
        symbol="BTCUSDT",
        tf="30m",
        rule_id="RSI_REBOUND_ST_124",
        open_trades=open_trades,
    )

    assert decision.allowed is False
    assert decision.reason == "test_user_duplicate_symbol_tf_rule"
    assert decision.conflict_trade_id == "t1"


def test_test_user_allowed_on_same_symbol_other_tf() -> None:
    service = EntryPolicyService(test_user_ids={"demo_user"})

    open_trades = [
        _make_open_trade(
            trade_id="t1",
            user_id="demo_user",
            symbol="BTCUSDT",
            tf="30m",
            rule_id="RSI_REBOUND_ST_124",
        )
    ]

    decision = service.can_open_trade(
        user_id="demo_user",
        symbol="BTCUSDT",
        tf="1h",
        rule_id="RSI_REBOUND_ST_124",
        open_trades=open_trades,
    )

    assert decision.allowed is True
    assert decision.reason == "allowed"


def test_test_user_allowed_on_same_symbol_tf_other_rule() -> None:
    service = EntryPolicyService(test_user_ids={"demo_user"})

    open_trades = [
        _make_open_trade(
            trade_id="t1",
            user_id="demo_user",
            symbol="BTCUSDT",
            tf="30m",
            rule_id="RSI_REBOUND_ST_124",
        )
    ]

    decision = service.can_open_trade(
        user_id="demo_user",
        symbol="BTCUSDT",
        tf="30m",
        rule_id="RSI_REBOUND_ST_201",
        open_trades=open_trades,
    )

    assert decision.allowed is True
    assert decision.reason == "allowed"