from __future__ import annotations

from meowbot.core.configs.sandbox_trading import SandboxTradingConfig
from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Trade
from meowbot.core.services.stats.paper_trade_stats_service import (
    PaperTradeStatsService,
)


def _make_trade(
    *,
    trade_id: str,
    user_id: str,
    status: TradeStatus,
    pnl: float,
    stake_usd: float = 10.0,
) -> Trade:
    return Trade(
        trade_id=trade_id,
        user_id=user_id,
        symbol="BTCUSDT",
        side=Side.LONG,
        status=status,
        opened_at=1,
        entry_price=100.0,
        qty=1.0,
        leverage=20,
        stake_usd=stake_usd,
        tf_entry="1h",
        model_id="RSI_REBOUND_ST_124",
        entry_bar_close_time=1,
        sl_price=98.0,
        mode="sandbox",
        tp_hit_count=0,
        remaining_pct=0.0 if status == TradeStatus.CLOSED else 1.0,
        exit_last_check_at=1,
        qty_remaining=0.0 if status == TradeStatus.CLOSED else 1.0,
        realized_pnl_usd=pnl,
    )


def test_stats_for_user() -> None:
    service = PaperTradeStatsService(
        SandboxTradingConfig(starting_balance_usd=1000.0)
    )

    trades = [
        _make_trade(trade_id="1", user_id="demo_user", status=TradeStatus.CLOSED, pnl=20.0),
        _make_trade(trade_id="2", user_id="demo_user", status=TradeStatus.CLOSED, pnl=-10.0),
        _make_trade(trade_id="3", user_id="demo_user", status=TradeStatus.OPEN, pnl=0.0),
        _make_trade(trade_id="4", user_id="other_user", status=TradeStatus.CLOSED, pnl=100.0),
    ]

    stats = service.calculate_for_user(trades, "demo_user")

    assert stats.total_trades == 3
    assert stats.open_trades == 1
    assert stats.closed_trades == 2
    assert stats.wins == 1
    assert stats.losses == 1
    assert stats.winrate_pct == 50.0
    assert stats.net_profit_usd == 10.0
    assert stats.current_balance_usd == 1010.0
    assert stats.gross_profit_usd == 20.0
    assert stats.gross_loss_usd == -10.0
    assert stats.profit_factor == 2.0


def test_stats_for_test_users() -> None:
    service = PaperTradeStatsService(
        SandboxTradingConfig(starting_balance_usd=1000.0)
    )

    trades = [
        _make_trade(trade_id="1", user_id="demo_user", status=TradeStatus.CLOSED, pnl=20.0),
        _make_trade(trade_id="2", user_id="demo_user_2", status=TradeStatus.CLOSED, pnl=30.0),
        _make_trade(trade_id="3", user_id="other_user", status=TradeStatus.CLOSED, pnl=40.0),
    ]

    stats = service.calculate_for_test_users(
        trades,
        test_user_ids={"demo_user", "demo_user_2"},
    )

    assert stats.total_trades == 2
    assert stats.closed_trades == 2
    assert stats.wins == 2
    assert stats.losses == 0
    assert stats.net_profit_usd == 50.0
    assert stats.current_balance_usd == 1050.0