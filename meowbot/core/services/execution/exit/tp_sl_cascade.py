from __future__ import annotations

from typing import List

from meowbot.core.domain.types import Bar, Trade
from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.ports.broker import Broker
from meowbot.core.ports.trade_events_repo import TradeEventsRepository


def _side_value(side: object) -> str:
    return side.value if hasattr(side, "value") else str(side)


def _add_event(
    trade_events_repo: TradeEventsRepository | None,
    trade: Trade,
    event_type: str,
    ts: int,
    payload: dict | None = None,
) -> None:
    if trade_events_repo is None:
        return

    trade_events_repo.add_event(
        trade_id=trade.trade_id,
        event_type=event_type,
        ts=ts,
        symbol=trade.symbol,
        user_id=trade.user_id,
        mode=trade.mode,
        payload=payload or {},
    )


def apply_tp_sl_cascade(
    trade: Trade,
    bars: List[Bar],
    broker: Broker,
    trade_events_repo: TradeEventsRepository | None = None,
) -> Trade:
    if trade.status != TradeStatus.OPEN:
        return trade

    side_value = _side_value(trade.side)

    if side_value == "LONG":
        tp1 = trade.entry_price * 1.005
        tp2 = trade.entry_price * 1.010
        tp3 = trade.entry_price * 1.015
        tp4 = trade.entry_price * 1.020

        for bar in bars:
            # 1) Спочатку SL
            if bar.l <= trade.sl_price:
                trade = broker.close_position(trade, price=trade.sl_price, reason="SL_HIT")
                _add_event(
                    trade_events_repo,
                    trade,
                    "SL_HIT",
                    bar.close_time,
                    {
                        "price": trade.sl_price,
                        "qty_remaining": trade.qty_remaining,
                        "realized_pnl_usd": trade.realized_pnl_usd,
                    },
                )
                _add_event(
                    trade_events_repo,
                    trade,
                    "CLOSED",
                    bar.close_time,
                    {
                        "price": trade.close_price,
                        "reason": trade.exit_reason,
                        "realized_pnl_usd": trade.realized_pnl_usd,
                    },
                )
                return trade

            # 2) TP1
            if trade.tp_hit_count == 0 and bar.h >= tp1:
                trade = broker.reduce_position(
                    trade,
                    qty_to_reduce=trade.qty * 0.25,
                    price=tp1,
                    reason="TP1_HIT",
                )
                trade.tp_hit_count = 1
                trade.sl_price = trade.entry_price

                _add_event(
                    trade_events_repo,
                    trade,
                    "TP1_HIT",
                    bar.close_time,
                    {
                        "price": tp1,
                        "qty_remaining": trade.qty_remaining,
                        "remaining_pct": trade.remaining_pct,
                        "realized_pnl_usd": trade.realized_pnl_usd,
                    },
                )
                _add_event(
                    trade_events_repo,
                    trade,
                    "SL_MOVED",
                    bar.close_time,
                    {"sl_price": trade.sl_price},
                )

            # 3) TP2
            if trade.tp_hit_count == 1 and bar.h >= tp2:
                trade = broker.reduce_position(
                    trade,
                    qty_to_reduce=trade.qty * 0.25,
                    price=tp2,
                    reason="TP2_HIT",
                )
                trade.tp_hit_count = 2
                trade.sl_price = trade.entry_price * 1.002

                _add_event(
                    trade_events_repo,
                    trade,
                    "TP2_HIT",
                    bar.close_time,
                    {
                        "price": tp2,
                        "qty_remaining": trade.qty_remaining,
                        "remaining_pct": trade.remaining_pct,
                        "realized_pnl_usd": trade.realized_pnl_usd,
                    },
                )
                _add_event(
                    trade_events_repo,
                    trade,
                    "SL_MOVED",
                    bar.close_time,
                    {"sl_price": trade.sl_price},
                )

            # 4) TP3
            if trade.tp_hit_count == 2 and bar.h >= tp3:
                trade = broker.reduce_position(
                    trade,
                    qty_to_reduce=trade.qty * 0.25,
                    price=tp3,
                    reason="TP3_HIT",
                )
                trade.tp_hit_count = 3
                trade.sl_price = trade.entry_price * 1.004

                _add_event(
                    trade_events_repo,
                    trade,
                    "TP3_HIT",
                    bar.close_time,
                    {
                        "price": tp3,
                        "qty_remaining": trade.qty_remaining,
                        "remaining_pct": trade.remaining_pct,
                        "realized_pnl_usd": trade.realized_pnl_usd,
                    },
                )
                _add_event(
                    trade_events_repo,
                    trade,
                    "SL_MOVED",
                    bar.close_time,
                    {"sl_price": trade.sl_price},
                )

            # 5) TP4
            if trade.tp_hit_count == 3 and bar.h >= tp4:
                trade = broker.close_position(trade, price=tp4, reason="TP4_HIT")
                _add_event(
                    trade_events_repo,
                    trade,
                    "TP4_HIT",
                    bar.close_time,
                    {
                        "price": tp4,
                        "qty_remaining": trade.qty_remaining,
                        "realized_pnl_usd": trade.realized_pnl_usd,
                    },
                )
                _add_event(
                    trade_events_repo,
                    trade,
                    "CLOSED",
                    bar.close_time,
                    {
                        "price": trade.close_price,
                        "reason": trade.exit_reason,
                        "realized_pnl_usd": trade.realized_pnl_usd,
                    },
                )
                return trade

    else:
        tp1 = trade.entry_price * 0.995
        tp2 = trade.entry_price * 0.990
        tp3 = trade.entry_price * 0.985
        tp4 = trade.entry_price * 0.980

        for bar in bars:
            # 1) Спочатку SL
            if bar.h >= trade.sl_price:
                trade = broker.close_position(trade, price=trade.sl_price, reason="SL_HIT")
                _add_event(
                    trade_events_repo,
                    trade,
                    "SL_HIT",
                    bar.close_time,
                    {
                        "price": trade.sl_price,
                        "qty_remaining": trade.qty_remaining,
                        "realized_pnl_usd": trade.realized_pnl_usd,
                    },
                )
                _add_event(
                    trade_events_repo,
                    trade,
                    "CLOSED",
                    bar.close_time,
                    {
                        "price": trade.close_price,
                        "reason": trade.exit_reason,
                        "realized_pnl_usd": trade.realized_pnl_usd,
                    },
                )
                return trade

            # 2) TP1
            if trade.tp_hit_count == 0 and bar.l <= tp1:
                trade = broker.reduce_position(
                    trade,
                    qty_to_reduce=trade.qty * 0.25,
                    price=tp1,
                    reason="TP1_HIT",
                )
                trade.tp_hit_count = 1
                trade.sl_price = trade.entry_price

                _add_event(
                    trade_events_repo,
                    trade,
                    "TP1_HIT",
                    bar.close_time,
                    {
                        "price": tp1,
                        "qty_remaining": trade.qty_remaining,
                        "remaining_pct": trade.remaining_pct,
                        "realized_pnl_usd": trade.realized_pnl_usd,
                    },
                )
                _add_event(
                    trade_events_repo,
                    trade,
                    "SL_MOVED",
                    bar.close_time,
                    {"sl_price": trade.sl_price},
                )

            # 3) TP2
            if trade.tp_hit_count == 1 and bar.l <= tp2:
                trade = broker.reduce_position(
                    trade,
                    qty_to_reduce=trade.qty * 0.25,
                    price=tp2,
                    reason="TP2_HIT",
                )
                trade.tp_hit_count = 2
                trade.sl_price = trade.entry_price * 0.998

                _add_event(
                    trade_events_repo,
                    trade,
                    "TP2_HIT",
                    bar.close_time,
                    {
                        "price": tp2,
                        "qty_remaining": trade.qty_remaining,
                        "remaining_pct": trade.remaining_pct,
                        "realized_pnl_usd": trade.realized_pnl_usd,
                    },
                )
                _add_event(
                    trade_events_repo,
                    trade,
                    "SL_MOVED",
                    bar.close_time,
                    {"sl_price": trade.sl_price},
                )

            # 4) TP3
            if trade.tp_hit_count == 2 and bar.l <= tp3:
                trade = broker.reduce_position(
                    trade,
                    qty_to_reduce=trade.qty * 0.25,
                    price=tp3,
                    reason="TP3_HIT",
                )
                trade.tp_hit_count = 3
                trade.sl_price = trade.entry_price * 0.996

                _add_event(
                    trade_events_repo,
                    trade,
                    "TP3_HIT",
                    bar.close_time,
                    {
                        "price": tp3,
                        "qty_remaining": trade.qty_remaining,
                        "remaining_pct": trade.remaining_pct,
                        "realized_pnl_usd": trade.realized_pnl_usd,
                    },
                )
                _add_event(
                    trade_events_repo,
                    trade,
                    "SL_MOVED",
                    bar.close_time,
                    {"sl_price": trade.sl_price},
                )

            # 5) TP4
            if trade.tp_hit_count == 3 and bar.l <= tp4:
                trade = broker.close_position(trade, price=tp4, reason="TP4_HIT")
                _add_event(
                    trade_events_repo,
                    trade,
                    "TP4_HIT",
                    bar.close_time,
                    {
                        "price": tp4,
                        "qty_remaining": trade.qty_remaining,
                        "realized_pnl_usd": trade.realized_pnl_usd,
                    },
                )
                _add_event(
                    trade_events_repo,
                    trade,
                    "CLOSED",
                    bar.close_time,
                    {
                        "price": trade.close_price,
                        "reason": trade.exit_reason,
                        "realized_pnl_usd": trade.realized_pnl_usd,
                    },
                )
                return trade

    return trade