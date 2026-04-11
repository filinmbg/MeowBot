from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot
from dotenv import load_dotenv

from meowbot.core.services.notifications.trade_event_message_builder import (
    TradeEventMessageBuilder,
)
from meowbot.core.usecases.async_send_trade_events_to_telegram import (
    AsyncSendTradeEventsToTelegramUseCase,
)
from meowbot.infra.mongo.async_client import AsyncMongoConfig, AsyncMongoConn
from meowbot.infra.mongo.repos_async.telegram_users_repo_async import (
    TelegramUsersRepositoryMongoAsync,
)
from meowbot.infra.mongo.repos_async.trade_events_repo_async import (
    TradeEventsRepositoryMongoAsync,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("meowbot")


ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")


async def main() -> None:
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not set in .env")

    admin_chat_id = os.getenv("ADMIN_CHAT_ID")
    poll_seconds = float(os.getenv("TELEGRAM_EVENTS_POLL_SECONDS", "2"))

    mongo = AsyncMongoConn(AsyncMongoConfig())
    await mongo.connect()

    bot = Bot(token=bot_token)

    trade_events_repo = TradeEventsRepositoryMongoAsync(mongo.db)
    telegram_users_repo = TelegramUsersRepositoryMongoAsync(mongo.db)
    message_builder = TradeEventMessageBuilder()

    usecase = AsyncSendTradeEventsToTelegramUseCase(
        trade_events_repo=trade_events_repo,
        telegram_users_repo=telegram_users_repo,
        bot=bot,
        message_builder=message_builder,
        admin_chat_id=admin_chat_id,
    )

    log.info("[telegram-event-worker] started")

    try:
        while True:
            sent = await usecase.run_once()
            if sent:
                log.info("[telegram-event-worker] sent=%s", sent)
            await asyncio.sleep(poll_seconds)
    finally:
        await bot.session.close()
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(main())


