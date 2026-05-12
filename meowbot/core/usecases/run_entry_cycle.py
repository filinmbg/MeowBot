from __future__ import annotations

from dataclasses import replace
from typing import Optional

from meowbot.core.domain.types import Trade, Bar
from meowbot.core.domain.enums import Side, TradeStatus, SignalAction
from meowbot.core.ports.bars_repo import BarsRepository
from meowbot.core.ports.trades_repo import TradesRepository
from meowbot.core.ports.entry_strategy import EntryStrategy
from meowbot.core.ports.broker import Broker
from meowbot.core.services.execution.entry.portfolio_gate import PortfolioGate
from meowbot.core.ports.account_repo import AccountRepository
from meowbot.core.services.execution.risk.sizing import PositionSizing
from meowbot.core.usecases.open_trade import OpenTradeUseCase


class RunEntryCycleUseCase:
    def __init__(
        self,
        bars_repo: BarsRepository,
        trades_repo: TradesRepository,
        broker: Broker,
        entry_strategy: EntryStrategy,
        gate: PortfolioGate,
        features_ver: str,
        account_repo: AccountRepository | None = None,   # ← додали
        sizing: PositionSizing | None = None,            # ← додали
        tail_needed: int = 1,
    ):
        self.bars_repo = bars_repo
        self.trades_repo = trades_repo
        self.broker = broker
        self.entry_strategy = entry_strategy
        self.gate = gate
        self.features_ver = features_ver
        self.account_repo = account_repo                # ← додали
        self.sizing = sizing                            # ← додали
        self.tail_needed = tail_needed
        self.open_trade_usecase = OpenTradeUseCase(
            trades_repo=trades_repo,
            broker=broker,
        )
    def run(
        self,
        symbol: str,
        tf: str,
        now_ms: int,
        stake_usd: float = 1.0,
        leverage: int = 20,
        qty: float = 1.0,
        sl_price: float = 0.0,
        model_id: str = "rule_based_v1",
    ) -> Optional[str]:
        tail = self.bars_repo.get_tail(
            symbol=symbol,
            tf=tf,
            n=self.tail_needed,
            features_ver=self.features_ver,
            require_features_ok=True,
        )
        if not tail:
            return None

        last_bar: Bar = tail[-1]
        signal = self.entry_strategy.decide(last_bar)
        # антидубль: на один (symbol, tf, close_time) entry робимо лише 1 раз
        if self.trades_repo.was_entry_bar_used(symbol, tf, last_bar.close_time):
            return None


        if not self.gate.allow(symbol, signal):
            return None

        if signal.action == SignalAction.LONG:
            side = Side.LONG
        elif signal.action == SignalAction.SHORT:
            side = Side.SHORT
        else:
            return None

        if self.account_repo and self.sizing:
            equity = self.account_repo.get_equity_usd()
            stake_usd = self.sizing.stake_usd(equity)
            leverage = self.sizing.leverage
            qty = self.sizing.qty_from_price(equity, last_bar.c)

        trade = Trade(
            trade_id=f"{symbol}_{tf}_{last_bar.close_time}",
            user_id="demo_user",
            symbol=symbol,
            side=side,
            status=TradeStatus.OPEN,
            opened_at=now_ms,
            entry_price=last_bar.c,
            qty=qty,
            leverage=leverage,
            stake_usd=stake_usd,
            tf_entry=tf,
            model_id=model_id,
            sl_price=sl_price,
            exit_last_check_at=0,
            entry_bar_close_time=last_bar.close_time,
        )

        result = self.open_trade_usecase.execute(
            trade=trade,
            max_open_trades_total=self.gate.limits.max_open_trades_total,
            max_open_trades_per_symbol=self.gate.limits.max_open_trades_per_symbol,
        )
        if not result.opened or result.trade is None:
            return None
        return result.trade.trade_id
