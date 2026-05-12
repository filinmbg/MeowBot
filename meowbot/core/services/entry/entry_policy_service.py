from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from meowbot.core.domain.enums import TradeStatus
from meowbot.core.domain.types import Trade


@dataclass(frozen=True)
class EntryPolicyDecision:
    allowed: bool
    reason: str
    conflict_trade_id: str | None = None


class EntryPolicyService:
    """
    Правила відкриття:

    1. Звичайний юзер:
       лише 1 відкритий трейд на symbol одночасно.

    2. Тестовий юзер:
       лише 1 відкритий трейд на symbol + timeframe + rule_id.
    """

    def __init__(self, *, test_user_ids: set[str] | None = None) -> None:
        self.test_user_ids = test_user_ids or {"demo_user"}

    def is_test_user(self, user_id: str) -> bool:
        return user_id in self.test_user_ids

    def can_open_trade(
        self,
        *,
        user_id: str,
        symbol: str,
        tf: str,
        rule_id: str,
        open_trades: Iterable[Trade],
    ) -> EntryPolicyDecision:
        normalized_symbol = symbol.upper()

        for trade in open_trades:
            if not self._is_open(trade):
                continue

            if trade.user_id != user_id:
                continue

            if trade.symbol.upper() != normalized_symbol:
                continue

            return EntryPolicyDecision(
                allowed=False,
                reason="open_trade_exists_for_symbol",
                conflict_trade_id=trade.trade_id,
            )

        return EntryPolicyDecision(
            allowed=True,
            reason="allowed",
            conflict_trade_id=None,
        )

    def _is_open(self, trade: Trade) -> bool:
        status = trade.status.value if hasattr(trade.status, "value") else str(trade.status)
        return status == TradeStatus.OPEN.value if hasattr(TradeStatus.OPEN, "value") else status == "OPEN"

