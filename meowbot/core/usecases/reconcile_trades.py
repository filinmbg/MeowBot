from __future__ import annotations

import logging

from meowbot.core.domain.types import Bar
from meowbot.core.ports.trades_repo import TradesRepository
from meowbot.core.ports.exchange_market_data import ExchangeMarketData
from meowbot.core.ports.broker import Broker
from meowbot.core.ports.trade_events_repo import TradeEventsRepository
from meowbot.core.services.execution.exit.tp_sl_cascade import apply_tp_sl_cascade

log = logging.getLogger("meowbot")


def floor_to_1m(ts_ms: int) -> int:
    return ts_ms - (ts_ms % 60_000)


def _side_value(side: object) -> str:
    return side.value if hasattr(side, "value") else str(side)


def _status_value(status: object) -> str:
    return status.value if hasattr(status, "value") else str(status)


def _move_pct(side: object, entry_price: float, current_price: float) -> float:
    if entry_price == 0:
        return 0.0

    sv = _side_value(side)
    if sv == "LONG":
        return ((current_price - entry_price) / entry_price) * 100.0
    return ((entry_price - current_price) / entry_price) * 100.0


class ReconcileOpenTradesUseCase:
    def __init__(
        self,
        trades_repo: TradesRepository,
        exchange: ExchangeMarketData,
        broker: Broker,
        trade_events_repo: TradeEventsRepository | None = None,
    ):
        self.trades_repo = trades_repo
        self.exchange = exchange
        self.broker = broker
        self.trade_events_repo = trade_events_repo

    def run(self, now_ms: int) -> None:
        end = floor_to_1m(now_ms)
        open_trades = self.trades_repo.get_open_trades()

        log.info("[exit] open_trades=%d end=%s", len(open_trades), end)

        for trade in open_trades:
            processed_until = trade.exit_last_check_at
            if processed_until >= end:
                log.info(
                    "[exit] trade_id=%s symbol=%s: skip (processed_until=%s >= end=%s)",
                    trade.trade_id,
                    trade.symbol,
                    processed_until,
                    end,
                )
                continue

            fetch_start = max(0, processed_until - 5 * 60_000)

            log.info(
                "[exit] trade_id=%s symbol=%s: raw fetch from %s to %s",
                trade.trade_id,
                trade.symbol,
                fetch_start,
                end,
            )

            raw_bars: list[Bar] = self.exchange.fetch_klines(trade.symbol, "1m", fetch_start, end)

            log.info(
                "[exit] trade_id=%s symbol=%s: fetched %d raw bars",
                trade.trade_id,
                trade.symbol,
                len(raw_bars),
            )

            m1_bars = [
                b for b in raw_bars
                if processed_until < b.close_time <= end
            ]
            m1_bars.sort(key=lambda x: x.close_time)

            log.info(
                "[exit] trade_id=%s symbol=%s: %d filtered bars to process",
                trade.trade_id,
                trade.symbol,
                len(m1_bars),
            )

            if not m1_bars:
                trade.exit_last_check_at = end
                self.trades_repo.update_trade(trade)

                log.info(
                    "[exit] %s trade_id=%s entry=%.4f now=%.4f move=%+.3f%% sl=%.4f tp_hit_count=%s qty_remaining=%.8f | no new filtered bars, cursor -> %s",
                    trade.symbol,
                    trade.trade_id,
                    trade.entry_price,
                    trade.entry_price,
                    0.0,
                    trade.sl_price,
                    trade.tp_hit_count,
                    trade.qty_remaining,
                    end,
                )
                continue

            last_bar = m1_bars[-1]
            current_price = float(last_bar.c)
            move_pct_before = _move_pct(trade.side, trade.entry_price, current_price)

            log.info(
                "[exit] %s trade_id=%s entry=%.4f now=%.4f move=%+.3f%% sl=%.4f tp_hit_count=%s qty_remaining=%.8f",
                trade.symbol,
                trade.trade_id,
                trade.entry_price,
                current_price,
                move_pct_before,
                trade.sl_price,
                trade.tp_hit_count,
                trade.qty_remaining,
            )

            updated = apply_tp_sl_cascade(
                trade,
                m1_bars,
                self.broker,
                self.trade_events_repo,
            )
            updated.exit_last_check_at = end
            self.trades_repo.update_trade(updated)

            status_value = _status_value(updated.status)
            close_or_now = updated.close_price if updated.close_price is not None else current_price
            move_pct_after = _move_pct(updated.side, updated.entry_price, float(close_or_now))

            log.info(
                "[exit] %s trade_id=%s status=%s entry=%.4f now=%.4f move=%+.3f%% sl=%.4f tp_hit_count=%s qty_remaining=%.8f realized_pnl=%.6f",
                updated.symbol,
                updated.trade_id,
                status_value,
                updated.entry_price,
                float(close_or_now),
                move_pct_after,
                updated.sl_price,
                updated.tp_hit_count,
                updated.qty_remaining,
                updated.realized_pnl_usd,
            )

            if status_value == "CLOSED":
                log.info(
                    "[exit] %s trade_id=%s CLOSED reason=%s close_price=%.4f total_move=%+.3f%% realized_pnl=%.6f",
                    updated.symbol,
                    updated.trade_id,
                    updated.exit_reason,
                    float(updated.close_price),
                    move_pct_after,
                    updated.realized_pnl_usd,
                )