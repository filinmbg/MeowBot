from __future__ import annotations

from dataclasses import asdict
from typing import Any

from meowbot.core.configs.sandbox_trading import DEFAULT_SANDBOX_TRADING_CONFIG
from meowbot.core.ports.trades_repo import TradesRepository
from meowbot.core.services.stats.paper_trade_stats_service import (
    PaperTradeStatsService,
)


class GetPaperTradeStatsUseCase:
    def __init__(
        self,
        trades_repo: TradesRepository,
        *,
        test_user_ids: set[str] | None = None,
    ) -> None:
        self.trades_repo = trades_repo
        self.test_user_ids = test_user_ids or {"demo_user"}
        self.stats_service = PaperTradeStatsService(DEFAULT_SANDBOX_TRADING_CONFIG)

    def get_global_stats(
        self,
        *,
        symbol: str | None = None,
        tf: str | None = None,
        model_id: str | None = None,
        mode: str = "sandbox",
        limit: int = 5000,
    ) -> dict[str, Any]:
        trades = self.trades_repo.get_trades(
            user_id=None,
            symbol=symbol,
            tf=tf,
            mode=mode,
            model_id=model_id,
            limit=limit,
        )
        stats = self.stats_service.calculate_for_all(trades)
        result = asdict(stats)
        result["scope"] = "global"
        result["symbol"] = symbol
        result["tf"] = tf
        result["model_id"] = model_id
        result["mode"] = mode
        return result

    def get_test_user_stats(
        self,
        *,
        symbol: str | None = None,
        tf: str | None = None,
        model_id: str | None = None,
        mode: str = "sandbox",
        limit: int = 5000,
    ) -> dict[str, Any]:
        trades = self.trades_repo.get_trades(
            user_id=None,
            symbol=symbol,
            tf=tf,
            mode=mode,
            model_id=model_id,
            limit=limit,
        )
        stats = self.stats_service.calculate_for_test_users(
            trades,
            test_user_ids=self.test_user_ids,
        )
        result = asdict(stats)
        result["scope"] = "test_users"
        result["test_user_ids"] = sorted(self.test_user_ids)
        result["symbol"] = symbol
        result["tf"] = tf
        result["model_id"] = model_id
        result["mode"] = mode
        return result

    def get_user_stats(
        self,
        *,
        user_id: str,
        symbol: str | None = None,
        tf: str | None = None,
        model_id: str | None = None,
        mode: str = "sandbox",
        limit: int = 5000,
    ) -> dict[str, Any]:
        trades = self.trades_repo.get_trades(
            user_id=user_id,
            symbol=symbol,
            tf=tf,
            mode=mode,
            model_id=model_id,
            limit=limit,
        )
        stats = self.stats_service.calculate_for_user(trades, user_id)
        result = asdict(stats)
        result["scope"] = "user"
        result["user_id"] = user_id
        result["symbol"] = symbol
        result["tf"] = tf
        result["model_id"] = model_id
        result["mode"] = mode
        return result