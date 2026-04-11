from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from meowbot.core.services.notifications.telegram_notify_service import (
    TelegramNotifyService,
)
from meowbot.core.services.notifications.telegram_trade_message_formatter import (
    TelegramTradeMessageFormatter,
)
from meowbot.core.usecases.get_paper_trade_stats import GetPaperTradeStatsUseCase
from meowbot.infra.mongo.client import MongoConn, MongoConfig
from meowbot.infra.mongo.repos.trades_repo import TradesRepositoryMongo


router = Router()


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    mongo = MongoConn(MongoConfig())
    mongo.connect()
    try:
        repo = TradesRepositoryMongo(mongo.db)
        stats_uc = GetPaperTradeStatsUseCase(repo, test_user_ids={"demo_user"})
        formatter = TelegramTradeMessageFormatter()
        notifier = TelegramNotifyService()

        stats = stats_uc.get_test_user_stats()
        text = formatter.format_stats_summary(stats, "Статистика sandbox")

        await message.answer(text, parse_mode="HTML")
    finally:
        mongo.close()