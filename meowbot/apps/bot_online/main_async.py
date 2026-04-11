from __future__ import annotations

import asyncio
import logging
import os
import time

from meowbot.apps.bot_online.async_runtime import (
    AsyncBotOnlineRuntime,
    MarketEntryProcessResult,
)
from meowbot.core.services.runtime.symbol_rollout_manager import SymbolRolloutManager
from meowbot.core.services.scheduling.bar_close_scheduler import (
    BarCloseScheduler,
    SchedulerConfig,
)
from meowbot.core.usecases.async_ensure_market_data import AsyncEnsureMarketDataUseCase
from meowbot.core.usecases.async_online_entry_cycle import AsyncOnlineEntryCycleUseCase
from meowbot.core.usecases.async_reconcile_open_trades import AsyncReconcileOpenTradesUseCase
from meowbot.infra.broker.paper import PaperBroker
from meowbot.infra.exchange.binance_futures_usdtm_async import (
    BinanceFuturesAsyncConfig,
    BinanceFuturesMarketDataAsync,
)
from meowbot.infra.exchange.binance_futures_ws_prices import BinanceFuturesWsPrices
from meowbot.infra.mongo.async_client import AsyncMongoConfig, AsyncMongoConn
from meowbot.infra.mongo.migrations import apply_migrations
from meowbot.infra.mongo.repos_async.bars_repo_async import BarsRepositoryMongoAsync
from meowbot.infra.mongo.repos_async.bot_state_repo_async import BotStateRepositoryMongoAsync
from meowbot.infra.mongo.repos_async.trade_events_repo_async import TradeEventsRepositoryMongoAsync
from meowbot.infra.mongo.repos_async.trades_repo_async import TradesRepositoryMongoAsync
from meowbot.infra.postgres.client import get_pg_pool
from meowbot.infra.postgres.repos.subscription_runtime_repo import SubscriptionRuntimeRepo
from meowbot.infra.postgres.repos.trade_entry_cooldowns_repo import (
    TradeEntryCooldownsRepo,
)


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
    "ROSEUSDT", "CELOUSDT", "LDOUSDT", "DYDXUSDT", "ARPAUSDT",
    "STXUSDT", "BLURUSDT", "JASMYUSDT", "GMXUSDT", "MASKUSDT",
    "YFIUSDT", "ZRXUSDT", "IOSTUSDT", "QTUMUSDT", "ANKRUSDT",
    "HOTUSDT", "ICXUSDT", "DASHUSDT", "OMGUSDT", "ONTUSDT",
    "SKLUSDT", "BATUSDT", "SUSHIUSDT", "RENUSDT", "RSRUSDT",
    "LRCUSDT", "ZENUSDT", "COTIUSDT", "STORJUSDT", "NKNUSDT",
    "MTLUSDT", "CHRUSDT", "DENTUSDT", "CELRUSDT", "BANDUSDT",
    "FLMUSDT", "TLMUSDT", "IDUSDT", "CFXUSDT", "HOOKUSDT",
    "MINAUSDT", "ASTRUSDT", "MAGICUSDT", "WOOUSDT", "PEOPLEUSDT",
]

ALL_SYMBOLS = [
    x.strip().upper()
    for x in os.getenv("ACTIVE_SYMBOLS", ",".join(DEFAULT_SYMBOLS)).split(",")
    if x.strip()
]

TFS = ["15m", "30m", "1h", "2h", "4h"]
FEATURES_VER = "v2_core"

ROLLOUT_ENABLED = os.getenv("SYMBOL_ROLLOUT_ENABLED", "true").lower() == "true"
ROLLOUT_BASE_COUNT = int(os.getenv("SYMBOL_ROLLOUT_BASE_COUNT", "5"))
ROLLOUT_STEP = int(os.getenv("SYMBOL_ROLLOUT_STEP", "5"))
ROLLOUT_MAX_COUNT = int(os.getenv("SYMBOL_ROLLOUT_MAX_COUNT", "100"))
ROLLOUT_GROW_INTERVAL_SECONDS = int(os.getenv("SYMBOL_ROLLOUT_GROW_INTERVAL_SECONDS", "900"))

STARTUP_PING_MAX_ATTEMPTS = int(os.getenv("BINANCE_STARTUP_PING_MAX_ATTEMPTS", "30"))
STARTUP_PING_SLEEP_SECONDS = float(os.getenv("BINANCE_STARTUP_PING_SLEEP_SECONDS", "10"))

STALE_LAST_CLOSED_MAX_AGE_MS = int(
    os.getenv("STALE_LAST_CLOSED_MAX_AGE_MS", str(3 * 24 * 60 * 60 * 1000))
)


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


