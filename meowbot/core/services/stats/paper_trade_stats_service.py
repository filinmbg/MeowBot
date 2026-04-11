from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from meowbot.core.configs.sandbox_trading import SandboxTradingConfig
from meowbot.core.domain.enums import TradeStatus
from meowbot.core.domain.types import Trade


@dataclass(frozen=True)
class PaperTradeStats:
    total_trades: int
    open_trades: int
    closed_trades: int
    wins: int
    losses: int
    winrate_pct: float

    start_balance_usd: float
    current_balance_usd: float

    net_profit_usd: float
    gross_profit_usd: float
    gross_loss_usd: float
    profit_factor: float

    avg_trade_usd: float
    avg_win_usd: float
    avg_loss_usd: float

    total_stake_usd: float


class PaperTradeStatsService:
    def __init__(self, config: SandboxTradingConfig):
        self.config = config

    def calculate_for_all(self, trades: Iterable[Trade]) -> PaperTradeStats:
        return self._calculate(list(trades))

    def calculate_for_user(self, trades: Iterable[Trade], user_id: str) -> PaperTradeStats:
        selected = [trade for trade in trades if trade.user_id == user_id]
        return self._calculate(selected)

    def calculate_for_test_users(
        self,
        trades: Iterable[Trade],
        *,
        test_user_ids: set[str],
    ) -> PaperTradeStats:
        selected = [trade for trade in trades if trade.user_id in test_user_ids]
        return self._calculate(selected)

    def _calculate(self, trades: list[Trade]) -> PaperTradeStats:
        total_trades = len(trades)

        open_trades = [
            t for t in trades
            if self._status(t) == TradeStatus.OPEN.value
        ]
        closed_trades = [
            t for t in trades
            if self._status(t) == TradeStatus.CLOSED.value
        ]

        wins = [t for t in closed_trades if float(t.realized_pnl_usd or 0.0) > 0]
        losses = [t for t in closed_trades if float(t.realized_pnl_usd or 0.0) <= 0]

        net_profit_usd = sum(float(t.realized_pnl_usd or 0.0) for t in closed_trades)
        gross_profit_usd = sum(float(t.realized_pnl_usd or 0.0) for t in wins)
        gross_loss_usd = sum(float(t.realized_pnl_usd or 0.0) for t in losses)

        closed_count = len(closed_trades)
        wins_count = len(wins)
        losses_count = len(losses)

        winrate_pct = (wins_count / closed_count * 100.0) if closed_count else 0.0
        profit_factor = (
            gross_profit_usd / abs(gross_loss_usd)
            if gross_loss_usd < 0
            else 0.0
        )

        avg_trade_usd = net_profit_usd / closed_count if closed_count else 0.0
        avg_win_usd = gross_profit_usd / wins_count if wins_count else 0.0
        avg_loss_usd = gross_loss_usd / losses_count if losses_count else 0.0

        total_stake_usd = sum(float(t.stake_usd or 0.0) for t in trades)

        current_balance_usd = self.config.starting_balance_usd + net_profit_usd

        return PaperTradeStats(
            total_trades=total_trades,
            open_trades=len(open_trades),
            closed_trades=closed_count,
            wins=wins_count,
            losses=losses_count,
            winrate_pct=round(winrate_pct, 4),
            start_balance_usd=round(float(self.config.starting_balance_usd), 8),
            current_balance_usd=round(float(current_balance_usd), 8),
            net_profit_usd=round(float(net_profit_usd), 8),
            gross_profit_usd=round(float(gross_profit_usd), 8),
            gross_loss_usd=round(float(gross_loss_usd), 8),
            profit_factor=round(float(profit_factor), 8),
            avg_trade_usd=round(float(avg_trade_usd), 8),
            avg_win_usd=round(float(avg_win_usd), 8),
            avg_loss_usd=round(float(avg_loss_usd), 8),
            total_stake_usd=round(float(total_stake_usd), 8),
        )

    def _status(self, trade: Trade) -> str:
        return trade.status.value if hasattr(trade.status, "value") else str(trade.status)