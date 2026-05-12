from __future__ import annotations

from meowbot.core.services.notifications.admin_test_users_summary_message_builder import (
    AdminTestUsersSummaryMessageBuilder,
)


def test_admin_summary_shows_v1_and_v2_blocks_with_winner() -> None:
    builder = AdminTestUsersSummaryMessageBuilder()

    text = builder.build(
        [
            {
                "email": "basic_test@example.com",
                "display_name": "Basic V1",
                "plan_code": "basic",
                "strategy_version": "v1",
                "trading_mode": "sandbox",
                "open_trades": 1,
                "closed_trades": 2,
                "wins": 1,
                "losses": 1,
                "realized_pnl_usd": 3.0,
                "start_balance_usd": 1000.0,
                "current_balance_usd": 1003.0,
                "roi_pct": 0.3,
                "active_cooldowns": 0,
                "risk_blocks_period": 4,
            },
            {
                "email": "basic_test_v2@example.com",
                "display_name": "Basic V2",
                "plan_code": "basic_v2",
                "strategy_version": "v2",
                "trading_mode": "sandbox",
                "open_trades": 0,
                "closed_trades": 3,
                "wins": 2,
                "losses": 1,
                "realized_pnl_usd": 6.0,
                "start_balance_usd": 1000.0,
                "current_balance_usd": 1006.0,
                "roi_pct": 0.6,
                "active_cooldowns": 0,
                "risk_blocks_period": 1,
            },
        ],
        period_hours=3,
    )

    assert "📋 <b>Тестові юзери summary</b>" in text
    assert "🔹 <b>Strategy V1</b>" in text
    assert "basic_test@example.com" in text
    assert "🔸 <b>Strategy V2 / LONG_BREAKOUT_V18</b>" in text
    assert "basic_test_v2@example.com" in text
    assert "📊 <b>V1 vs V2 comparison</b>" in text
    assert "<b>V1:</b>" in text
    assert "<b>V2:</b>" in text
    assert "- by PnL: <code>V2</code>" in text
    assert "- by ROI: <code>V2</code>" in text
    assert "- by winrate: <code>V2</code>" in text


def test_admin_summary_keeps_empty_v2_user_visible_with_zeroes() -> None:
    builder = AdminTestUsersSummaryMessageBuilder()

    text = builder.build(
        [
            {
                "email": "free_test_v2@example.com",
                "username": "free_test_v2",
                "plan_code": "free_v2",
                "strategy_version": None,
                "open_trades": 0,
                "closed_trades": 0,
                "wins": 0,
                "losses": 0,
                "realized_pnl_usd": 0.0,
                "start_balance_usd": 1000.0,
                "risk_blocks_period": 0,
            }
        ],
        period_hours=3,
    )

    assert "free_test_v2@example.com" in text
    assert "PnL: <code>0.00 USD</code> | ROI: <code>0.00%</code>" in text
    assert "Open / Closed: <code>0</code> / <code>0</code>" in text
