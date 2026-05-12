from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from meowbot.core.services.notifications.stats_message_builder import StatsMessageBuilder
from meowbot.core.usecases.get_stats_usecase import GetStatsUseCase
from meowbot.infra.mongo.client_async import get_async_mongo_db
from meowbot.infra.mongo.repos_async.trades_repo_async import TradesRepositoryMongoAsync


router = Router()


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is unavailable.")
        return

    db = await get_async_mongo_db()
    repo = TradesRepositoryMongoAsync(db)
    stats_uc = GetStatsUseCase(repo)
    builder = StatsMessageBuilder()

    stats = await stats_uc.get_user_stats(f"tg:{user.id}", mode="sandbox")
    text = builder.build(stats, "Sandbox stats", "No trades yet.")
    await message.answer(text, parse_mode="HTML")
