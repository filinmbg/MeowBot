from __future__ import annotations

import inspect
from typing import Any


class GetStatsUseCase:
    def __init__(self, trades_repo) -> None:
        self.trades_repo = trades_repo

    async def get_user_stats(self, user_id: str, *, mode: str | None = None) -> dict[str, Any]:
        if hasattr(self.trades_repo, "get_user_trade_stats"):
            stats = self.trades_repo.get_user_trade_stats(user_id=user_id, mode=mode)
            row = await stats if inspect.isawaitable(stats) else stats
            return self._normalize_aggregate_stats(row)

        trades = await self.trades_repo.get_closed_trades(user_id=user_id, mode=mode)
        return self._build_stats(trades)

    async def get_global_stats(self, *, mode: str | None = None) -> dict[str, Any]:
        if hasattr(self.trades_repo, "get_global_trade_stats"):
            stats = self.trades_repo.get_global_trade_stats(mode=mode)
            row = await stats if inspect.isawaitable(stats) else stats
            return self._normalize_aggregate_stats(row)

        trades = await self.trades_repo.get_all_closed_trades(mode=mode)
        return self._build_stats(trades)

    async def get_symbol_stats(self, symbol: str, *, mode: str | None = None) -> dict[str, Any]:
        if hasattr(self.trades_repo, "get_symbol_trade_stats"):
            stats = self.trades_repo.get_symbol_trade_stats(symbol=symbol, mode=mode)
            row = await stats if inspect.isawaitable(stats) else stats
            return self._normalize_aggregate_stats(row)

        trades = await self.trades_repo.get_closed_by_symbol(symbol=symbol, mode=mode)
        return self._build_stats(trades)

    def _normalize_aggregate_stats(self, row: dict[str, Any]) -> dict[str, Any]:
        total = int(row.get("closed_trades", row.get("total", 0)) or 0)
        wins = int(row.get("wins", 0) or 0)
        losses = int(row.get("losses", max(total - wins, 0)) or 0)
        pnl = float(row.get("realized_pnl_usd", row.get("pnl", 0.0)) or 0.0)
        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "winrate": (wins / total * 100.0) if total else 0.0,
            "pnl": pnl,
            "open_trades": int(row.get("open_trades", 0) or 0),
        }

    def _build_stats(self, trades: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(trades)
        if total == 0:
            return {
                "total": 0,
                "wins": 0,
                "losses": 0,
                "winrate": 0.0,
                "pnl": 0.0,
            }

        pnl_values = [self._realized_pnl(t) for t in trades]
        wins = sum(1 for pnl in pnl_values if pnl > 0)
        losses = total - wins
        pnl = sum(pnl_values)
        winrate = (wins / total) * 100 if total else 0.0

        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "winrate": winrate,
            "pnl": pnl,
        }

    def _realized_pnl(self, trade: dict[str, Any]) -> float:
        try:
            exchange_pnl = trade.get("exchange_net_realized_pnl_usd")
            if exchange_pnl is None:
                exchange_pnl = trade.get("exchange_net_realized_pnl_usdt")
            if exchange_pnl is None:
                exchange_pnl = trade.get("exchange_realized_pnl_usd")
            if exchange_pnl is None:
                exchange_pnl = trade.get("exchange_realized_pnl_usdt")
            if str(trade.get("mode") or "").lower() == "live" and exchange_pnl is not None:
                return float(exchange_pnl or 0.0)
            return float(trade.get("realized_pnl_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
