from __future__ import annotations

from meowbot.infra.postgres.repos.subscription_runtime_repo import SubscriptionRuntimeRepo


def test_runtime_repo_allows_paid_sandbox_when_plan_enables_sandbox() -> None:
    repo = SubscriptionRuntimeRepo(pool=None)  # type: ignore[arg-type]

    row = repo._normalize_row(  # noqa: SLF001 - small unit guard for runtime filter
        {
            "trading_mode": "sandbox",
            "features_json": '{"sandbox_enabled": true, "live_enabled": true}',
            "enabled_symbols": ["btcusdt"],
            "enabled_timeframes": ["1h"],
        }
    )

    assert row["enabled_symbols"] == ["BTCUSDT"]
    assert repo._is_runtime_mode_allowed(row) is True  # noqa: SLF001


def test_runtime_repo_blocks_live_when_plan_has_no_live_access() -> None:
    repo = SubscriptionRuntimeRepo(pool=None)  # type: ignore[arg-type]

    row = repo._normalize_row(  # noqa: SLF001
        {
            "trading_mode": "live",
            "features_json": {"sandbox_enabled": True, "live_enabled": False},
            "enabled_symbols": [],
            "enabled_timeframes": [],
        }
    )

    assert repo._is_runtime_mode_allowed(row) is False  # noqa: SLF001


def test_runtime_repo_detects_v2_from_plan_even_if_subscription_strategy_is_v1() -> None:
    repo = SubscriptionRuntimeRepo(pool=None)  # type: ignore[arg-type]

    row = repo._normalize_row(  # noqa: SLF001
        {
            "telegram_id": 900012002,
            "email": "basic_test_v2@example.com",
            "username": "basic_test_v2",
            "plan_code": "basic_v2",
            "strategy_version": "v1",
            "features_json": {"strategy_version": "v2", "live_enabled": True, "sandbox_enabled": True},
            "trading_mode": "sandbox",
            "enabled_symbols": [],
            "enabled_timeframes": [],
        }
    )

    assert row["strategy_version"] == "v2"
    assert row["subscription_type"] == "basic_v2"
