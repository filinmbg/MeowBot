from __future__ import annotations

import asyncio
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from meowbot.apps.telegram_bot.handlers_start import router as start_router
from meowbot.infra.mongo.client import MongoConn, MongoConfig
from meowbot.infra.postgres.client import get_pg_pool


ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")


async def main() -> None:
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not set in .env")

    pg_pool = await get_pg_pool()

    mongo = MongoConn(MongoConfig())
    mongo.connect()

    bot = Bot(token=bot_token)
    dp = Dispatcher()

    dp.include_router(start_router)

    dp.workflow_data.update(
        {
            "pg_pool": pg_pool,
            "mongo": mongo,
        }
    )

    print("✅ Telegram bot started")
    print("✅ Postgres connected")
    print("✅ Mongo connected")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await pg_pool.close()
        mongo.close()


if __name__ == "__main__":
    asyncio.run(main())