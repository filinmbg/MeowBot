from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from meowbot.apps.telegram_bot.handlers_api_keys import register_api_keys_handlers
from meowbot.apps.telegram_bot.handlers_menu import router as menu_router
from meowbot.apps.telegram_bot.handlers_settings import router as settings_router
from meowbot.apps.telegram_bot.handlers_start import router as start_router
from meowbot.apps.telegram_bot.handlers.stats import register_stats_handlers
from meowbot.apps.telegram_bot.i18n.service import I18nService
from meowbot.core.services.exchange.binance_api_key_validation_service import (
    BinanceApiKeyValidationService,
)
from meowbot.core.services.exchange.user_api_keys_service import UserApiKeysService
from meowbot.core.services.notifications.admin_test_users_summary_message_builder import (
    AdminTestUsersSummaryMessageBuilder,
)
from meowbot.core.services.notifications.bot_views_message_builder import (
    BotViewsMessageBuilder,
)
from meowbot.core.services.notifications.risk_subscription_message_builder import (
    RiskSubscriptionMessageBuilder,
)
from meowbot.core.services.notifications.stats_message_builder import (
    StatsMessageBuilder,
)
from meowbot.core.services.security.fernet_crypto_service import (
    FernetCryptoService,
)
from meowbot.core.usecases.async_send_risk_events_to_telegram import (
    AsyncSendRiskEventsToTelegramUseCase,
)
from meowbot.core.usecases.expire_trials_and_notify import (
    ExpireTrialsAndNotifyUseCase,
)
from meowbot.core.usecases.get_bot_views_usecase import GetBotViewsUseCase
from meowbot.core.usecases.get_stats_usecase import GetStatsUseCase
from meowbot.core.usecases.register_telegram_user import (
    RegisterTelegramUserUseCase,
)
from meowbot.core.usecases.save_user_binance_api_keys import (
    SaveUserBinanceApiKeysUseCase,
)
from meowbot.core.usecases.send_admin_test_users_summary import (
    SendAdminTestUsersSummaryUseCase,
)
from meowbot.core.usecases.send_trial_expiry_warnings import (
    SendTrialExpiryWarningsUseCase,
)
from meowbot.infra.exchange.binance_futures_account_async import (
    BinanceFuturesAccountAsyncConfig,
)
from meowbot.infra.mongo.async_client import AsyncMongoConfig, AsyncMongoConn
from meowbot.infra.mongo.repos_async.admin_trade_stats_repo_async import (
    AdminTradeStatsRepositoryMongoAsync,
)
from meowbot.infra.mongo.repos_async.telegram_users_repo_async import (
    TelegramUsersRepositoryMongoAsync,
)
from meowbot.infra.mongo.repos_async.trade_events_repo_async import (
    TradeEventsRepositoryMongoAsync,
)
from meowbot.infra.mongo.repos_async.trades_repo_async import (
    TradesRepositoryMongoAsync,
)
from meowbot.infra.postgres.client import get_pg_pool
from meowbot.infra.postgres.repos.admin_test_users_repo import (
    AdminTestUsersRepo,
)
from meowbot.infra.postgres.repos.notification_preferences_repo import (
    NotificationPreferencesRepo,
)
from meowbot.infra.postgres.repos.subscription_plans_repo import (
    SubscriptionPlansRepo,
)
from meowbot.infra.postgres.repos.subscription_runtime_repo import (
    SubscriptionRuntimeRepo,
)
from meowbot.infra.postgres.repos.telegram_profiles_repo import (
    TelegramProfilesRepo,
)
from meowbot.infra.postgres.repos.telegram_user_account_repo import (
    TelegramUserAccountRepo,
)
from meowbot.infra.postgres.repos.trader_settings_repo import (
    TraderSettingsRepo,
)
from meowbot.infra.postgres.repos.trial_expiration_repo import (
    TrialExpirationRepo,
)
from meowbot.infra.postgres.repos.trial_notifications_repo import (
    TrialNotificationsRepo,
)
from meowbot.infra.postgres.repos.user_api_keys_repo import (
    UserApiKeysRepo,
)
from meowbot.infra.postgres.repos.user_subscriptions_repo import (
    UserSubscriptionsRepo,
)
from meowbot.infra.postgres.repos.users_repo import UsersRepo


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


async def _trial_warning_loop(
    *,
    usecase: SendTrialExpiryWarningsUseCase,
    interval_seconds: int = 300,
) -> None:
    while True:
        try:
            await usecase.execute()
        except Exception as exc:
            print(f"trial warning loop error: {type(exc).__name__}: {exc}")
        await asyncio.sleep(interval_seconds)


async def _trial_expire_loop(
    *,
    usecase: ExpireTrialsAndNotifyUseCase,
    interval_seconds: int = 300,
) -> None:
    while True:
        try:
            await usecase.execute()
        except Exception as exc:
            print(f"trial expire loop error: {type(exc).__name__}: {exc}")
        await asyncio.sleep(interval_seconds)


