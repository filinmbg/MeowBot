from __future__ import annotations

import pytest

from meowbot.core.services.risk.live_account_risk_service import LiveAccountRiskService


def _account_info() -> dict:
    return {
        "availableBalance": "100",
        "totalMarginBalance": "100",
        "totalMaintMargin": "1",
        "totalInitialMargin": "10",
    }


async def _max_leverage(symbol: str) -> int:
    return 20


async def _max_leverage_10(symbol: str) -> int:
    return 10


async def _trading_rules(symbol: str) -> dict:
    return {
        "qty_step": 0.1,
        "min_qty": 0.1,
        "min_notional": 5.0,
    }


@pytest.mark.anyio
async def test_adjusts_qty_to_min_notional_before_entry() -> None:
    async def account_info() -> dict:
        return _account_info()

    service = LiveAccountRiskService(
        account_info_provider=account_info,
        symbol_max_leverage_provider=_max_leverage,
        symbol_trading_rules_provider=_trading_rules,
        margin_buffer_ratio=1.0,
    )

    result = await service.check_new_entry(
        symbol="INJUSDT",
        entry_price=3.357,
        default_stake_mode="fixed",
        default_stake_value=0.75,
        min_entry_margin_usdt=0,
        default_leverage=5,
        max_margin_per_trade_mode="fixed",
        max_margin_per_trade_value=2.0,
        margin_ratio_warn_pct=6.0,
        margin_ratio_block_pct=10.0,
    )

    assert result.allowed is True
    assert result.reason == "ok"
    assert result.planned_qty == 1.1
    assert result.qty == 1.5
    assert result.qty_bump_applied is True
    assert result.notional_usdt == pytest.approx(5.0355)
    assert result.stake_margin_usdt == pytest.approx(1.0071)
    assert result.account_margin_current_used_usdt == 10.0
    assert result.account_margin_after_entry_usdt == pytest.approx(11.0071)
    assert result.account_margin_usage_pct == pytest.approx(11.0071)


@pytest.mark.anyio
async def test_blocks_min_notional_bump_when_margin_limit_would_be_exceeded() -> None:
    async def account_info() -> dict:
        return _account_info()

    service = LiveAccountRiskService(
        account_info_provider=account_info,
        symbol_max_leverage_provider=_max_leverage,
        symbol_trading_rules_provider=_trading_rules,
        margin_buffer_ratio=1.0,
    )

    result = await service.check_new_entry(
        symbol="INJUSDT",
        entry_price=3.357,
        default_stake_mode="fixed",
        default_stake_value=0.75,
        min_entry_margin_usdt=0,
        default_leverage=5,
        max_margin_per_trade_mode="fixed",
        max_margin_per_trade_value=1.0,
        margin_ratio_warn_pct=6.0,
        margin_ratio_block_pct=10.0,
    )

    assert result.allowed is False
    assert result.reason == "entry_bump_blocked_by_global_margin_limit"
    assert result.qty_bump_applied is True
    assert result.adjusted_qty == 1.5
    assert result.required_margin_usdt == pytest.approx(1.0071)


@pytest.mark.anyio
async def test_keeps_qty_when_min_notional_is_already_met() -> None:
    async def account_info() -> dict:
        return _account_info()

    service = LiveAccountRiskService(
        account_info_provider=account_info,
        symbol_max_leverage_provider=_max_leverage,
        symbol_trading_rules_provider=_trading_rules,
        margin_buffer_ratio=1.0,
    )

    result = await service.check_new_entry(
        symbol="INJUSDT",
        entry_price=3.357,
        default_stake_mode="fixed",
        default_stake_value=2.0,
        min_entry_margin_usdt=0,
        default_leverage=5,
        max_margin_per_trade_mode="fixed",
        max_margin_per_trade_value=5.0,
        margin_ratio_warn_pct=6.0,
        margin_ratio_block_pct=10.0,
    )

    assert result.allowed is True
    assert result.qty_bump_applied is False
    assert result.planned_qty == result.qty
    assert result.notional_usdt >= 5.0


@pytest.mark.anyio
async def test_adjusts_leverage_to_exchange_max_instead_of_rejecting() -> None:
    async def account_info() -> dict:
        return _account_info()

    service = LiveAccountRiskService(
        account_info_provider=account_info,
        symbol_max_leverage_provider=_max_leverage_10,
        symbol_trading_rules_provider=_trading_rules,
        margin_buffer_ratio=1.0,
    )

    result = await service.check_new_entry(
        symbol="BTCUSDT",
        entry_price=100.0,
        default_stake_mode="fixed",
        default_stake_value=1.0,
        min_entry_margin_usdt=0,
        default_leverage=20,
        max_margin_per_trade_mode="fixed",
        max_margin_per_trade_value=5.0,
        margin_ratio_warn_pct=6.0,
        margin_ratio_block_pct=10.0,
    )

    assert result.allowed is True
    assert result.reason == "ok"
    assert result.requested_leverage == 20
    assert result.symbol_max_leverage == 10
    assert result.final_leverage == 10
    assert result.leverage == 10
    assert result.leverage_adjusted is True
    assert result.qty == 0.1
    assert result.notional_usdt == pytest.approx(10.0)
    assert result.stake_margin_usdt == pytest.approx(1.0)


@pytest.mark.anyio
async def test_applies_position_size_multiplier_before_final_risk_checks() -> None:
    async def account_info() -> dict:
        return _account_info()

    service = LiveAccountRiskService(
        account_info_provider=account_info,
        symbol_max_leverage_provider=_max_leverage,
        symbol_trading_rules_provider=_trading_rules,
        margin_buffer_ratio=1.0,
    )

    result = await service.check_new_entry(
        symbol="BTCUSDT",
        entry_price=10.0,
        default_stake_mode="fixed",
        default_stake_value=2.0,
        min_entry_margin_usdt=0,
        default_leverage=5,
        max_margin_per_trade_mode="fixed",
        max_margin_per_trade_value=5.0,
        margin_ratio_warn_pct=6.0,
        margin_ratio_block_pct=10.0,
        position_size_multiplier=1.5,
    )

    assert result.allowed is True
    assert result.base_stake_margin_usdt == pytest.approx(2.0)
    assert result.position_size_multiplier == pytest.approx(1.5)
    assert result.stake_margin_usdt == pytest.approx(3.0)
    assert result.notional_usdt == pytest.approx(15.0)
