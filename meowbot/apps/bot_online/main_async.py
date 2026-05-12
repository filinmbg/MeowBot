from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from meowbot.apps.bot_online.async_runtime import (
    AsyncBotOnlineRuntime,
    MarketEntryProcessResult,
)
from meowbot.core.services.execution.live_trade_sync_service import LiveTradeSyncService
from meowbot.core.services.exchange.per_user_binance_account_provider import (
    PerUserBinanceAccountProvider,
)
from meowbot.core.services.admin_alert_service import AdminAlertService
from meowbot.core.services.mongo_write_queue import (
    MongoWriteQueue,
    QueueBackedBarsPersistenceAsync,
    QueueBackedBotStatePersistenceAsync,
    QueueBackedTradeEventsRepositoryAsync,
    QueueBackedTradesRepositoryAsync,
)
from meowbot.core.services.risk.per_user_live_risk_service import PerUserLiveRiskService
from meowbot.core.services.runtime.symbol_rollout_manager import SymbolRolloutManager
from meowbot.core.services.runtime.binance_symbol_ranker import BinanceFuturesSymbolRanker
from meowbot.core.services.runtime.active_trades_cache import ActiveTradesCache
from meowbot.core.services.runtime.bars_cache import BarsCache, CacheBackedBarsRepositoryAsync
from meowbot.core.services.runtime.bot_state_cache import BotStateCache
from meowbot.core.services.runtime.symbol_validator import (
    DEFAULT_SYMBOL_BLACKLIST,
    filter_valid_binance_usdt_symbols,
)
from meowbot.core.services.notifications.telegram_notifier_async import TelegramNotifierAsync
from meowbot.core.services.scheduling.bar_close_scheduler import (
    BarCloseScheduler,
    SchedulerConfig,
)
from meowbot.core.usecases.async_ensure_market_data import AsyncEnsureMarketDataUseCase
from meowbot.core.usecases.async_online_entry_cycle import AsyncOnlineEntryCycleUseCase
from meowbot.core.usecases.async_reconcile_open_trades import AsyncReconcileOpenTradesUseCase
from meowbot.core.usecases.startup_reconcile_missed_exits import (
    StartupReconcileMissedExitsUseCase,
)
from meowbot.infra.broker.binance_live_async import BinanceLiveBrokerAsync
from meowbot.infra.broker.mode_aware import ModeAwareBroker
from meowbot.infra.broker.paper import PaperBroker
from meowbot.infra.exchange.binance_futures_account_async import (
    BinanceFuturesAccountAsyncConfig,
)
from meowbot.infra.exchange.binance_futures_usdtm_async import (
    BinanceFuturesAsyncConfig,
    BinanceFuturesMarketDataAsync,
)
from meowbot.infra.exchange.binance_futures_ws_prices import BinanceFuturesWsPrices
from meowbot.infra.exchange.binance_user_data_stream import (
    BinanceUserDataStreamConfig,
    BinanceUserDataStreamSupervisor,
)
from meowbot.infra.mongo.async_client import AsyncMongoConfig, AsyncMongoConn
from meowbot.infra.mongo.migrations import apply_migrations_async
from meowbot.infra.mongo.repos_async.bars_repo_async import BarsRepositoryMongoAsync
from meowbot.infra.mongo.repos_async.bot_state_repo_async import BotStateRepositoryMongoAsync
from meowbot.infra.mongo.repos_async.trade_events_repo_async import (
    TradeEventsRepositoryMongoAsync,
)
from meowbot.infra.mongo.repos_async.trades_repo_async import TradesRepositoryMongoAsync
from meowbot.infra.postgres.client import get_pg_pool
from meowbot.infra.postgres.repos.subscription_runtime_repo import SubscriptionRuntimeRepo
from meowbot.infra.postgres.repos.trade_entry_cooldowns_repo import (
    TradeEntryCooldownsRepo,
)
from meowbot.infra.postgres.repos.user_api_keys_repo import UserApiKeysRepo
from meowbot.core.services.security.fernet_crypto_service import FernetCryptoService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("meowbot").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
log = logging.getLogger("meowbot")


ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")


CORE_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "TRXUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "LTCUSDT",
    "BCHUSDT",
    "DOTUSDT",
    "UNIUSDT",
    "ATOMUSDT",
    "NEARUSDT",
    "APTUSDT",
    "ETCUSDT",
    "FILUSDT",
    "ARBUSDT",
]

TFS = ["15m", "30m", "1h", "2h", "4h"]
FEATURES_VER = "v2_core"

