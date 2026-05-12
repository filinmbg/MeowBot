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
from meowbot.infra.mongo.migrations import apply_migrations_async
from meowbot.infra.mongo.repos_async.trade_events_repo_async import (
    TradeEventsRepositoryMongoAsync,
)
from meowbot.infra.mongo.repos_async.trades_repo_async import TradesRepositoryMongoAsync
from meowbot.infra.postgres.client import get_pg_pool
from meowbot.infra.postgres.repos.subscription_runtime_repo import (
    SubscriptionRuntimeRepo,
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

    pg_pool = await get_pg_pool()
    mongo = AsyncMongoConn(
        AsyncMongoConfig(
            max_pool_size=int(os.getenv("MONGO_MAX_POOL_SIZE", "10")),
            min_pool_size=int(os.getenv("MONGO_MIN_POOL_SIZE", "1")),
            max_idle_time_ms=int(os.getenv("MONGO_MAX_IDLE_TIME_MS", "60000")),
            wait_queue_timeout_ms=int(os.getenv("MONGO_WAIT_QUEUE_TIMEOUT_MS", "5000")),
            connect_timeout_ms=int(os.getenv("MONGO_CONNECT_TIMEOUT_MS", "20000")),
            socket_timeout_ms=int(os.getenv("MONGO_SOCKET_TIMEOUT_MS", "30000")),
            server_selection_timeout_ms=int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "20000")),
            retry_reads=os.getenv("MONGO_RETRY_READS", "true").lower() == "true",
            retry_writes=os.getenv("MONGO_RETRY_WRITES", "true").lower() == "true",
            app_name=os.getenv("MONGO_APP_NAME", "MeowBot-telegram-event-worker"),
        )
    )
    await mongo.connect()
    await apply_migrations_async(
        mongo.db,
        timeframes=("1m", "15m", "30m", "1h", "2h", "4h", "1d"),
    )

    bot = Bot(token=bot_token)

    trade_events_repo = TradeEventsRepositoryMongoAsync(mongo.db)
    trades_repo = TradesRepositoryMongoAsync(mongo.db)
    notification_targets_repo = SubscriptionRuntimeRepo(pg_pool)
    message_builder = TradeEventMessageBuilder()

    usecase = AsyncSendTradeEventsToTelegramUseCase(
        trade_events_repo=trade_events_repo,
        telegram_users_repo=notification_targets_repo,
        bot=bot,
        message_builder=message_builder,
        admin_chat_id=admin_chat_id,
        trades_repo=trades_repo,
        max_send_concurrency=int(os.getenv("TELEGRAM_SEND_CONCURRENCY", "5")),
        stuck_after_seconds=float(os.getenv("TELEGRAM_OPEN_NOTIFICATION_STUCK_SECONDS", "30")),
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
        await pg_pool.close()
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(main())