def is_stale_last_closed(last_closed_time_ms: int | None, now_ms: int) -> bool:
    if last_closed_time_ms is None:
        return True
    return (now_ms - int(last_closed_time_ms)) > STALE_LAST_CLOSED_MAX_AGE_MS


async def main() -> None:
    pg_pool = await get_pg_pool()

    mongo = AsyncMongoConn(AsyncMongoConfig())
    await mongo.connect()

    apply_migrations(
        mongo.db,
        timeframes=("1m", "15m", "30m", "1h", "2h", "4h", "1d"),
    )

    bars_repo = BarsRepositoryMongoAsync(mongo.db)
    trades_repo = TradesRepositoryMongoAsync(mongo.db)
    bot_state_repo = BotStateRepositoryMongoAsync(mongo.db)
    trade_events_repo = TradeEventsRepositoryMongoAsync(mongo.db)

    runtime_repo = SubscriptionRuntimeRepo(pg_pool)
    cooldowns_repo = TradeEntryCooldownsRepo(pg_pool)

    exchange = BinanceFuturesMarketDataAsync(BinanceFuturesAsyncConfig())
    await wait_for_binance_ready(exchange)

    broker = PaperBroker()

    rollout = SymbolRolloutManager(
        all_symbols=ALL_SYMBOLS,
        base_count=ROLLOUT_BASE_COUNT,
        step=ROLLOUT_STEP,
        max_count=min(ROLLOUT_MAX_COUNT, len(ALL_SYMBOLS)),
        grow_interval_seconds=ROLLOUT_GROW_INTERVAL_SECONDS,
    )

    initial_symbols = rollout.get_active_symbols() if ROLLOUT_ENABLED else ALL_SYMBOLS

    log.info(
        "[main-async] all_symbols=%s rollout_enabled=%s base=%s step=%s max=%s grow_interval_seconds=%s",
        len(ALL_SYMBOLS),
        ROLLOUT_ENABLED,
        ROLLOUT_BASE_COUNT,
        ROLLOUT_STEP,
        min(ROLLOUT_MAX_COUNT, len(ALL_SYMBOLS)),
        ROLLOUT_GROW_INTERVAL_SECONDS,
    )
    log.info("[main-async] initial active symbols=%s", len(initial_symbols))

    ws_prices = BinanceFuturesWsPrices(
        symbols_provider=rollout.get_active_symbols if ROLLOUT_ENABLED else (lambda: ALL_SYMBOLS),
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
    )

    exit_uc = AsyncReconcileOpenTradesUseCase(
        trades_repo=trades_repo,
        broker=broker,
        trade_events_repo=trade_events_repo,
        price_provider=ws_prices,
        max_concurrency=20,
        cooldowns_repo=cooldowns_repo,
        telegram_users_repo=runtime_repo,
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

    async def market_entry_handler(symbol: str, tf: str, now_ms: int) -> MarketEntryProcessResult:
        bar_ready, last_closed_time_ms, sync_reason = await market_data_uc.sync_if_new_bar(symbol, tf)

        if not bar_ready:
            return MarketEntryProcessResult(
                symbol=symbol,
                tf=tf,
                bar_ready=False,
                last_closed_time_ms=last_closed_time_ms,
                reason=sync_reason,
            )

        entry_ready, processed_close, entry_reason = await entry_uc.run(symbol, tf, now_ms)

        return MarketEntryProcessResult(
            symbol=symbol,
            tf=tf,
            bar_ready=True,
            last_closed_time_ms=processed_close or last_closed_time_ms,
            reason=entry_reason if entry_ready else sync_reason,
        )

    async def exit_handler(now_ms: int) -> None:
        await exit_uc.run(now_ms)

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
        market_entry_max_concurrency=20,
        exit_max_concurrency=1,
        symbols_provider=rollout.get_active_symbols if ROLLOUT_ENABLED else None,
    )

    async def rollout_loop() -> None:
        if not ROLLOUT_ENABLED:
            return

        log.info("[rollout] timer loop started")
        while True:
            await asyncio.sleep(30)

            changed = rollout.maybe_grow()
            if changed:
                log.info("[rollout] active symbols increased to %s", rollout.current_count)

    try:
        await ws_prices.start()
        await bootstrap_warmup()
        await asyncio.gather(
            runtime.run(),
            rollout_loop(),
        )
    finally:
        await ws_prices.stop()
        await exchange.close()
        await mongo.close()
        await pg_pool.close()


if __name__ == "__main__":
    asyncio.run(main())