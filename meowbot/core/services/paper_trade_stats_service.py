from __future__ import annotations

from typing import List

from meowbot.core.services.paper_trade_runtime_service import PaperTrade


class PaperTradeStatsService:
    def calculate(self, trades: List[PaperTrade]) -> dict:
        closed = [t for t in trades if t.status == "closed"]

        if not closed:
            return {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "winrate": 0,
                "avg_pnl": 0,
            }

        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]

        return {
            "trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "winrate": round(len(wins) / len(closed) * 100, 2),
            "avg_pnl": round(sum(t.pnl for t in closed) / len(closed), 4),
        }