from __future__ import annotations

from meowbot.core.configs.sandbox_trading import SandboxTradingConfig
from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Trade
from meowbot.core.services.entry.sandbox_position_sizing_service import (
    SandboxPositionSizingService,
)


def _make_closed_trade(*, user_id: str, pnl: float) -> Trade:
    return Trade(
        trade_id=f"{user_id}:{pnl}",
        user_id=user_id,
        symbol="BTCUSDT",
        side=Side.LONG,
        status=TradeStatus.CLOSED,
        opened_at=1,
        entry_price=100.0,
        qty=1.0,
        leverage=10,
        stake_usd=10.0,
        tf_entry="1h",
        model_id="RULE",
        entry_bar_close_time=1,
        sl_price=98.0,
        mode="sandbox",
        tp_hit_count=0,
        remaining_pct=0.0,
        exit_last_check_at=1,
        qty_remaining=0.0,
        realized_pnl_usd=pnl,
    )


def test_percent_mode_uses_one_percent_of_balance() -> None:
    service = SandboxPositionSizingService(
        SandboxTradingConfig(
            starting_balance_usd=1000.0,
            leverage=20.0,
            entry_mode="percent",
            entry_percent=0.01,
            entry_fixed_usd=10.0,
            min_stake_usd=5.0,
        )
    )

    result = service.calculate(
        user_id="demo_user",
        entry_price=100.0,
        trades=[],
    )

    assert result.current_balance_usd == 1000.0
    assert result.stake_usd == 10.0
    assert result.qty == 2.0


def test_percent_mode_updates_balance_after_closed_trades() -> None:
    service = SandboxPositionSizingService(
        SandboxTradingConfig(
            starting_balance_usd=1000.0,
            leverage=20.0,
            entry_mode="percent",
            entry_percent=0.01,
            min_stake_usd=5.0,
        )
    )

    trades = [
        _make_closed_trade(user_id="demo_user", pnl=50.0),
        _make_closed_trade(user_id="demo_user", pnl=-20.0),
    ]

    result = service.calculate(
        user_id="demo_user",
        entry_price=103.0,
        trades=trades,
    )

    assert result.current_balance_usd == 1030.0
    assert result.stake_usd == 10.3


def test_fixed_mode_uses_fixed_stake() -> None:
    service = SandboxPositionSizingService(
        SandboxTradingConfig(
            starting_balance_usd=1000.0,
            leverage=20.0,
            entry_mode="fixed",
            entry_fixed_usd=25.0,
        )
    )

    result = service.calculate(
        user_id="demo_user",
        entry_price=100.0,
        trades=[],
    )

    assert result.current_balance_usd == 1000.0
    assert result.stake_usd == 25.0
    assert result.qty == 5.0