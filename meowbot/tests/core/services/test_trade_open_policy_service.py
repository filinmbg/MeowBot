from meowbot.core.services.trade_open_policy_service import (
    TradeOpenPolicyService,
    ActiveTradeRef,
)


def test_test_user_allows_multiple_rules():
    policy = TradeOpenPolicyService()

    decision = policy.can_open_trade(
        user_id="1",
        symbol="BTCUSDT",
        timeframe="1h",
        rule_id="rule_1",
        is_test_user=True,
        active_trades=[],
    )

    assert decision.allowed is True


def test_regular_user_blocks_second_trade():
    policy = TradeOpenPolicyService()

    active = [
        ActiveTradeRef(
            trade_id="1",
            user_id="1",
            symbol="BTCUSDT",
            timeframe="1h",
            rule_id="rule_1",
        )
    ]

    decision = policy.can_open_trade(
        user_id="1",
        symbol="BTCUSDT",
        timeframe="4h",
        rule_id="rule_2",
        is_test_user=False,
        active_trades=active,
    )

    assert decision.allowed is False