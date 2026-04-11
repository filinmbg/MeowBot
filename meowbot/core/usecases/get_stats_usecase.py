from __future__ import annotations

from typing import Any


class GetStatsUseCase:
    def __init__(self, trades_repo) -> None:
        self.trades_repo = trades_repo

    async def get_user_stats(self, user_id: str) -> dict[str, Any]:
        trades = await self.trades_repo.get_closed_trades(user_id=user_id)
        return self._build_stats(trades)

    async def get_global_stats(self) -> dict[str, Any]:
        trades = await self.trades_repo.get_all_closed_trades()
        return self._build_stats(trades)

    async def get_symbol_stats(self, symbol: str) -> dict[str, Any]:
        trades = await self.trades_repo.get_closed_by_symbol(symbol=symbol)
        return self._build_stats(trades)

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

        pnl_values = [float(t.get("realized_pnl_usd", 0.0) or 0.0) for t in trades]
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