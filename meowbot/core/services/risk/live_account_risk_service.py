from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from typing import Any, Awaitable, Callable


AccountInfoProvider = Callable[[], Awaitable[dict[str, Any] | None]]
SymbolMaxLeverageProvider = Callable[[str], Awaitable[int | None]]
SymbolTradingRulesProvider = Callable[[str], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True)
class LiveEntryRiskResult:
    allowed: bool
    reason: str

    warn_user: bool = False
    warning_code: str | None = None

    stake_margin_usdt: float = 0.0
    leverage: int = 1
    qty: float = 0.0

    available_balance_usdt: float | None = None
    current_margin_ratio_pct: float | None = None
    symbol_max_leverage: int | None = None
    requested_leverage: int | None = None
    final_leverage: int | None = None
    leverage_adjusted: bool = False

    required_margin_usdt: float = 0.0
    notional_usdt: float = 0.0
    base_stake_margin_usdt: float = 0.0
    position_size_multiplier: float = 1.0
    planned_qty: float = 0.0
    planned_notional_usdt: float = 0.0
    adjusted_qty: float = 0.0
    adjusted_notional_usdt: float = 0.0
    min_notional_usdt: float = 0.0
    qty_step: float = 0.0
    min_qty: float = 0.0
    qty_bump_applied: bool = False
    account_margin_used_usdt: float | None = None
    account_margin_limit_usdt: float | None = None
    account_margin_usage_pct: float | None = None
    account_margin_current_used_usdt: float | None = None
    account_margin_after_entry_usdt: float | None = None
    account_margin_per_trade_limit_usdt: float | None = None


