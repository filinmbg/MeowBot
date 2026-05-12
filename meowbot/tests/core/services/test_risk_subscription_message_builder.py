from __future__ import annotations

from meowbot.core.services.notifications.risk_subscription_message_builder import (
    RiskSubscriptionMessageBuilder,
)


def test_user_message_suppressed_for_existing_symbol_block() -> None:
    builder = RiskSubscriptionMessageBuilder()

    text = builder.build_user_message(
        "ENTRY_BLOCKED_SUBSCRIPTION",
        {
            "reason": "open_trade_exists_for_symbol",
            "symbol": "BCHUSDT",
            "tf_entry": "4h",
        },
    )

    assert text is None


def test_user_message_suppressed_for_global_risk_limit_block() -> None:
    builder = RiskSubscriptionMessageBuilder()

    text = builder.build_user_message(
        "ENTRY_BLOCKED_RISK",
        {
            "reason": "risk_limit_reached",
            "symbol": "DOTUSDT",
            "tf_entry": "4h",
        },
    )

    assert text is None


def test_user_message_still_sent_for_margin_block() -> None:
    builder = RiskSubscriptionMessageBuilder()

    text = builder.build_user_message(
        "ENTRY_BLOCKED_RISK",
        {
            "reason": "margin_ratio_blocked",
            "symbol": "BTCUSDT",
            "tf_entry": "1h",
            "current_margin_ratio_pct": 12.5,
        },
    )

    assert text is not None
