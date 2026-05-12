from __future__ import annotations

from meowbot.core.configs.strategy_version_test_users import (
    DEFAULT_V1_TEST_USER_EMAILS,
    DEFAULT_V2_TEST_USER_EMAILS,
    detect_strategy_version,
    expand_strategy_version_test_user_emails,
    normalize_strategy_plan_code,
)
from meowbot.core.configs.trading_plan_limits import normalize_plan_code


def test_expand_strategy_version_test_user_emails_adds_matching_v2_users() -> None:
    emails = expand_strategy_version_test_user_emails(
        [
            "free_test@example.com",
            "basic_test@example.com",
        ]
    )

    assert emails == [
        "free_test@example.com",
        "basic_test@example.com",
        "free_test_v2@example.com",
        "basic_test_v2@example.com",
    ]


def test_expand_strategy_version_test_user_emails_defaults_to_full_v1_v2_set() -> None:
    emails = expand_strategy_version_test_user_emails([])

    assert emails == [*DEFAULT_V1_TEST_USER_EMAILS, *DEFAULT_V2_TEST_USER_EMAILS]


def test_normalize_strategy_plan_code_strips_v2_suffix() -> None:
    assert normalize_strategy_plan_code("basic_v2") == "basic"
    assert normalize_strategy_plan_code("vip") == "vip"
    assert normalize_strategy_plan_code(None) == "unknown"


def test_trade_plan_limit_normalization_supports_v2_plan_codes() -> None:
    assert normalize_plan_code("basic_v2") == "basic"
    assert normalize_plan_code("pro") == "pro"
    assert normalize_plan_code("vip_v2") == "vip"


def test_detect_strategy_version_supports_v2_markers() -> None:
    assert detect_strategy_version(strategy_version="v2") == "v2"
    assert detect_strategy_version(strategy_version="v1", plan_code="basic_v2") == "v2"
    assert detect_strategy_version(strategy_version="v1", features_json={"strategy_version": "v2"}) == "v2"
    assert detect_strategy_version(
        strategy_version="v1",
        features_json={"enabled_strategy_rules": ["LONG_BREAKOUT_V18"]},
    ) == "v2"
    assert detect_strategy_version(plan_code="basic_v2") == "v2"
    assert detect_strategy_version(email="basic_test_v2@example.com") == "v2"
    assert detect_strategy_version(username="basic_test_v2") == "v2"
    assert detect_strategy_version(telegram_id=900012003) == "v2"
    assert detect_strategy_version(strategy_version="v1", plan_code="basic") == "v1"
