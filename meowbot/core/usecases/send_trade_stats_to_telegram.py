from __future__ import annotations

from meowbot.core.services.notifications.telegram_notify_service import (
    TelegramNotifyService,
)
from meowbot.core.services.notifications.telegram_trade_message_formatter import (
    TelegramTradeMessageFormatter,
)
from meowbot.core.usecases.get_paper_trade_stats import GetPaperTradeStatsUseCase


class SendTradeStatsToTelegramUseCase:
    def __init__(
        self,
        stats_usecase: GetPaperTradeStatsUseCase,
        telegram_notify: TelegramNotifyService,
        formatter: TelegramTradeMessageFormatter,
    ) -> None:
        self.stats_usecase = stats_usecase
        self.telegram_notify = telegram_notify
        self.formatter = formatter

    async def send_global_stats(self) -> None:
        stats = self.stats_usecase.get_global_stats()
        text = self.formatter.format_stats_summary(stats, "Глобальна статистика")
        await self.telegram_notify.send_message(text)

    async def send_test_users_stats(self) -> None:
        stats = self.stats_usecase.get_test_user_stats()
        text = self.formatter.format_stats_summary(stats, "Статистика test users")
        await self.telegram_notify.send_message(text)

    async def send_user_stats(self, user_id: str, *, chat_id: str | None = None) -> None:
        stats = self.stats_usecase.get_user_stats(user_id=user_id)
        text = self.formatter.format_stats_summary(stats, f"Статистика user {user_id}")
        await self.telegram_notify.send_message(text, chat_id=chat_id)