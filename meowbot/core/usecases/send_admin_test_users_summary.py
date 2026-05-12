from __future__ import annotations

import time
from typing import Any

from meowbot.core.configs.strategy_version_test_users import (
    expand_strategy_version_test_user_emails,
)


class SendAdminTestUsersSummaryUseCase:
    def __init__(
        self,
        *,
        admin_test_users_repo,
        admin_trade_stats_repo,
        bot,
        message_builder,
        admin_chat_ids: list[int],
        test_user_emails: list[str],
        sandbox_start_balance_usd: float = 1000.0,
        risk_period_hours: int = 3,
    ) -> None:
        self.admin_test_users_repo = admin_test_users_repo
        self.admin_trade_stats_repo = admin_trade_stats_repo
        self.bot = bot
        self.message_builder = message_builder
        self.admin_chat_ids = admin_chat_ids
        self.test_user_emails = expand_strategy_version_test_user_emails(test_user_emails)
        self.sandbox_start_balance_usd = float(sandbox_start_balance_usd)
        self.risk_period_hours = int(risk_period_hours)

    async def execute(self) -> dict[str, Any]:
        users = await self.admin_test_users_repo.get_test_users_by_emails(self.test_user_emails)
        if not users:
            return {
                "users": 0,
                "sent_to_admin_chats": 0,
            }

        runtime_user_ids = [str(x["runtime_user_id"]) for x in users]
        trade_summary = await self.admin_trade_stats_repo.get_trade_summary_by_user_ids(
            user_ids=runtime_user_ids,
        )
        strategy_summary_rows = []
        if hasattr(self.admin_trade_stats_repo, "get_strategy_summary_by_user_ids"):
            strategy_summary_rows = await self.admin_trade_stats_repo.get_strategy_summary_by_user_ids(
                user_ids=runtime_user_ids,
            )

        ts_from_ms = int(time.time() * 1000) - self.risk_period_hours * 60 * 60 * 1000
        risk_blocks = await self.admin_trade_stats_repo.count_risk_blocks_since(
            user_ids=runtime_user_ids,
            ts_from_ms=ts_from_ms,
        )

        active_cooldowns = await self.admin_test_users_repo.count_active_cooldowns_by_runtime_user_ids(
            runtime_user_ids,
        )

        rows: list[dict[str, Any]] = []
        for user in users:
            runtime_user_id = str(user["runtime_user_id"])
            summary = trade_summary.get(runtime_user_id, {})

            realized_pnl = float(summary.get("realized_pnl_usd", 0.0) or 0.0)
            start_balance = self.sandbox_start_balance_usd
            current_balance = start_balance + realized_pnl
            roi_pct = 0.0
            if start_balance > 0:
                roi_pct = (realized_pnl / start_balance) * 100.0

            rows.append(
                {
                    **user,
                    "open_trades": int(summary.get("open_trades", 0) or 0),
                    "closed_trades": int(summary.get("closed_trades", 0) or 0),
                    "wins": int(summary.get("wins", 0) or 0),
                    "losses": int(summary.get("losses", 0) or 0),
                    "realized_pnl_usd": realized_pnl,
                    "start_balance_usd": start_balance,
                    "current_balance_usd": current_balance,
                    "roi_pct": roi_pct,
                    "active_cooldowns": int(active_cooldowns.get(runtime_user_id, 0) or 0),
                    "risk_blocks_period": int(risk_blocks.get(runtime_user_id, 0) or 0),
                }
            )

        text = self.message_builder.build(
            rows,
            period_hours=self.risk_period_hours,
            strategy_summary_rows=strategy_summary_rows,
        )

        sent = 0
        for chat_id in self.admin_chat_ids:
            try:
                await self.bot.send_message(
                    chat_id=int(chat_id),
                    text=text,
                    parse_mode="HTML",
                )
                sent += 1
            except Exception:
                pass

        return {
            "users": len(rows),
            "sent_to_admin_chats": sent,
        }