class LiveAccountRiskService:
    def __init__(
        self,
        *,
        account_info_provider: AccountInfoProvider,
        symbol_max_leverage_provider: SymbolMaxLeverageProvider,
        symbol_trading_rules_provider: SymbolTradingRulesProvider | None = None,
        margin_buffer_ratio: float = 0.98,
    ) -> None:
        self.account_info_provider = account_info_provider
        self.symbol_max_leverage_provider = symbol_max_leverage_provider
        self.symbol_trading_rules_provider = symbol_trading_rules_provider
        self.margin_buffer_ratio = float(margin_buffer_ratio)

    async def check_new_entry(
        self,
        *,
        symbol: str,
        entry_price: float,
        default_stake_mode: str,
        default_stake_value: float,
        min_entry_margin_usdt: float,
        default_leverage: int,
        max_margin_per_trade_mode: str,
        max_margin_per_trade_value: float,
        margin_ratio_warn_pct: float,
        margin_ratio_block_pct: float,
        position_size_multiplier: float = 1.0,
    ) -> LiveEntryRiskResult:
        account_info = await self.account_info_provider()
        if not account_info:
            return LiveEntryRiskResult(
                allowed=False,
                reason="account_info_unavailable",
            )

        available_balance = self._to_float(
            account_info.get("availableBalance")
            or account_info.get("available_balance")
            or account_info.get("available_balance_usdt")
        )
        total_margin_balance = self._to_float(
            account_info.get("totalMarginBalance")
            or account_info.get("total_margin_balance")
        )
        total_maint_margin = self._to_float(
            account_info.get("totalMaintMargin")
            or account_info.get("total_maint_margin")
        )
        account_margin_current_used = self._to_float(
            account_info.get("totalInitialMargin")
            or account_info.get("total_initial_margin")
            or account_info.get("totalPositionInitialMargin")
            or account_info.get("total_position_initial_margin")
        )

        if available_balance is None or available_balance <= 0:
            return LiveEntryRiskResult(
                allowed=False,
                reason="available_balance_unavailable",
            )

        current_margin_ratio_pct: float | None = None
        if total_margin_balance is not None and total_margin_balance > 0 and total_maint_margin is not None:
            current_margin_ratio_pct = (total_maint_margin / total_margin_balance) * 100.0

        if current_margin_ratio_pct is not None and current_margin_ratio_pct >= float(margin_ratio_block_pct):
            return LiveEntryRiskResult(
                allowed=False,
                reason="margin_ratio_blocked",
                available_balance_usdt=available_balance,
                current_margin_ratio_pct=current_margin_ratio_pct,
                account_margin_used_usdt=account_margin_current_used,
                account_margin_current_used_usdt=account_margin_current_used,
                account_margin_limit_usdt=total_margin_balance,
            )

        if default_stake_mode == "percent":
            stake_margin_usdt = available_balance * (float(default_stake_value) / 100.0)
        else:
            stake_margin_usdt = float(default_stake_value)

        minimum_margin = max(0.0, float(min_entry_margin_usdt))
        if minimum_margin > 0:
            stake_margin_usdt = max(stake_margin_usdt, minimum_margin)

        base_stake_margin_usdt = float(stake_margin_usdt)
        try:
            multiplier = float(position_size_multiplier or 1.0)
        except (TypeError, ValueError):
            multiplier = 1.0
        if multiplier <= 0:
            multiplier = 1.0
        stake_margin_usdt = base_stake_margin_usdt * multiplier

        if stake_margin_usdt <= 0:
            return LiveEntryRiskResult(
                allowed=False,
                reason="invalid_stake_margin",
                available_balance_usdt=available_balance,
                current_margin_ratio_pct=current_margin_ratio_pct,
                base_stake_margin_usdt=base_stake_margin_usdt,
                position_size_multiplier=multiplier,
            )

        if max_margin_per_trade_mode == "percent":
            max_allowed_margin_usdt = available_balance * (float(max_margin_per_trade_value) / 100.0)
        else:
            max_allowed_margin_usdt = float(max_margin_per_trade_value)

        max_allowed_margin_usdt *= self.margin_buffer_ratio

        symbol_max_leverage = await self.symbol_max_leverage_provider(symbol)
        if symbol_max_leverage is None:
            return LiveEntryRiskResult(
                allowed=False,
                reason="symbol_max_leverage_unavailable",
                available_balance_usdt=available_balance,
                current_margin_ratio_pct=current_margin_ratio_pct,
                account_margin_used_usdt=account_margin_current_used,
                account_margin_current_used_usdt=account_margin_current_used,
                account_margin_limit_usdt=total_margin_balance,
            )

        requested_leverage = int(default_leverage)
        if requested_leverage <= 0:
            requested_leverage = 1
        exchange_max_leverage = max(int(symbol_max_leverage), 1)
        final_leverage = min(requested_leverage, exchange_max_leverage)
        leverage_adjusted = final_leverage != requested_leverage

        rules = await self._get_symbol_trading_rules(symbol)
        qty_step = max(self._to_float(rules.get("qty_step"), default=0.0) or 0.0, 0.0)
        min_qty = max(self._to_float(rules.get("min_qty"), default=0.0) or 0.0, 0.0)
        min_notional = max(self._to_float(rules.get("min_notional"), default=0.0) or 0.0, 0.0)

        planned_qty_raw = (stake_margin_usdt * final_leverage) / entry_price if entry_price > 0 else 0.0
        planned_qty = self._round_qty_down(planned_qty_raw, qty_step)
        if min_qty > 0 and planned_qty < min_qty:
            planned_qty = self._ceil_to_step(min_qty, qty_step)

        planned_notional = planned_qty * entry_price if entry_price > 0 else 0.0
        adjusted_qty = self.adjust_qty_to_min_notional(
            planned_qty=planned_qty,
            entry_price=entry_price,
            min_notional=min_notional,
            step_size=qty_step,
        )
        adjusted_notional = adjusted_qty * entry_price if entry_price > 0 else 0.0
        qty_bump_applied = adjusted_qty > planned_qty
        required_margin_usdt = adjusted_notional / final_leverage if final_leverage > 0 else adjusted_notional

        account_margin_after_entry = (
            account_margin_current_used + required_margin_usdt
            if account_margin_current_used is not None
            else None
        )
        account_margin_usage_pct = (
            (account_margin_after_entry / total_margin_balance) * 100.0
            if account_margin_after_entry is not None and total_margin_balance is not None and total_margin_balance > 0
            else None
        )

        diagnostics = {
            "available_balance_usdt": float(available_balance),
            "current_margin_ratio_pct": current_margin_ratio_pct,
            "symbol_max_leverage": int(exchange_max_leverage),
            "requested_leverage": int(requested_leverage),
            "final_leverage": int(final_leverage),
            "leverage_adjusted": bool(leverage_adjusted),
            "required_margin_usdt": float(required_margin_usdt),
            "notional_usdt": float(adjusted_notional),
            "base_stake_margin_usdt": float(base_stake_margin_usdt),
            "position_size_multiplier": float(multiplier),
            "planned_qty": float(planned_qty),
            "planned_notional_usdt": float(planned_notional),
            "adjusted_qty": float(adjusted_qty),
            "adjusted_notional_usdt": float(adjusted_notional),
            "min_notional_usdt": float(min_notional),
            "qty_step": float(qty_step),
            "min_qty": float(min_qty),
            "qty_bump_applied": bool(qty_bump_applied),
            "account_margin_used_usdt": account_margin_after_entry,
            "account_margin_limit_usdt": total_margin_balance,
            "account_margin_usage_pct": account_margin_usage_pct,
            "account_margin_current_used_usdt": account_margin_current_used,
            "account_margin_after_entry_usdt": account_margin_after_entry,
            "account_margin_per_trade_limit_usdt": float(max_allowed_margin_usdt),
        }

        if required_margin_usdt <= 0 or adjusted_qty <= 0:
            return LiveEntryRiskResult(
                allowed=False,
                reason="invalid_entry_quantity",
                **diagnostics,
            )

        if required_margin_usdt > max_allowed_margin_usdt:
            return LiveEntryRiskResult(
                allowed=False,
                reason="entry_bump_blocked_by_global_margin_limit" if qty_bump_applied else "max_margin_per_trade_exceeded",
                **diagnostics,
            )

        if required_margin_usdt > available_balance:
            return LiveEntryRiskResult(
                allowed=False,
                reason="entry_bump_blocked_by_available_balance" if qty_bump_applied else "insufficient_available_balance",
                **diagnostics,
            )

        warn_user = (
            current_margin_ratio_pct is not None
            and current_margin_ratio_pct >= float(margin_ratio_warn_pct)
            and current_margin_ratio_pct < float(margin_ratio_block_pct)
        )

        return LiveEntryRiskResult(
            allowed=True,
            reason="ok",
            warn_user=warn_user,
            warning_code="margin_ratio_warning" if warn_user else None,
            stake_margin_usdt=float(required_margin_usdt),
            leverage=int(final_leverage),
            qty=float(adjusted_qty),
            **diagnostics,
        )

    async def _get_symbol_trading_rules(self, symbol: str) -> dict[str, Any]:
        if self.symbol_trading_rules_provider is None:
            return {}
        try:
            rules = await self.symbol_trading_rules_provider(symbol)
        except Exception:
            return {}
        return rules if isinstance(rules, dict) else {}

    @classmethod
    def adjust_qty_to_min_notional(
        cls,
        *,
        planned_qty: float,
        entry_price: float,
        min_notional: float,
        step_size: float,
    ) -> float:
        if planned_qty <= 0 or entry_price <= 0 or min_notional <= 0:
            return float(planned_qty)
        planned_notional = planned_qty * entry_price
        if planned_notional >= min_notional:
            return float(planned_qty)
        return cls._ceil_to_step(min_notional / entry_price, step_size)

    @classmethod
    def _round_qty_down(cls, value: float, step: float) -> float:
        return cls._round_number(value, step, ROUND_DOWN)

    @classmethod
    def _ceil_to_step(cls, value: float, step: float) -> float:
        return cls._round_number(value, step, ROUND_CEILING)

    @staticmethod
    def _round_number(value: float, step: float, rounding_mode: str) -> float:
        if step <= 0:
            return float(value)
        value_dec = Decimal(str(value))
        step_dec = Decimal(str(step))
        units = (value_dec / step_dec).to_integral_value(rounding=rounding_mode)
        return float(units * step_dec)

    @staticmethod
    def _to_float(value: Any, *, default: float | None = None) -> float | None:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default