ROLLOUT_ENABLED = os.getenv("SYMBOL_ROLLOUT_ENABLED", "true").lower() == "true"
ROLLOUT_BASE_COUNT = int(os.getenv("SYMBOL_ROLLOUT_BASE_COUNT", "5"))
ROLLOUT_STEP = int(os.getenv("SYMBOL_ROLLOUT_STEP", "5"))
ROLLOUT_MAX_COUNT = int(os.getenv("SYMBOL_ROLLOUT_MAX_COUNT", "200"))
ROLLOUT_GROW_INTERVAL_SECONDS = int(os.getenv("SYMBOL_ROLLOUT_GROW_INTERVAL_SECONDS", "900"))
MARKET_ENTRY_MAX_CONCURRENCY = int(os.getenv("MARKET_ENTRY_MAX_CONCURRENCY", "50"))
SIGNAL_MAX_CONCURRENCY_15M = int(os.getenv("SIGNAL_MAX_CONCURRENCY_15M", "60"))
SIGNAL_MAX_CONCURRENCY_OTHER = int(os.getenv("SIGNAL_MAX_CONCURRENCY_OTHER", "20"))
SIGNAL_MAX_CONCURRENCY_HARD_LIMIT = int(os.getenv("SIGNAL_MAX_CONCURRENCY_HARD_LIMIT", "100"))
ADMIN_SIGNAL_SCAN_REPORT_MIN_INTERVAL_SECONDS = float(
    os.getenv("ADMIN_SIGNAL_SCAN_REPORT_MIN_INTERVAL_SECONDS", "300")
)
DEBUG_EXIT_REPLAY = os.getenv("DEBUG_EXIT_REPLAY", "false").lower() in {"1", "true", "yes", "y", "on"}

STARTUP_PING_MAX_ATTEMPTS = int(os.getenv("BINANCE_STARTUP_PING_MAX_ATTEMPTS", "30"))
STARTUP_PING_SLEEP_SECONDS = float(os.getenv("BINANCE_STARTUP_PING_SLEEP_SECONDS", "10"))

STALE_LAST_CLOSED_MAX_AGE_MS = int(
    os.getenv("STALE_LAST_CLOSED_MAX_AGE_MS", str(3 * 24 * 60 * 60 * 1000))
)

SYMBOL_MIN_QUOTE_VOLUME = float(os.getenv("SYMBOL_MIN_QUOTE_VOLUME", "10000000"))
SYMBOL_MIN_LAST_PRICE = float(os.getenv("SYMBOL_MIN_LAST_PRICE", "0.0005"))
SYMBOL_MAX_ABS_PRICE_CHANGE_PERCENT = float(os.getenv("SYMBOL_MAX_ABS_PRICE_CHANGE_PERCENT", "25"))
SYMBOL_BLACKLIST = [
    symbol.strip().upper()
    for symbol in [*DEFAULT_SYMBOL_BLACKLIST, *os.getenv("SYMBOL_BLACKLIST", "").split(",")]
    if symbol.strip()
]
SYMBOL_RANK_CACHE_PATH = os.getenv(
    "SYMBOL_RANK_CACHE_PATH",
    str(ROOT / "var" / "symbol_rank_cache.json"),
)
USE_DYNAMIC_SYMBOL_SELECTOR = os.getenv("USE_DYNAMIC_SYMBOL_SELECTOR", "true").lower() == "true"


async def wait_for_binance_ready(exchange: BinanceFuturesMarketDataAsync) -> None:
    for attempt in range(1, STARTUP_PING_MAX_ATTEMPTS + 1):
        ok = await exchange.ping()
        if ok:
            log.info("[startup] binance ready on attempt=%s", attempt)
            return

        status = exchange.get_gate_status()
        blocked_for = float(status.get("blocked_for_seconds", 0.0) or 0.0)
        sleep_for = max(STARTUP_PING_SLEEP_SECONDS, blocked_for)

        log.warning(
            "[startup] binance not ready attempt=%s/%s blocked=%s blocked_for=%.2fs last_error=%s sleep=%.2fs",
            attempt,
            STARTUP_PING_MAX_ATTEMPTS,
            status.get("blocked"),
            blocked_for,
            status.get("last_error"),
            sleep_for,
        )
        await asyncio.sleep(sleep_for)

    raise RuntimeError("Binance REST is not ready after startup wait loop")


