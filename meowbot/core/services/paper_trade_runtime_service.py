from __future__ import annotations

from dataclasses import dataclass
from typing import List

from meowbot.core.services.signal_detector_service import SignalCandidate
from meowbot.core.services.trade_open_policy_service import (
    TradeOpenPolicyService,
    ActiveTradeRef,
)


@dataclass
class PaperTrade:
    trade_id: str
    user_id: str
    symbol: str
    timeframe: str
    rule_id: str
    side: str

    entry_price: float
    current_price: float

    status: str = "open"
    pnl: float = 0.0


class PaperTradeRuntimeService:
    def __init__(self):
        self.policy = TradeOpenPolicyService()
        self._trades: dict[str, PaperTrade] = {}

    def get_active_trades(self, user_id: str) -> List[ActiveTradeRef]:
        return [
            ActiveTradeRef(
                trade_id=t.trade_id,
                user_id=t.user_id,
                symbol=t.symbol,
                timeframe=t.timeframe,
                rule_id=t.rule_id,
                status=t.status,
            )
            for t in self._trades.values()
            if t.user_id == user_id and t.status == "open"
        ]

    def process_signals(
        self,
        *,
        user_id: str,
        signals: list[SignalCandidate],
        is_test_user: bool,
    ) -> list[PaperTrade]:

        created: list[PaperTrade] = []

        for signal in signals:
            decision = self.policy.can_open_trade(
                user_id=user_id,
                symbol=signal.symbol,
                timeframe=signal.timeframe,
                rule_id=signal.rule_id,
                is_test_user=is_test_user,
                active_trades=self.get_active_trades(user_id),
            )

            if not decision.allowed:
                continue

            trade_id = signal.signal_key

            trade = PaperTrade(
                trade_id=trade_id,
                user_id=user_id,
                symbol=signal.symbol,
                timeframe=signal.timeframe,
                rule_id=signal.rule_id,
                side=signal.side,
                entry_price=signal.entry_price,
                current_price=signal.entry_price,
            )

            self._trades[trade_id] = trade
            created.append(trade)

        return created

    def update_price(self, symbol: str, price: float):
        for trade in self._trades.values():
            if trade.symbol != symbol or trade.status != "open":
                continue

            trade.current_price = price

            if trade.side == "LONG":
                trade.pnl = (price - trade.entry_price) / trade.entry_price
            else:
                trade.pnl = (trade.entry_price - price) / trade.entry_price

            # простий TP/SL для старту
            if trade.pnl >= 0.02:
                trade.status = "closed"
            elif trade.pnl <= -0.02:
                trade.status = "closed"