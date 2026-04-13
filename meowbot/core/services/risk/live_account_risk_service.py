from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


AccountInfoProvider = Callable[[], Awaitable[dict[str, Any] | None]]
SymbolMaxLeverageProvider = Callable[[str], Awaitable[int | None]]


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


class LiveAccountRiskService:
    def __init__(
        self,
        *,
        account_info_provider: AccountInfoProvider,
        symbol_max_leverage_provider: SymbolMaxLeverageProvider,
        margin_buffer_ratio: float = 0.98,
    ) -> None:
        self.account_info_provider = account_info_provider
        self.symbol_max_leverage_provider = symbol_max_leverage_provider
        self.margin_buffer_ratio = float(margin_buffer_ratio)

    async def check_new_entry(
        self,
        *,
        symbol: str,
        entry_price: float,
        default_stake_mode: str,
        default_stake_value: float,
        default_leverage: int,
        max_margin_per_trade_mode: str,
        max_margin_per_trade_value: float,
        margin_ratio_warn_pct: float,
        margin_ratio_block_pct: float,
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
            )

        if default_stake_mode == "percent":
            stake_margin_usdt = available_balance * (float(default_stake_value) / 100.0)
        else:
            stake_margin_usdt = float(default_stake_value)

        if stake_margin_usdt <= 0:
            return LiveEntryRiskResult(
                allowed=False,
                reason="invalid_stake_margin",
                available_balance_usdt=available_balance,
                current_margin_ratio_pct=current_margin_ratio_pct,
            )

        if max_margin_per_trade_mode == "percent":
            max_allowed_margin_usdt = available_balance * (float(max_margin_per_trade_value) / 100.0)
        else:
            max_allowed_margin_usdt = float(max_margin_per_trade_value)

        max_allowed_margin_usdt *= self.margin_buffer_ratio

        if stake_margin_usdt > max_allowed_margin_usdt:
            return LiveEntryRiskResult(
                allowed=False,
                reason="max_margin_per_trade_exceeded",
                available_balance_usdt=available_balance,
                current_margin_ratio_pct=current_margin_ratio_pct,
            )

        symbol_max_leverage = await self.symbol_max_leverage_provider(symbol)
        if symbol_max_leverage is None:
            return LiveEntryRiskResult(
                allowed=False,
                reason="symbol_max_leverage_unavailable",
                available_balance_usdt=available_balance,
                current_margin_ratio_pct=current_margin_ratio_pct,
            )

        preferred_leverage = int(default_leverage)
        if preferred_leverage <= 0:
            preferred_leverage = 1

        if preferred_leverage > int(symbol_max_leverage):
            return LiveEntryRiskResult(
                allowed=False,
                reason="leverage_above_symbol_limit",
                available_balance_usdt=available_balance,
                current_margin_ratio_pct=current_margin_ratio_pct,
                symbol_max_leverage=int(symbol_max_leverage),
            )

        qty = (stake_margin_usdt * preferred_leverage) / entry_price if entry_price > 0 else 0.0

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
            stake_margin_usdt=float(stake_margin_usdt),
            leverage=int(preferred_leverage),
            qty=float(qty),
            available_balance_usdt=float(available_balance),
            current_margin_ratio_pct=current_margin_ratio_pct,
            symbol_max_leverage=int(symbol_max_leverage),
        )

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None