async def load_all_symbols(exchange: BinanceFuturesMarketDataAsync) -> list[str]:
    env_symbols_raw = os.getenv("SYMBOLS", "").strip() or os.getenv("ACTIVE_SYMBOLS", "").strip()
    raw_env_symbols = [x.strip().upper() for x in env_symbols_raw.split(",") if x.strip()] if env_symbols_raw else []
    env_symbols, invalid_env_symbols = filter_valid_binance_usdt_symbols(
        raw_env_symbols,
        blacklist=SYMBOL_BLACKLIST,
    )
    if invalid_env_symbols:
        log.warning(
            "[symbols] invalid env symbols skipped count=%s examples=%s",
            len(invalid_env_symbols),
            [
                f"{item.normalized_symbol or item.symbol}:{item.reason}"
                for item in invalid_env_symbols[:10]
            ],
        )

    if raw_env_symbols:
        log.warning(
            "[symbols] WARNING env_override detected len(SYMBOLS)=%s valid=%s invalid=%s use_dynamic_symbol_selector=%s",
            len(raw_env_symbols),
            len(env_symbols),
            len(invalid_env_symbols),
            USE_DYNAMIC_SYMBOL_SELECTOR,
        )

    if not USE_DYNAMIC_SYMBOL_SELECTOR:
        if env_symbols:
            log.info(
                "[symbols] source=env_override selected_count=%s rollout_max=%s first_20_symbols=%s",
                len(env_symbols),
                min(ROLLOUT_MAX_COUNT, len(env_symbols)),
                env_symbols[:20],
            )
            return env_symbols

        fallback_symbols, invalid_fallback_symbols = filter_valid_binance_usdt_symbols(
            [symbol for symbol in CORE_SYMBOLS if symbol not in SYMBOL_BLACKLIST],
            blacklist=SYMBOL_BLACKLIST,
        )
        log.warning(
            "[symbols] source=static_fallback selected_count=%s invalid_skipped=%s rollout_max=%s first_20_symbols=%s reason=no_env_override_and_dynamic_disabled",
            len(fallback_symbols),
            len(invalid_fallback_symbols),
            min(ROLLOUT_MAX_COUNT, len(fallback_symbols)),
            fallback_symbols[:20],
        )
        return fallback_symbols

    ranker = BinanceFuturesSymbolRanker(
        exchange=exchange,
        core_symbols=CORE_SYMBOLS,
        blacklist=SYMBOL_BLACKLIST,
        cache_path=SYMBOL_RANK_CACHE_PATH,
        min_quote_volume=SYMBOL_MIN_QUOTE_VOLUME,
        min_last_price=SYMBOL_MIN_LAST_PRICE,
        max_abs_price_change_percent=SYMBOL_MAX_ABS_PRICE_CHANGE_PERCENT,
        max_symbols=max(ROLLOUT_MAX_COUNT, 200),
    )
    result = await ranker.load_ranked_symbols()
    diagnostics = result.diagnostics
    if diagnostics.source == "static_fallback" and env_symbols:
        log.warning(
            "[symbols] dynamic selector failed; falling back to env_override selected_count=%s invalid_skipped=%s rollout_max=%s first_20_symbols=%s",
            len(env_symbols),
            len(invalid_env_symbols),
            min(ROLLOUT_MAX_COUNT, len(env_symbols)),
            env_symbols[:20],
        )
        return env_symbols
    log.info(
        "[symbols] source=%s all_symbols_count=%s after_base_filter_count=%s after_quality_filter_count=%s selected_count=%s invalid_symbols_skipped=%s rollout_max=%s first_20_symbols=%s excluded_examples=%s",
        "dynamic_selector" if diagnostics.source == "binance_api" else diagnostics.source,
        diagnostics.all_symbols_count,
        diagnostics.after_base_filter_count,
        diagnostics.after_quality_filter_count,
        diagnostics.selected_count,
        diagnostics.invalid_symbols_skipped,
        min(ROLLOUT_MAX_COUNT, len(result.symbols)),
        diagnostics.first_20_symbols,
        diagnostics.excluded_examples[:10],
    )
    return result.symbols


async def close_sandbox_trades_for_live_users(
    *,
    runtime_repo: SubscriptionRuntimeRepo,
    trades_repo: TradesRepositoryMongoAsync,
) -> int:
    live_user_ids = await runtime_repo.list_live_trading_user_ids()
    if not live_user_ids:
        log.info("[startup] sandbox cleanup for live users skipped: no live users")
        return 0

    now_ms = int(time.time() * 1000)
    total_closed = 0
    for trading_user_id in live_user_ids:
        closed = await trades_repo.close_open_trades_by_user_mode(
            user_id=trading_user_id,
            mode="sandbox",
            closed_at=now_ms,
            reason="SWITCH_TO_LIVE",
        )
        total_closed += closed
        if closed:
            log.info(
                "[startup] closed sandbox trades for live user user_id=%s count=%s",
                trading_user_id,
                closed,
            )

    log.info(
        "[startup] sandbox cleanup for live users done users=%s closed=%s",
        len(live_user_ids),
        total_closed,
    )
    return total_closed


