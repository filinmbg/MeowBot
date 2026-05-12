from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from meowbot.core.configs.sandbox_trading import SandboxTradingConfig
from meowbot.core.domain.types import Trade


@dataclass(frozen=True)
class SandboxSizingResult:
    current_balance_usd: float
    stake_usd: float
    leverage: float
    qty: float


class SandboxPositionSizingService:
    def __init__(self, config: SandboxTradingConfig):
        self.config = config

    def calculate(
        self,
        *,
        user_id: str,
        entry_price: float,
        trades: Iterable[Trade],
    ) -> SandboxSizingResult:
        current_balance_usd = self._calculate_current_balance(
            user_id=user_id,
            trades=trades,
        )

        stake_usd = self.config.resolve_stake_usd(current_balance_usd)
        qty = (stake_usd * self.config.leverage) / entry_price

        return SandboxSizingResult(
            current_balance_usd=round(current_balance_usd, 8),
            stake_usd=round(stake_usd, 8),
            leverage=float(self.config.leverage),
            qty=float(qty),
        )

    def _calculate_current_balance(
        self,
        *,
        user_id: str,
        trades: Iterable[Trade],
    ) -> float:
        balance = float(self.config.starting_balance_usd)

        for trade in trades:
            if trade.user_id != user_id:
                continue

            # TP1/TP2 close part of the position, so their realized PnL belongs
            # to the sandbox balance even while the trade remains open.
            balance += float(trade.realized_pnl_usd or 0.0)

        return max(balance, 0.0)
