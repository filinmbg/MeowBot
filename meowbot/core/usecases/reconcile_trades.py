from __future__ import annotations

from dataclasses import replace
from typing import Optional, List

from meowbot.core.domain.types import Trade, Bar
from meowbot.core.domain.enums import Side
from meowbot.core.ports.trades_repo import TradesRepository
from meowbot.core.ports.exchange_market_data import ExchangeMarketData


def floor_to_1m(ts_ms: int) -> int:
    return ts_ms - (ts_ms % 60_000)


def calc_pnl_usd(side: Side, entry: float, close: float, qty: float, leverage: int) -> float:
    # дуже спрощено: pnl = (delta * qty) * leverage
    # комісії поки ігноруємо (тут тест пайплайна)
    if side == Side.LONG:
        return (close - entry) * qty * leverage
    else:
        return (entry - close) * qty * leverage


class ReconcileOpenTradesUseCase:
    """
    Бере всі OPEN trades, доганяє їх 1m барами з біржі і робить простий exit по SL.
    """

    def __init__(self, trades_repo: TradesRepository, exchange: ExchangeMarketData):
        self.trades_repo = trades_repo
        self.exchange = exchange

    def run(self, now_ms: int) -> None:
        end = floor_to_1m(now_ms)
        open_trades = self.trades_repo.get_open_trades()

        for trade in open_trades:
            start = trade.exit_last_check_at
            if start >= end:
                continue

            m1_bars: List[Bar] = self.exchange.fetch_klines(trade.symbol, "1m", start, end)
            if not m1_bars:
                # навіть якщо барів нема — оновимо курсор, щоб не зациклюватись
                trade.exit_last_check_at = end
                self.trades_repo.update_trade(trade)
                continue

            closed = False
            close_price: Optional[float] = None
            close_ts: Optional[int] = None

            # Простий SL:
            # LONG: якщо low <= sl_price -> close
            # SHORT: якщо high >= sl_price -> close
            for b in m1_bars:
                if trade.side == Side.LONG and b.l <= trade.sl_price:
                    closed = True
                    close_price = trade.sl_price
                    close_ts = b.close_time
                    break
                if trade.side == Side.SHORT and b.h >= trade.sl_price:
                    closed = True
                    close_price = trade.sl_price
                    close_ts = b.close_time
                    break

            if closed and close_price is not None and close_ts is not None:
                pnl = calc_pnl_usd(trade.side, trade.entry_price, close_price, trade.qty, trade.leverage)
                self.trades_repo.close_trade(
                    trade_id=trade.trade_id,
                    closed_at=close_ts,
                    close_price=close_price,
                    reason="SL_HIT",
                    pnl_usd=pnl,
                )
            else:
                # не закрились — просто оновлюємо курсор до end
                trade.exit_last_check_at = end
                self.trades_repo.update_trade(trade)
