from __future__ import annotations

from dataclasses import replace

from meowbot.core.domain.types import Trade
from meowbot.core.domain.enums import TradeStatus


class PaperBroker:
    def ping(self) -> bool:
        return True

    def open_position(self, trade: Trade) -> Trade:
        # У paper mode нічого не виконуємо зовні, просто повертаємо trade
        return trade

    def reduce_position(self, trade: Trade, qty_to_reduce: float, price: float, reason: str) -> Trade:
        qty_to_reduce = min(qty_to_reduce, trade.qty_remaining)
        new_qty_remaining = trade.qty_remaining - qty_to_reduce

        # дуже спрощений pnl, потім зробимо окремо нормальний futures pnl
        side_value = trade.side.value if hasattr(trade.side, "value") else str(trade.side)

        if side_value == "LONG":
            pnl = (price - trade.entry_price) * qty_to_reduce * trade.leverage
        else:
            pnl = (trade.entry_price - price) * qty_to_reduce * trade.leverage

        return replace(
            trade,
            qty_remaining=new_qty_remaining,
            remaining_pct=(new_qty_remaining / trade.qty) if trade.qty > 0 else 0.0,
            realized_pnl_usd=trade.realized_pnl_usd + pnl,
            exit_reason=reason,
        )

    def close_position(self, trade: Trade, price: float, reason: str) -> Trade:
        side_value = trade.side.value if hasattr(trade.side, "value") else str(trade.side)

        if side_value == "LONG":
            pnl = (price - trade.entry_price) * trade.qty_remaining * trade.leverage
        else:
            pnl = (trade.entry_price - price) * trade.qty_remaining * trade.leverage

        return replace(
            trade,
            qty_remaining=0.0,
            remaining_pct=0.0,
            realized_pnl_usd=trade.realized_pnl_usd + pnl,
            status=TradeStatus.CLOSED,
            close_price=price,
            exit_reason=reason,
        )