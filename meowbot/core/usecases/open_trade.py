from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Sequence

from meowbot.core.domain.types import Trade
from meowbot.core.services.trade_manager import TradeManager, TradeOpenCheckResult


@dataclass(frozen=True)
class OpenTradeResult:
    opened: bool
    trade: Trade | None
    check: TradeOpenCheckResult


class OpenTradeUseCase:
    def __init__(
        self,
        *,
        trades_repo,
        broker,
        trade_manager: TradeManager | None = None,
    ) -> None:
        self.trades_repo = trades_repo
        self.broker = broker
        self.trade_manager = trade_manager or TradeManager()

    def execute(
        self,
        *,
        trade: Trade,
        plan_code: str | None = None,
        plan_features: dict[str, Any] | str | None = None,
        allowed_symbols: Sequence[str] | None = None,
        enabled_symbols: Sequence[str] | None = None,
        max_open_trades_total: int | None = None,
        max_open_trades_per_symbol: int | None = None,
    ) -> OpenTradeResult:
        open_trades = self.trades_repo.get_open_trades()
        check = self.trade_manager.evaluate_new_trade(
            user_id=trade.user_id,
            symbol=trade.symbol,
            open_trades=open_trades,
            plan_code=plan_code,
            plan_features=plan_features,
            allowed_symbols=allowed_symbols,
            enabled_symbols=enabled_symbols,
            max_open_trades_total=max_open_trades_total,
            max_open_trades_per_symbol=max_open_trades_per_symbol,
        )
        if not check.allowed:
            return OpenTradeResult(opened=False, trade=None, check=check)

        opened_trade = self._open_with_broker(trade)
        self.trades_repo.create_trade(opened_trade)
        return OpenTradeResult(opened=True, trade=opened_trade, check=check)

    def _open_with_broker(self, trade: Trade) -> Trade:
        if hasattr(self.broker, "open_trade"):
            opened = self.broker.open_trade(trade)
        else:
            opened = self.broker.open_position(trade)

        if inspect.isawaitable(opened):
            raise RuntimeError("OpenTradeUseCase expects a synchronous broker")
        return opened
