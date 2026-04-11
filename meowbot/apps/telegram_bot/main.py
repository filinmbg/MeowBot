from __future__ import annotations

import asyncio
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from meowbot.apps.telegram_bot.handlers_menu import router as menu_router
from meowbot.apps.telegram_bot.handlers_settings import router as settings_router
from meowbot.apps.telegram_bot.handlers_start import router as start_router
from meowbot.apps.telegram_bot.handlers.stats import register_stats_handlers
from meowbot.apps.telegram_bot.i18n.service import I18nService
from meowbot.core.services.notifications.bot_views_message_builder import BotViewsMessageBuilder
from meowbot.core.services.notifications.stats_message_builder import StatsMessageBuilder
from meowbot.core.usecases.get_bot_views_usecase import GetBotViewsUseCase
from meowbot.core.usecases.get_stats_usecase import GetStatsUseCase
from meowbot.infra.mongo.async_client import AsyncMongoConfig, AsyncMongoConn
from meowbot.infra.mongo.repos_async.telegram_users_repo_async import (
    TelegramUsersRepositoryMongoAsync,
)
from meowbot.infra.mongo.repos_async.trades_repo_async import TradesRepositoryMongoAsync
from meowbot.infra.postgres.client import get_pg_pool


ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")


DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT",
    "SOLUSDT", "DOGEUSDT", "TRXUSDT", "LINKUSDT", "LTCUSDT",
    "BCHUSDT", "XLMUSDT", "ETCUSDT", "ATOMUSDT", "FILUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT", "SUIUSDT", "NEARUSDT",
    "AVAXUSDT", "DOTUSDT", "MATICUSDT", "UNIUSDT", "AAVEUSDT",
    "ALGOUSDT", "VETUSDT", "EOSUSDT", "ICPUSDT", "SANDUSDT",
    "MANAUSDT", "APEUSDT", "RUNEUSDT", "GALAUSDT", "HBARUSDT",
    "INJUSDT", "SEIUSDT", "FTMUSDT", "THETAUSDT", "AXSUSDT",
    "CHZUSDT", "CRVUSDT", "1INCHUSDT", "ENJUSDT", "ZILUSDT",
    "KAVAUSDT", "WAVESUSDT", "COMPUSDT", "SNXUSDT", "KSMUSDT",
]

ACTIVE_SYMBOLS = [
    x.strip().upper()
    for x in os.getenv("ACTIVE_SYMBOLS", ",".join(DEFAULT_SYMBOLS)).split(",")
    if x.strip()
]

ACTIVE_TFS = ["15m", "30m", "1h", "2h", "4h"]


async def main() -> None:
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not set in .env")

    pg_pool = await get_pg_pool()

    mongo = AsyncMongoConn(AsyncMongoConfig())
    await mongo.connect()

    bot = Bot(token=bot_token)
    dp = Dispatcher()

    trades_repo = TradesRepositoryMongoAsync(mongo.db)
    telegram_users_repo = TelegramUsersRepositoryMongoAsync(mongo.db)
    i18n_service = I18nService()

    stats_uc = GetStatsUseCase(trades_repo)
    stats_builder = StatsMessageBuilder()

    bot_views_uc = GetBotViewsUseCase(
        trades_repo=trades_repo,
        telegram_users_repo=telegram_users_repo,
        trading_user_id=os.getenv("TRADING_OWNER_USER_ID", "demo_user"),
        sandbox_start_balance_usd=float(os.getenv("SANDBOX_START_BALANCE_USD", "1000")),
        active_symbols=ACTIVE_SYMBOLS,
        active_tfs=ACTIVE_TFS,
    )
    bot_views_builder = BotViewsMessageBuilder()

    admin_ids_raw = os.getenv("ADMIN_IDS", "")
    admin_ids = {x.strip() for x in admin_ids_raw.split(",") if x.strip()}

    stats_router = register_stats_handlers(
        stats_uc=stats_uc,
        notifier=None,
        builder=stats_builder,
        admin_ids=admin_ids,
    )

    dp.include_router(start_router)
    dp.include_router(menu_router)
    dp.include_router(settings_router)
    dp.include_router(stats_router)

    dp.workflow_data.update(
        {
            "pg_pool": pg_pool,
            "mongo_async": mongo,
            "telegram_users_repo": telegram_users_repo,
            "i18n_service": i18n_service,
            "bot_views_uc": bot_views_uc,
            "bot_views_builder": bot_views_builder,
        }
    )

    print("✅ Telegram bot started")
    print("✅ Postgres connected")
    print("✅ Async Mongo connected")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await pg_pool.close()
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(main())