def is_stale_last_closed(last_closed_time_ms: int | None, now_ms: int) -> bool:
    if last_closed_time_ms is None:
        return True
    return (now_ms - int(last_closed_time_ms)) > STALE_LAST_CLOSED_MAX_AGE_MS


async def main() -> None:
    pg_pool = await get_pg_pool()

    mongo = AsyncMongoConn(
        AsyncMongoConfig(
            max_pool_size=int(os.getenv("MONGO_MAX_POOL_SIZE", "30")),
            min_pool_size=int(os.getenv("MONGO_MIN_POOL_SIZE", "2")),
            max_idle_time_ms=int(os.getenv("MONGO_MAX_IDLE_TIME_MS", "60000")),
            wait_queue_timeout_ms=int(os.getenv("MONGO_WAIT_QUEUE_TIMEOUT_MS", "5000")),
            connect_timeout_ms=int(os.getenv("MONGO_CONNECT_TIMEOUT_MS", "20000")),
            socket_timeout_ms=int(os.getenv("MONGO_SOCKET_TIMEOUT_MS", "30000")),
            server_selection_timeout_ms=int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "20000")),
            retry_reads=os.getenv("MONGO_RETRY_READS", "true").lower() == "true",
            retry_writes=os.getenv("MONGO_RETRY_WRITES", "true").lower() == "true",
            app_name=os.getenv("MONGO_APP_NAME", "MeowBot-online"),
        )
    )
    await mongo.connect()

    await apply_migrations_async(
        mongo.db,
        timeframes=("1m", "15m", "30m", "1h", "2h", "4h", "1d"),
    )

    mongo_bars_repo = BarsRepositoryMongoAsync(mongo.db)
    mongo_trades_repo = TradesRepositoryMongoAsync(mongo.db)
    mongo_bot_state_repo = BotStateRepositoryMongoAsync(mongo.db)
    mongo_trade_events_repo = TradeEventsRepositoryMongoAsync(mongo.db)
    duplicate_cleanup = await mongo_trades_repo.cleanup_duplicate_open_trades()
    placeholder_closed = await mongo_trades_repo.suspend_placeholder_open_trades()
    if duplicate_cleanup or placeholder_closed:
        log.warning(
            "[startup] open trade cleanup done duplicate_groups=%s placeholder_closed=%s",
            len(duplicate_cleanup),
            placeholder_closed,
        )
    await mongo_trades_repo.ensure_indexes()
    await mongo_trade_events_repo.ensure_indexes()

    admin_alert_service = AdminAlertService(
        notifier=TelegramNotifierAsync(
            bot_token=os.getenv("BOT_TOKEN"),
            default_chat_id=os.getenv("ADMIN_CHAT_ID"),
            timeout_seconds=float(os.getenv("ADMIN_ALERT_TIMEOUT_SECONDS", "10")),
        ),
        throttle_seconds=float(os.getenv("ADMIN_ALERT_THROTTLE_SECONDS", "600")),
    )
    mongo_write_queue = MongoWriteQueue(
        bars_repo=mongo_bars_repo,
        bot_state_repo=mongo_bot_state_repo,
        trades_repo=mongo_trades_repo,
        trade_events_repo=mongo_trade_events_repo,
        admin_alert_service=admin_alert_service,
        max_queue_size=int(os.getenv("MONGO_WRITE_QUEUE_MAX_SIZE", "5000")),
        high_watermark=int(os.getenv("MONGO_WRITE_QUEUE_HIGH_WATERMARK", "4000")),
        flush_interval_seconds=float(os.getenv("MONGO_WRITE_QUEUE_FLUSH_INTERVAL_SECONDS", "2")),
        health_interval_seconds=float(os.getenv("MONGO_WRITE_QUEUE_HEALTH_INTERVAL_SECONDS", "30")),
        worker_count=int(os.getenv("MONGO_WRITE_QUEUE_WORKERS", "3")),
        mongo_unavailable_alert_seconds=float(os.getenv("MONGO_WRITE_QUEUE_UNAVAILABLE_ALERT_SECONDS", "60")),
        critical_oldest_age_alert_seconds=float(os.getenv("MONGO_WRITE_QUEUE_OLDEST_AGE_ALERT_SECONDS", "60")),
        critical_queue_alert_threshold=int(os.getenv("MONGO_WRITE_QUEUE_CRITICAL_THRESHOLD", "100")),
    )
    await mongo_write_queue.start()

    bars_cache = BarsCache(maxlen=int(os.getenv("BARS_CACHE_MAXLEN", "500")))
    bars_repo = CacheBackedBarsRepositoryAsync(
        cache=bars_cache,
        persistence_repo=QueueBackedBarsPersistenceAsync(queue=mongo_write_queue),
        persist_writes=os.getenv("BARS_CACHE_PERSIST_MONGO", "true").lower() == "true",
        max_pending_persist_tasks=1,
    )
    trades_repo = QueueBackedTradesRepositoryAsync(
        persistence_repo=mongo_trades_repo,
        queue=mongo_write_queue,
    )
    bot_state_cache = BotStateCache()
    bot_state_repo = QueueBackedBotStatePersistenceAsync(
        cache=bot_state_cache,
        persistence_repo=mongo_bot_state_repo,
        queue=mongo_write_queue,
    )
    await bot_state_repo.preload_from_persistence()
    trade_events_repo = QueueBackedTradeEventsRepositoryAsync(
        persistence_repo=mongo_trade_events_repo,
        queue=mongo_write_queue,
    )

    runtime_repo = SubscriptionRuntimeRepo(pg_pool)
    cooldowns_repo = TradeEntryCooldownsRepo(pg_pool)
    user_api_keys_repo = UserApiKeysRepo(pg_pool)
    crypto_service = FernetCryptoService.from_env("MEOWBOT_SECRETS_FERNET_KEY")

    await close_sandbox_trades_for_live_users(
        runtime_repo=runtime_repo,
        trades_repo=trades_repo,
    )

    active_trades_cache = ActiveTradesCache()
    try:
        startup_open_trades = await mongo_trades_repo.get_open_trades()
    except Exception:
        log.exception("[startup] failed to load open trades into memory cache; continuing with empty cache")
        startup_open_trades = []
    active_trades_cache.load_open_trades(startup_open_trades, source="mongo_startup")
    log.info(
        "[runtime] mongo skipped in hot path: bars=memory bot_state=memory trades=memory mongo=persistence_only"
    )

    account_config = BinanceFuturesAccountAsyncConfig(
        futures_base_url=os.getenv("BINANCE_FUTURES_BASE_URL", "https://fapi.binance.com"),
        wallet_base_url=os.getenv("BINANCE_WALLET_BASE_URL", "https://api.binance.com"),
        timeout_seconds=float(os.getenv("BINANCE_HTTP_TIMEOUT_SECONDS", "15")),
        max_weight_per_minute=int(os.getenv("BINANCE_MAX_WEIGHT_PER_MINUTE", "600")),
        max_concurrent_requests=int(os.getenv("BINANCE_MAX_CONCURRENT_REQUESTS", "2")),
        retry_attempts=int(os.getenv("BINANCE_HTTP_RETRY_ATTEMPTS", "5")),
        retry_base_delay_seconds=float(os.getenv("BINANCE_HTTP_RETRY_BASE_DELAY_SECONDS", "1.0")),
        retry_max_delay_seconds=float(os.getenv("BINANCE_HTTP_RETRY_MAX_DELAY_SECONDS", "15.0")),
        recv_window_ms=int(os.getenv("BINANCE_RECV_WINDOW_MS", "10000")),
        signed_request_safety_margin_ms=int(os.getenv("BINANCE_SIGNED_REQUEST_SAFETY_MARGIN_MS", "1500")),
    )

    exchange = BinanceFuturesMarketDataAsync(BinanceFuturesAsyncConfig())
    await wait_for_binance_ready(exchange)
    all_symbols = await load_all_symbols(exchange)
    if not all_symbols:
        all_symbols, invalid_fallback_symbols = filter_valid_binance_usdt_symbols(
            [symbol for symbol in CORE_SYMBOLS if symbol not in SYMBOL_BLACKLIST],
            blacklist=SYMBOL_BLACKLIST,
        )
        log.warning(
            "[symbols] ranked list was empty; using fallback core list count=%s invalid_skipped=%s",
            len(all_symbols),
            len(invalid_fallback_symbols),
        )

    per_user_live_risk_service = PerUserLiveRiskService(
        user_api_keys_repo=user_api_keys_repo,
        crypto_service=crypto_service,
        account_config=account_config,
    )

    account_provider = PerUserBinanceAccountProvider(
        user_api_keys_repo=user_api_keys_repo,
        crypto_service=crypto_service,
        account_config=account_config,
    )
    sandbox_broker = PaperBroker()
    live_entry_broker = BinanceLiveBrokerAsync(account_provider=account_provider)
    broker = ModeAwareBroker(
        sandbox_broker=sandbox_broker,
        live_entry_broker=live_entry_broker,
    )
    live_sync_service = LiveTradeSyncService(
        trades_repo=trades_repo,
        trade_events_repo=trade_events_repo,
        account_provider=account_provider,
        live_broker=live_entry_broker,
        active_trades_cache=active_trades_cache,
        live_poll_interval_ms=int(os.getenv("LIVE_BACKUP_POLL_INTERVAL_MS", "15000")),
    )

    rollout = SymbolRolloutManager(
        all_symbols=all_symbols,
        base_count=ROLLOUT_BASE_COUNT,
        step=ROLLOUT_STEP,
        max_count=min(ROLLOUT_MAX_COUNT, len(all_symbols)),
        grow_interval_seconds=ROLLOUT_GROW_INTERVAL_SECONDS,
    )

    initial_symbols = rollout.get_active_symbols() if ROLLOUT_ENABLED else all_symbols

    log.info(
        "[main-async] all_symbols=%s rollout_enabled=%s base=%s step=%s max=%s grow_interval_seconds=%s active_symbols_count=%s",
        len(all_symbols),
        ROLLOUT_ENABLED,
        ROLLOUT_BASE_COUNT,
        ROLLOUT_STEP,
        min(ROLLOUT_MAX_COUNT, len(all_symbols)),
        ROLLOUT_GROW_INTERVAL_SECONDS,
        len(initial_symbols),
    )
    log.info("[main-async] initial active symbols=%s symbols=%s", len(initial_symbols), initial_symbols[:20])

    ws_prices = BinanceFuturesWsPrices(
        symbols_provider=rollout.get_active_symbols if ROLLOUT_ENABLED else (lambda: all_symbols),
        use_mark_price_stream=True,
    )

    market_data_uc = AsyncEnsureMarketDataUseCase(
        bars_repo=bars_repo,
        exchange=exchange,
        features_ver=FEATURES_VER,
        startup_history_bars=500,
        runtime_window_bars=300,
    )

    entry_uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=bars_repo,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        telegram_users_repo=runtime_repo,
        broker=broker,
        exchange=exchange,
        features_ver=FEATURES_VER,
        max_entry_price_deviation_pct=0.5,
        sandbox_start_balance_usd=float(os.getenv("SANDBOX_START_BALANCE_USD", "1000")),
        cooldowns_repo=cooldowns_repo,
        per_user_live_risk_service=per_user_live_risk_service,
        active_trades_cache=active_trades_cache,
    )

    exit_uc = AsyncReconcileOpenTradesUseCase(
        trades_repo=trades_repo,
        broker=broker,
        trade_events_repo=trade_events_repo,
        price_provider=ws_prices,
        max_concurrency=20,
        cooldowns_repo=cooldowns_repo,
        telegram_users_repo=runtime_repo,
        live_sync_service=live_sync_service,
        exchange=exchange,
        bars_repo=bars_repo,
        active_trades_cache=active_trades_cache,
        admin_alert_service=admin_alert_service,
        reconcile_tf="1m",
        features_ver=FEATURES_VER,
        max_replay_trades_per_tick=int(os.getenv("EXIT_REPLAY_MAX_TRADES_PER_TICK", "20")),
    )

    startup_reconcile_uc = StartupReconcileMissedExitsUseCase(
        trades_repo=trades_repo,
        exit_usecase=exit_uc,
        exchange=exchange,
        max_bars_per_request=1000,
        reconcile_tf="1m",
        live_sync_service=live_sync_service,
        active_trades_cache=active_trades_cache,
    )

    user_stream_supervisor = BinanceUserDataStreamSupervisor(
        runtime_repo=runtime_repo,
        trades_repo=trades_repo,
        account_provider=account_provider,
        live_sync_service=live_sync_service,
        active_trades_cache=active_trades_cache,
        config=BinanceUserDataStreamConfig(
            ws_base_url=os.getenv("BINANCE_USER_STREAM_WS_BASE_URL", "wss://fstream.binance.com/ws"),
            refresh_users_interval_seconds=float(os.getenv("BINANCE_USER_STREAM_REFRESH_SECONDS", "15")),
            reconnect_delay_seconds=float(os.getenv("BINANCE_USER_STREAM_RECONNECT_SECONDS", "5")),
            keepalive_interval_seconds=float(os.getenv("BINANCE_USER_STREAM_KEEPALIVE_SECONDS", str(25 * 60))),
            message_timeout_seconds=float(os.getenv("BINANCE_USER_STREAM_MESSAGE_TIMEOUT_SECONDS", "15")),
        ),
    )

    async def bootstrap_warmup() -> None:
        log.info("[main-async] warmup start active_symbols=%s", len(initial_symbols))
        for symbol in initial_symbols:
            for tf in TFS:
                try:
                    last_close = await market_data_uc.warmup(symbol, tf)
                    log.info(
                        "[main-async] warmup %s %s last_close=%s",
                        symbol,
                        tf,
                        last_close,
                    )
                except Exception:
                    log.exception("[main-async] warmup failed for %s %s", symbol, tf)
        log.info("[main-async] warmup done")

    async def last_closed_provider(symbol: str, tf: str) -> int | None:
        last_closed = await exchange.get_last_closed_time(symbol, tf)
        now_ms = int(time.time() * 1000)

        if is_stale_last_closed(last_closed, now_ms):
            log.warning(
                "[startup] stale last_closed detected symbol=%s tf=%s last_closed=%s now=%s -> forcing warmup",
                symbol,
                tf,
                last_closed,
                now_ms,
            )
            refreshed = await market_data_uc.warmup(symbol, tf)
            if refreshed is not None:
                return refreshed

        return last_closed

    async def market_entry_handler(
        symbol: str,
        tf: str,
        now_ms: int,
        *,
        force_signal_scan: bool = False,
    ) -> MarketEntryProcessResult:
        cycle_started_at = time.perf_counter()
        indicator_started_at = time.perf_counter()
        bar_ready, last_closed_time_ms, sync_reason = await market_data_uc.sync_if_new_bar(symbol, tf)
        market_sync_ms = int((time.perf_counter() - indicator_started_at) * 1000)
        sync_metrics = market_data_uc.get_last_sync_metrics(symbol, tf)
        indicator_calculation_ms = int(sync_metrics.get("build_indicators_ms", 0) or 0)

        if not bar_ready and not force_signal_scan:
            return MarketEntryProcessResult(
                symbol=symbol,
                tf=tf,
                bar_ready=False,
                last_closed_time_ms=last_closed_time_ms,
                reason=sync_reason,
                duration_ms=int((time.perf_counter() - cycle_started_at) * 1000),
                indicator_calculation_ms=indicator_calculation_ms,
                market_sync_ms=market_sync_ms,
                last_closed_ms=int(sync_metrics.get("last_closed_ms", 0) or 0),
                get_tail_ms=int(sync_metrics.get("get_tail_ms", 0) or 0),
                fetch_klines_ms=int(sync_metrics.get("fetch_klines_ms", 0) or 0),
                merge_bars_ms=int(sync_metrics.get("merge_bars_ms", 0) or 0),
                indicator_build_wait_ms=int(sync_metrics.get("build_wait_ms", 0) or 0),
                upsert_bars_ms=int(sync_metrics.get("upsert_bars_ms", 0) or 0),
                cache_mode="not_ready",
            )

        entry_ready, processed_close, entry_reason = await entry_uc.run(
            symbol,
            tf,
            now_ms,
            force_signal_scan=force_signal_scan,
        )
        entry_metrics = dict(getattr(entry_uc, "last_metrics", {}) or {})
        duration_ms = int((time.perf_counter() - cycle_started_at) * 1000)

        return MarketEntryProcessResult(
            symbol=symbol,
            tf=tf,
            bar_ready=bool(bar_ready or force_signal_scan),
            last_closed_time_ms=processed_close or last_closed_time_ms,
            reason=entry_reason if entry_ready or force_signal_scan else sync_reason,
            duration_ms=duration_ms,
            indicator_calculation_ms=indicator_calculation_ms,
            market_sync_ms=market_sync_ms,
            last_closed_ms=int(sync_metrics.get("last_closed_ms", 0) or 0),
            get_tail_ms=int(sync_metrics.get("get_tail_ms", 0) or 0),
            fetch_klines_ms=int(sync_metrics.get("fetch_klines_ms", 0) or 0),
            merge_bars_ms=int(sync_metrics.get("merge_bars_ms", 0) or 0),
            indicator_build_wait_ms=int(sync_metrics.get("build_wait_ms", 0) or 0),
            upsert_bars_ms=int(sync_metrics.get("upsert_bars_ms", 0) or 0),
            fetch_bars_ms=int(entry_metrics.get("feature_tail_ms", 0) or 0),
            feature_rows=int(entry_metrics.get("feature_rows", 0) or 0),
            cache_mode=str(entry_metrics.get("cache_mode") or "memory"),
            v1_signal_check_ms=int(entry_metrics.get("v1_signal_check_ms", 0) or 0),
            v2_signal_check_ms=int(entry_metrics.get("v2_signal_check_ms", 0) or 0),
            user_routing_ms=int(entry_metrics.get("user_routing_ms", 0) or 0),
            entry_checks_ms=int(entry_metrics.get("entry_checks_ms", 0) or 0),
            order_trade_creation_ms=int(entry_metrics.get("order_trade_creation_ms", 0) or 0),
            user_checks=int(entry_metrics.get("user_checks", 0) or 0),
            created=int(entry_metrics.get("created", 0) or 0),
            blocked=int(entry_metrics.get("blocked", 0) or 0),
            signals_computed=int(entry_metrics.get("signals_computed", 0) or 0),
            active_signals=int(entry_metrics.get("active_signals", 0) or 0),
            v1_checked=int(entry_metrics.get("v1_checked", 0) or 0),
            v2_checked=int(entry_metrics.get("v2_checked", 0) or 0),
            v1_skipped=int(entry_metrics.get("v1_skipped", 0) or 0),
            v2_skipped=int(entry_metrics.get("v2_skipped", 0) or 0),
            signal_cache_hits=int(entry_metrics.get("signal_cache_hits", 0) or 0),
            active_signal_breakdown=dict(entry_metrics.get("active_signal_breakdown") or {}),
            created_breakdown=dict(entry_metrics.get("created_breakdown") or {}),
            blocked_breakdown=dict(entry_metrics.get("blocked_breakdown") or {}),
            blocked_reasons=dict(entry_metrics.get("blocked_reasons") or {}),
        )

    async def exit_handler(now_ms: int) -> None:
        started_at = time.perf_counter()
        await exit_uc.run(now_ms)
        metrics = dict(getattr(exit_uc, "last_metrics", {}) or {})
        if metrics.get("open_trades"):
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            log_fn = log.info if DEBUG_EXIT_REPLAY else log.debug
            log_fn(
                "[cycle-metrics] duration_ms=%s symbols=%s timeframes=%s indicator_calculation_ms=%s v1_signal_check_ms=%s v2_signal_check_ms=%s user_routing_ms=%s entry_checks_ms=%s order_trade_creation_ms=%s exit_replay_ms=%s user_checks=%s created=%s blocked=%s exit_open_trades=%s exit_live_trades=%s exit_replay_processed=%s exit_replay_skipped=%s reason=%s",
                duration_ms,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                int(metrics.get("duration_ms", 0) or 0),
                0,
                0,
                0,
                int(metrics.get("open_trades", 0) or 0),
                int(metrics.get("live_trades", 0) or 0),
                int(metrics.get("replay_processed", 0) or 0),
                int(metrics.get("replay_skipped", 0) or 0),
                "exit_replay",
            )

    scheduler = BarCloseScheduler(
        SchedulerConfig(
            first_check_delay_ms=15_000,
            retry_delay_ms=15_000,
            max_retries_after_close=8,
        )
    )

    runtime = AsyncBotOnlineRuntime(
        symbols=initial_symbols,
        tfs=TFS,
        scheduler=scheduler,
        last_closed_provider=last_closed_provider,
        market_entry_handler=market_entry_handler,
        exit_handler=exit_handler,
        scheduler_poll_ms=1_000,
        exit_poll_ms=5_000,
        market_entry_max_concurrency=MARKET_ENTRY_MAX_CONCURRENCY,
        exit_max_concurrency=1,
        symbols_provider=rollout.get_active_symbols if ROLLOUT_ENABLED else None,
        signal_max_concurrency_15m=SIGNAL_MAX_CONCURRENCY_15M,
        signal_max_concurrency_other=SIGNAL_MAX_CONCURRENCY_OTHER,
        signal_max_concurrency_hard_limit=SIGNAL_MAX_CONCURRENCY_HARD_LIMIT,
        admin_alert_service=admin_alert_service,
        admin_signal_scan_report_min_interval_seconds=ADMIN_SIGNAL_SCAN_REPORT_MIN_INTERVAL_SECONDS,
    )

    async def rollout_loop() -> None:
        if not ROLLOUT_ENABLED:
            return

        log.info("[rollout] timer loop started")
        while True:
            await asyncio.sleep(30)

            previous_count = rollout.current_count
            changed = rollout.maybe_grow()
            if changed:
                active_symbols = rollout.get_active_symbols()
                added_symbols = active_symbols[previous_count: rollout.current_count]
                log.info(
                    "[rollout] active symbols increased from=%s to=%s added=%s active_symbols_count=%s",
                    previous_count,
                    rollout.current_count,
                    added_symbols,
                    len(active_symbols),
                )

    try:
        await ws_prices.start()
        await user_stream_supervisor.start()
        await bootstrap_warmup()

        startup_reconcile_summary = await startup_reconcile_uc.run()
        log.info("[main-async] startup reconcile summary=%s", startup_reconcile_summary)

        await asyncio.gather(
            runtime.run(),
            rollout_loop(),
        )
    finally:
        await user_stream_supervisor.stop()
        await ws_prices.stop()
        await exchange.close()
        await mongo_write_queue.stop(
            flush_timeout_seconds=float(os.getenv("MONGO_WRITE_QUEUE_DRAIN_TIMEOUT_SECONDS", "20"))
        )
        await mongo.close()
        await pg_pool.close()


if __name__ == "__main__":
    asyncio.run(main())
