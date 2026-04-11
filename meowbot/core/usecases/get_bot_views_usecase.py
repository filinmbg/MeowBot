from __future__ import annotations

from typing import Any


class GetBotViewsUseCase:
    def __init__(
        self,
        *,
        trades_repo,
        telegram_users_repo,
        trading_user_id: str = "demo_user",
        sandbox_start_balance_usd: float = 1000.0,
        active_symbols: list[str] | None = None,
        active_tfs: list[str] | None = None,
    ) -> None:
        self.trades_repo = trades_repo
        self.telegram_users_repo = telegram_users_repo
        self.trading_user_id = trading_user_id
        self.sandbox_start_balance_usd = sandbox_start_balance_usd
        self.active_symbols = active_symbols or []
        self.active_tfs = active_tfs or []

    async def get_my_bot_view(self, telegram_id: int) -> dict[str, Any]:
        user_row = await self.telegram_users_repo.get_by_telegram_id(telegram_id) or {}

        trading_user_id = str(user_row.get("trading_user_id") or self.trading_user_id)

        open_trades = await self.trades_repo.get_open_trades_by_user(trading_user_id, limit=20)
        closed_trades = await self.trades_repo.get_closed_trades(user_id=trading_user_id)

        total_realized_pnl = sum(float(x.get("realized_pnl_usd", 0.0) or 0.0) for x in closed_trades)
        sandbox_balance = self.sandbox_start_balance_usd + total_realized_pnl

        return {
            "preferred_language": user_row.get("preferred_language", "uk"),
            "trading_mode": user_row.get("trading_mode", "sandbox"),
            "notifications_enabled": bool(user_row.get("notifications_enabled", True)),
            "default_stake_mode": user_row.get("default_stake_mode", "percent"),
            "default_stake_value": float(user_row.get("default_stake_value", 1.0) or 1.0),
            "default_leverage": int(user_row.get("default_leverage", 20) or 20),
            "open_trades_count": len(open_trades),
            "closed_trades_count": len(closed_trades),
            "sandbox_balance_usd": sandbox_balance,
            "active_symbols": self.active_symbols,
            "active_tfs": self.active_tfs,
            "trading_user_id": trading_user_id,
            "bot_enabled": bool(user_row.get("bot_enabled", True)),
        }

    async def get_trades_view(self, telegram_id: int) -> dict[str, Any]:
        user_row = await self.telegram_users_repo.get_by_telegram_id(telegram_id) or {}
        trading_user_id = str(user_row.get("trading_user_id") or self.trading_user_id)

        open_trades = await self.trades_repo.get_open_trades_by_user(trading_user_id, limit=10)
        recent_closed = await self.trades_repo.get_recent_closed_trades(user_id=trading_user_id, limit=10)

        return {
            "open_trades": open_trades,
            "recent_closed_trades": recent_closed,
            "trading_user_id": trading_user_id,
        }