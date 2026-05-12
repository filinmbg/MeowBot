from __future__ import annotations

import inspect
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
        stats = await self._get_trade_stats_by_user(trading_user_id, mode="sandbox")
        total_realized_pnl = float(stats.get("realized_pnl_usd", 0.0) or 0.0)
        sandbox_balance = self.sandbox_start_balance_usd + total_realized_pnl

        return {
            "preferred_language": user_row.get("preferred_language", "uk"),
            "trading_mode": user_row.get("trading_mode", "sandbox"),
            "notifications_enabled": bool(user_row.get("notifications_enabled", True)),
            "default_stake_mode": user_row.get("default_stake_mode", "percent"),
            "default_stake_value": float(user_row.get("default_stake_value", 1.0) or 1.0),
            "default_leverage": int(user_row.get("default_leverage", 20) or 20),
            "open_trades_count": len(open_trades),
            "closed_trades_count": int(stats.get("closed_trades", 0) or 0),
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

    async def _get_trade_stats_by_user(self, user_id: str, *, mode: str | None = None) -> dict[str, Any]:
        if hasattr(self.trades_repo, "get_user_trade_stats"):
            result = self.trades_repo.get_user_trade_stats(user_id=user_id, mode=mode)
            return await result if inspect.isawaitable(result) else result

        rows = await self._get_trade_rows_by_user(user_id, mode=mode)
        closed_rows = [row for row in rows if self._status_value(row.get("status")) == "CLOSED"]
        wins = sum(1 for row in closed_rows if self._realized_pnl(row) > 0)
        return {
            "closed_trades": len(closed_rows),
            "wins": wins,
            "losses": max(len(closed_rows) - wins, 0),
            "realized_pnl_usd": sum(self._realized_pnl(row) for row in closed_rows),
        }

    async def _get_trade_rows_by_user(self, user_id: str, *, mode: str | None = None) -> list[dict[str, Any]]:
        if hasattr(self.trades_repo, "get_trade_rows_by_user"):
            result = self.trades_repo.get_trade_rows_by_user(user_id=user_id, mode=mode, limit=100)
            return await result if inspect.isawaitable(result) else result

        if hasattr(self.trades_repo, "get_trades"):
            result = self.trades_repo.get_trades(user_id=user_id, mode=mode, limit=100)
            trades = await result if inspect.isawaitable(result) else result
            return [
                {
                    "status": getattr(trade, "status", ""),
                    "realized_pnl_usd": getattr(trade, "realized_pnl_usd", 0.0),
                    "exchange_net_realized_pnl_usd": getattr(trade, "exchange_net_realized_pnl_usd", None),
                    "exchange_net_realized_pnl_usdt": getattr(trade, "exchange_net_realized_pnl_usdt", None),
                    "exchange_realized_pnl_usd": getattr(trade, "exchange_realized_pnl_usd", None),
                    "exchange_realized_pnl_usdt": getattr(trade, "exchange_realized_pnl_usdt", None),
                    "mode": getattr(trade, "mode", None),
                }
                for trade in trades
            ]

        return await self.trades_repo.get_closed_trades(user_id=user_id)

    def _status_value(self, value: Any) -> str:
        return str(value.value if hasattr(value, "value") else value)

    def _realized_pnl(self, row: dict[str, Any]) -> float:
        try:
            exchange_pnl = row.get("exchange_net_realized_pnl_usd")
            if exchange_pnl is None:
                exchange_pnl = row.get("exchange_net_realized_pnl_usdt")
            if exchange_pnl is None:
                exchange_pnl = row.get("exchange_realized_pnl_usd")
            if exchange_pnl is None:
                exchange_pnl = row.get("exchange_realized_pnl_usdt")
            if str(row.get("mode") or "").lower() == "live" and exchange_pnl is not None:
                return float(exchange_pnl or 0.0)
            return float(row.get("realized_pnl_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