async def _risk_events_loop(
    *,
    usecase: AsyncSendRiskEventsToTelegramUseCase,
    interval_seconds: int = 10,
) -> None:
    while True:
        try:
            await usecase.run_once()
        except Exception as exc:
            print(f"risk events loop error: {type(exc).__name__}: {exc}")
        await asyncio.sleep(interval_seconds)


async def _admin_test_users_summary_loop(
    *,
    usecase: SendAdminTestUsersSummaryUseCase,
    interval_seconds: int = 10800,
) -> None:
    while True:
        try:
            await usecase.execute()
        except Exception as exc:
            print(f"admin summary loop error: {type(exc).__name__}: {exc}")
        await asyncio.sleep(interval_seconds)


async def main() -> None:
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not set in .env")

    pg_pool = await get_pg_pool()

    mongo = AsyncMongoConn(AsyncMongoConfig())
    await mongo.connect()

    bot = Bot(token=bot_token)
    dp = Dispatcher()

    # Mongo repos
    trades_repo = TradesRepositoryMongoAsync(mongo.db)
    trade_events_repo = TradeEventsRepositoryMongoAsync(mongo.db)
    telegram_users_repo = TelegramUsersRepositoryMongoAsync(mongo.db)
    admin_trade_stats_repo = AdminTradeStatsRepositoryMongoAsync(mongo.db)

    await trade_events_repo.ensure_indexes()

    # Shared services
    i18n_service = I18nService()

    # Postgres repos
    users_repo = UsersRepo(pg_pool)
    telegram_profiles_repo = TelegramProfilesRepo(pg_pool)
    trader_settings_repo = TraderSettingsRepo(pg_pool)
    notification_preferences_repo = NotificationPreferencesRepo(pg_pool)
    subscription_plans_repo = SubscriptionPlansRepo(pg_pool)
    user_subscriptions_repo = UserSubscriptionsRepo(pg_pool)

    telegram_user_account_repo = TelegramUserAccountRepo(pg_pool)
    user_api_keys_repo = UserApiKeysRepo(pg_pool)
    trial_notifications_repo = TrialNotificationsRepo(pg_pool)
    trial_expiration_repo = TrialExpirationRepo(pg_pool)
    runtime_repo = SubscriptionRuntimeRepo(pg_pool)
    admin_test_users_repo = AdminTestUsersRepo(pg_pool)

    # Usecase: create/register user in Postgres on /start
    register_user_usecase = RegisterTelegramUserUseCase(
        users_repo=users_repo,
        telegram_profiles_repo=telegram_profiles_repo,
        trader_settings_repo=trader_settings_repo,
        notification_preferences_repo=notification_preferences_repo,
        subscription_plans_repo=subscription_plans_repo,
        user_subscriptions_repo=user_subscriptions_repo,
    )

    # API keys flow
    crypto_service = FernetCryptoService.from_env("MEOWBOT_SECRETS_FERNET_KEY")
    binance_validation_service = BinanceApiKeyValidationService(
        account_config=BinanceFuturesAccountAsyncConfig(
            futures_base_url=os.getenv("BINANCE_FUTURES_BASE_URL", "https://fapi.binance.com"),
            wallet_base_url=os.getenv("BINANCE_WALLET_BASE_URL", "https://api.binance.com"),
            timeout_seconds=float(os.getenv("BINANCE_HTTP_TIMEOUT_SECONDS", "15")),
            max_weight_per_minute=int(os.getenv("BINANCE_MAX_WEIGHT_PER_MINUTE", "600")),
            max_concurrent_requests=int(os.getenv("BINANCE_MAX_CONCURRENT_REQUESTS", "2")),
            retry_attempts=int(os.getenv("BINANCE_HTTP_RETRY_ATTEMPTS", "5")),
            retry_base_delay_seconds=float(os.getenv("BINANCE_HTTP_RETRY_BASE_DELAY_SECONDS", "1.0")),
            retry_max_delay_seconds=float(os.getenv("BINANCE_HTTP_RETRY_MAX_DELAY_SECONDS", "15.0")),
            recv_window_ms=int(os.getenv("BINANCE_RECV_WINDOW_MS", "5000")),
        ),
        require_reading=True,
        require_futures=True,
        require_withdrawals_disabled=True,
    )
    user_api_keys_service = UserApiKeysService(
        user_api_keys_repo=user_api_keys_repo,
        crypto_service=crypto_service,
        validation_service=binance_validation_service,
    )
    save_user_binance_api_keys_uc = SaveUserBinanceApiKeysUseCase(
        telegram_user_account_repo=telegram_user_account_repo,
        user_api_keys_service=user_api_keys_service,
    )

    # Stats / views
    stats_uc = GetStatsUseCase(trades_repo)
    stats_builder = StatsMessageBuilder()

    # Поки залишаю bot views на Mongo, щоб не ламати стару логіку статистики/екранів
    bot_views_uc = GetBotViewsUseCase(
        trades_repo=trades_repo,
        telegram_users_repo=telegram_users_repo,
        trading_user_id=os.getenv("TRADING_OWNER_USER_ID", "demo_user"),
        sandbox_start_balance_usd=float(os.getenv("SANDBOX_START_BALANCE_USD", "1000")),
        active_symbols=ACTIVE_SYMBOLS,
        active_tfs=ACTIVE_TFS,
    )
    bot_views_builder = BotViewsMessageBuilder()

    # Admin settings
    admin_ids_raw = os.getenv("ADMIN_IDS", "")
    admin_ids = {x.strip() for x in admin_ids_raw.split(",") if x.strip()}

    admin_chat_ids_raw = os.getenv("ADMIN_CHAT_IDS", "")
    admin_chat_ids = [int(x.strip()) for x in admin_chat_ids_raw.split(",") if x.strip()]

    test_user_emails_raw = os.getenv("TEST_USER_EMAILS", "")
    test_user_emails = [x.strip() for x in test_user_emails_raw.split(",") if x.strip()]

    # Routers
    stats_router = register_stats_handlers(
        stats_uc=stats_uc,
        notifier=None,
        builder=stats_builder,
        admin_ids=admin_ids,
    )

    api_keys_router = register_api_keys_handlers(
        save_user_binance_api_keys_uc=save_user_binance_api_keys_uc,
        user_api_keys_repo=user_api_keys_repo,
        telegram_user_account_repo=telegram_user_account_repo,
    )

    dp.include_router(start_router)
    dp.include_router(menu_router)
    dp.include_router(settings_router)
    dp.include_router(stats_router)
    dp.include_router(api_keys_router)

    # Workflow data for handlers
    dp.workflow_data.update(
        {
            "pg_pool": pg_pool,
            "mongo_async": mongo,
            "telegram_users_repo": telegram_users_repo,  # залишаю тимчасово для старих частин UI
            "i18n_service": i18n_service,
            "bot_views_uc": bot_views_uc,
            "bot_views_builder": bot_views_builder,

            # New Postgres onboarding/runtime deps
            "register_user_usecase": register_user_usecase,
            "users_repo": users_repo,
            "telegram_profiles_repo": telegram_profiles_repo,
        }
    )

    # Background usecases
    trial_warning_uc = SendTrialExpiryWarningsUseCase(
        trial_notifications_repo=trial_notifications_repo,
        bot=bot,
        admin_chat_ids=admin_chat_ids,
        hours_before_end=6,
    )

    trial_expire_uc = ExpireTrialsAndNotifyUseCase(
        trial_expiration_repo=trial_expiration_repo,
        bot=bot,
        admin_chat_ids=admin_chat_ids,
    )

    risk_builder = RiskSubscriptionMessageBuilder()
    risk_events_uc = AsyncSendRiskEventsToTelegramUseCase(
        trade_events_repo=trade_events_repo,
        runtime_repo=runtime_repo,
        bot=bot,
        builder=risk_builder,
        admin_chat_ids=admin_chat_ids,
    )

    admin_summary_builder = AdminTestUsersSummaryMessageBuilder()
    admin_test_users_summary_uc = SendAdminTestUsersSummaryUseCase(
        admin_test_users_repo=admin_test_users_repo,
        admin_trade_stats_repo=admin_trade_stats_repo,
        bot=bot,
        message_builder=admin_summary_builder,
        admin_chat_ids=admin_chat_ids,
        test_user_emails=test_user_emails,
        sandbox_start_balance_usd=float(os.getenv("SANDBOX_START_BALANCE_USD", "1000")),
        risk_period_hours=3,
    )

    print("✅ Telegram bot started")
    print("✅ Postgres connected")
    print("✅ Async Mongo connected")

    trial_warning_task = asyncio.create_task(
        _trial_warning_loop(
            usecase=trial_warning_uc,
            interval_seconds=300,
        )
    )

    trial_expire_task = asyncio.create_task(
        _trial_expire_loop(
            usecase=trial_expire_uc,
            interval_seconds=300,
        )
    )

    risk_events_task = asyncio.create_task(
        _risk_events_loop(
            usecase=risk_events_uc,
            interval_seconds=10,
        )
    )

    admin_summary_task = asyncio.create_task(
        _admin_test_users_summary_loop(
            usecase=admin_test_users_summary_uc,
            interval_seconds=10800,
        )
    )

    try:
        await dp.start_polling(bot)
    finally:
        trial_warning_task.cancel()
        trial_expire_task.cancel()
        risk_events_task.cancel()
        admin_summary_task.cancel()

        with contextlib.suppress(Exception):
            await trial_warning_task
        with contextlib.suppress(Exception):
            await trial_expire_task
        with contextlib.suppress(Exception):
            await risk_events_task
        with contextlib.suppress(Exception):
            await admin_summary_task

        await bot.session.close()
        await pg_pool.close()
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(main())