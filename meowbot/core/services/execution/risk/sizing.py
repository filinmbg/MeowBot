from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class PositionSizing:
    stake_pct: float = 0.01   # 1% від equity
    leverage: int = 20

    def stake_usd(self, equity_usd: float) -> float:
        return float(equity_usd) * float(self.stake_pct)

    def qty_from_price(self, equity_usd: float, entry_price: float) -> float:
        """
        Проста формула для тесту:
        notional = stake_usd * leverage
        qty = notional / entry_price
        """
        if entry_price <= 0:
            return 0.0
        notional = self.stake_usd(equity_usd) * float(self.leverage)
        return notional / float(entry_price)