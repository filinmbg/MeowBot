from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from meowbot.core.domain.types import Trade
from meowbot.core.services.entry.long_breakout_v18_detector import LONG_BREAKOUT_V18_RULE_ID
from meowbot.core.services.entry.rsi_rebound_supertrend_detector import RsiReboundSupertrendConfig
from meowbot.core.services.exchange.per_user_binance_account_provider import PerUserBinanceAccountProvider
from meowbot.core.services.risk.per_user_live_risk_service import PerUserLiveRiskService
from meowbot.core.services.runtime.active_trades_cache import ActiveTradesCache
from meowbot.core.services.runtime.bars_cache import BarsCache, CacheBackedBarsRepositoryAsync
from meowbot.core.services.runtime.symbol_validator import validate_binance_usdt_perp_symbol
from meowbot.core.usecases.async_ensure_market_data import AsyncEnsureMarketDataUseCase
from meowbot.core.usecases.async_online_entry_cycle import AsyncOnlineEntryCycleUseCase
from meowbot.infra.broker.binance_live_async import BinanceLiveBrokerAsync
from meowbot.infra.broker.mode_aware import ModeAwareBroker
from meowbot.infra.broker.paper import PaperBroker
from meowbot.infra.exchange.binance_futures_account_async import BinanceFuturesAccountAsyncConfig
from meowbot.infra.exchange.binance_futures_usdtm_async import (
    BinanceFuturesAsyncConfig,
    BinanceFuturesMarketDataAsync,
)
from meowbot.infra.mongo.async_client import AsyncMongoConfig, AsyncMongoConn
from meowbot.infra.mongo.repos_async.bot_state_repo_async import BotStateRepositoryMongoAsync
from meowbot.infra.mongo.repos_async.trade_events_repo_async import TradeEventsRepositoryMongoAsync
from meowbot.infra.mongo.repos_async.trades_repo_async import TradesRepositoryMongoAsync
from meowbot.infra.postgres.client import get_pg_pool
from meowbot.infra.postgres.repos.subscription_runtime_repo import SubscriptionRuntimeRepo
from meowbot.infra.postgres.repos.trade_entry_cooldowns_repo import TradeEntryCooldownsRepo
from meowbot.infra.postgres.repos.user_api_keys_repo import UserApiKeysRepo
from meowbot.core.services.security.fernet_crypto_service import FernetCryptoService


ROOT = Path(__file__).resolve().parents[2]
FEATURES_VER = os.getenv("FEATURES_VER", "v2_core")
DEFAULT_RUNTIME_USER_ID = os.getenv("LIVE_TEST_RUNTIME_USER_ID", "tg:853048829")

log = logging.getLogger("meowbot")


V2_SIGNAL_PROFILES: dict[str, dict[str, float | int]] = {
    "weak": {
        "rsi_14": 80.0,
        "dist_to_ema_50_pct": 0.02,
        "volume_ratio_sma_20": 2.0,
        "atr_14_pct": 0.005,
        "vol_peak_offset_10": -2,
        "close_position_in_candle": 0.70,
        "adx_14": 25.0,
        "signal_score": 6,
        "position_size_multiplier": 1.0,
        "soft_stop_activation_pct": 0.6,
    },
    "medium": {
        "rsi_14": 82.0,
        "dist_to_ema_50_pct": 0.022,
        "volume_ratio_sma_20": 2.1,
        "atr_14_pct": 0.0055,
        "vol_peak_offset_10": -2,
        "close_position_in_candle": 0.68,
        "adx_14": 26.0,
        "signal_score": 7,
        "position_size_multiplier": 1.25,
        "soft_stop_activation_pct": 0.8,
    },
    "strong": {
        "rsi_14": 84.0,
        "dist_to_ema_50_pct": 0.024,
        "volume_ratio_sma_20": 2.4,
        "atr_14_pct": 0.006,
        "vol_peak_offset_10": -1,
        "close_position_in_candle": 0.68,
        "adx_14": 30.0,
        "signal_score": 9,
        "position_size_multiplier": 1.5,
        "soft_stop_activation_pct": 1.0,
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Force one entry-cycle signal for a single symbol/user. Dry-run by default."
    )
    parser.add_argument("--symbol", default="ZRXUSDT", help="Binance USD-M perpetual symbol.")
    parser.add_argument("--tf", default="15m", help="Entry timeframe.")
    parser.add_argument(
        "--user",
        default=DEFAULT_RUNTIME_USER_ID,
        help="Runtime user id, e.g. tg:853048829. Defaults to LIVE_TEST_RUNTIME_USER_ID or tg:853048829.",
    )
    parser.add_argument(
        "--level",
        choices=("weak", "medium", "strong"),
        default="weak",
        help="Forced V2 signal level when the user resolves to strategy_version=v2.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually call entry-cycle open flow. Without this flag the script only prints dry-run diagnostics.",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required together with --execute when the resolved user trading_mode is live/real.",
    )
    parser.add_argument(
        "--use-live-price-as-signal",
        action="store_true",
        help="Use current mark price as signal price so the normal entry price deviation gate can pass in a forced test.",
    )
    parser.add_argument(
        "--unique-close-time",
        action="store_true",
        help="Use current time as forced entry_bar_close_time to avoid trade_id collision during repeated tests.",
    )
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("meowbot").setLevel(logging.INFO)


def runtime_user_to_telegram_id(runtime_user_id: str) -> int:
    value = str(runtime_user_id).strip()
    if not value.startswith("tg:") or not value.removeprefix("tg:").isdigit():
        raise ValueError("runtime user must look like tg:<telegram_id>")
    return int(value.removeprefix("tg:"))


def build_account_config() -> BinanceFuturesAccountAsyncConfig:
    return BinanceFuturesAccountAsyncConfig(
        futures_base_url=os.getenv("BINANCE_FUTURES_BASE_URL", "https://fapi.binance.com"),
        wallet_base_url=os.getenv("BINANCE_WALLET_BASE_URL", "https://api.binance.com"),
        timeout_seconds=float(os.getenv("BINANCE_HTTP_TIMEOUT_SECONDS", "15")),
        max_weight_per_minute=int(os.getenv("BINANCE_MAX_WEIGHT_PER_MINUTE", "600")),
        max_concurrent_requests=int(os.getenv("BINANCE_MAX_CONCURRENT_REQUESTS", "2")),
        retry_attempts=int(os.getenv("BINANCE_HTTP_RETRY_ATTEMPTS", "5")),
        retry_base_delay_seconds=float(os.getenv("BINANCE_HTTP_RETRY_BASE_DELAY_SECONDS", "1.0")),
        retry_max_delay_seconds=float(os.getenv("BINANCE_HTTP_RETRY_MAX_DELAY_SECONDS", "15.0")),
        recv_window_ms=int(os.getenv("BINANCE_RECV_WINDOW_MS", "30000")),
        signed_request_safety_margin_ms=int(os.getenv("BINANCE_SIGNED_REQUEST_SAFETY_MARGIN_MS", "3000")),
    )


def forced_v2_signal_meta(level: str) -> dict[str, Any]:
    profile = dict(V2_SIGNAL_PROFILES[level])
    indicator_values = {
        "rsi_14": profile["rsi_14"],
        "dist_to_ema_50_pct": profile["dist_to_ema_50_pct"],
        "volume_ratio_sma_20": profile["volume_ratio_sma_20"],
        "atr_14_pct": profile["atr_14_pct"],
        "vol_peak_offset_10": profile["vol_peak_offset_10"],
        "close_position_in_candle": profile["close_position_in_candle"],
        "adx_14": profile["adx_14"],
    }
    return {
        "forced_signal": True,
        "force_reason": "manual_entry_cycle_test",
        "signal_level": level,
        "final_signal": level,
        "signal_score": int(profile["signal_score"]),
        "position_size_multiplier": float(profile["position_size_multiplier"]),
        "soft_stop_activation_pct": float(profile["soft_stop_activation_pct"]),
        "soft_stop_start_pct": 0.1,
        "soft_stop_increment_pct": 0.05,
        "soft_stop_increment_interval_seconds": 900,
        "indicator_values": indicator_values,
        "strong_result": level == "strong",
        "medium_result": level == "medium",
        "weak_result": level == "weak",
        "strong_passed": level == "strong",
        "medium_passed": level == "medium",
        "weak_passed": level == "weak",
        "strong_failed_conditions": [] if level == "strong" else ["forced lower signal level"],
        "medium_failed_conditions": [] if level == "medium" else ["forced different signal level"],
        "weak_failed_conditions": [] if level == "weak" else ["forced higher signal level"],
    }


def forced_v1_signal_meta() -> dict[str, Any]:
    return {
        "forced_signal": True,
        "force_reason": "manual_entry_cycle_test",
        "low_level": 45.0,
        "reclaim_level": 50.0,
        "lookback": 10,
        "rsi_key": "rsi14",
        "supertrend_key": "supertrend_bullish_10_3_0",
    }


def merge_forced_features(bar, signal_meta: dict[str, Any], *, strategy_version: str) -> dict[str, Any]:
    features = dict(getattr(bar, "features", None) or {})
    if strategy_version == "v2":
        features.update(dict(signal_meta.get("indicator_values") or {}))
    else:
        features.setdefault("rsi14", 55.0)
        features.setdefault("supertrend_bullish_10_3_0", 1)
    return features


async def wait_for_binance_ready(exchange: BinanceFuturesMarketDataAsync) -> None:
    for attempt in range(1, 4):
        if await exchange.ping():
            log.info("[force-entry] binance ready attempt=%s", attempt)
            return
        await asyncio.sleep(1.0)
    raise RuntimeError("Binance REST is not ready")


async def load_open_trades(repo: TradesRepositoryMongoAsync) -> list[Trade]:
    try:
        return await repo.get_open_trades()
    except Exception:
        log.exception("[force-entry] failed to load open trades; continuing with empty startup cache")
        return []


def group_open_trades_by_user(trades: list[Trade]) -> dict[str, list[Trade]]:
    grouped: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        grouped[str(trade.user_id)].append(trade)
    return grouped


async def main_async() -> int:
    load_dotenv(ROOT / ".env")
    configure_logging()
    args = parse_args()

    symbol_check = validate_binance_usdt_perp_symbol(args.symbol)
    if not symbol_check.valid:
        raise SystemExit(f"Invalid symbol {args.symbol!r}: {symbol_check.reason}")
    symbol = str(symbol_check.normalized_symbol)
    runtime_user_id = str(args.user).strip()
    runtime_user_to_telegram_id(runtime_user_id)

    pg_pool = await get_pg_pool()
    mongo = AsyncMongoConn(
        AsyncMongoConfig(
            app_name=os.getenv("MONGO_APP_NAME_FORCE_ENTRY", "MeowBot-force-entry"),
            max_pool_size=int(os.getenv("MONGO_MAX_POOL_SIZE_FORCE_ENTRY", "5")),
            min_pool_size=int(os.getenv("MONGO_MIN_POOL_SIZE_FORCE_ENTRY", "0")),
        )
    )
    exchange = BinanceFuturesMarketDataAsync(BinanceFuturesAsyncConfig())

    try:
        await mongo.connect()
        assert mongo.db is not None
        trades_repo = TradesRepositoryMongoAsync(mongo.db)
        bot_state_repo = BotStateRepositoryMongoAsync(mongo.db)
        trade_events_repo = TradeEventsRepositoryMongoAsync(mongo.db)
        await trades_repo.ensure_indexes()
        await trade_events_repo.ensure_indexes()

        runtime_repo = SubscriptionRuntimeRepo(pg_pool)
        tg_user = await runtime_repo.get_by_trading_user_id(runtime_user_id)
        if not tg_user:
            raise SystemExit(f"User {runtime_user_id} was not found in Postgres runtime profile")
        tg_user["trading_user_id"] = runtime_user_id
        if str(tg_user.get("trading_mode") or "").lower() == "real":
            tg_user["trading_mode"] = "live"

        trading_mode = str(tg_user.get("trading_mode") or "sandbox").lower()
        strategy_version = str(tg_user.get("strategy_version") or "v1").lower()
        if trading_mode == "live" and args.execute and not args.confirm_live:
            raise SystemExit("Refusing live order: pass --confirm-live together with --execute")

        bars_cache = BarsCache(maxlen=int(os.getenv("BARS_CACHE_MAXLEN", "500")))
        bars_repo = CacheBackedBarsRepositoryAsync(
            cache=bars_cache,
            persistence_repo=None,
            persist_writes=False,
        )
        market_data_uc = AsyncEnsureMarketDataUseCase(
            bars_repo=bars_repo,
            exchange=exchange,
            features_ver=FEATURES_VER,
            startup_history_bars=500,
            runtime_window_bars=300,
        )

        await wait_for_binance_ready(exchange)
        last_close = await market_data_uc.warmup(symbol, args.tf)
        if last_close is None:
            raise SystemExit(f"No closed market data for {symbol} {args.tf}")

        tail = await bars_repo.get_tail(
            symbol=symbol,
            tf=args.tf,
            n=1,
            features_ver=FEATURES_VER,
            require_features_ok=False,
        )
        if not tail:
            raise SystemExit(f"No warmed bars for {symbol} {args.tf}")

        latest_bar = tail[-1]
        live_entry_price = await exchange.get_mark_price(symbol)
        if live_entry_price is None:
            raise SystemExit(f"Could not fetch mark price for {symbol}")

        if strategy_version == "v2":
            rule_id = LONG_BREAKOUT_V18_RULE_ID
            signal_meta = forced_v2_signal_meta(args.level)
        else:
            rule_id = RsiReboundSupertrendConfig().rule_id
            signal_meta = forced_v1_signal_meta()

        signal_price = float(live_entry_price if args.use_live_price_as_signal else latest_bar.c)
        forced_close_time = int(time.time() * 1000) if args.unique_close_time else int(latest_bar.close_time)
        latest_bar = replace(
            latest_bar,
            c=signal_price,
            close_time=forced_close_time,
            features=merge_forced_features(latest_bar, signal_meta, strategy_version=strategy_version),
            features_ok=True,
            features_ver=FEATURES_VER,
        )

        account_config = build_account_config()
        user_api_keys_repo = UserApiKeysRepo(pg_pool)
        crypto_service = FernetCryptoService.from_env("MEOWBOT_SECRETS_FERNET_KEY")
        account_provider = PerUserBinanceAccountProvider(
            user_api_keys_repo=user_api_keys_repo,
            crypto_service=crypto_service,
            account_config=account_config,
        )
        live_broker = BinanceLiveBrokerAsync(account_provider=account_provider)
        broker = ModeAwareBroker(
            sandbox_broker=PaperBroker(),
            live_entry_broker=live_broker,
        )
        active_trades_cache = ActiveTradesCache()
        open_trades = await load_open_trades(trades_repo)
        active_trades_cache.load_open_trades(open_trades, source="force_entry_cycle_signal")

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
            cooldowns_repo=TradeEntryCooldownsRepo(pg_pool),
            per_user_live_risk_service=PerUserLiveRiskService(
                user_api_keys_repo=user_api_keys_repo,
                crypto_service=crypto_service,
                account_config=account_config,
            ),
            active_trades_cache=active_trades_cache,
        )

        price_check = entry_uc._check_long_entry_price(
            signal_entry_price=signal_price,
            live_entry_price=float(live_entry_price),
        )
        log.info(
            "[force-entry] symbol=%s tf=%s user=%s mode=%s strategy_version=%s rule_id=%s execute=%s confirm_live=%s signal_price=%s live_price=%s price_allowed=%s price_reason=%s level=%s",
            symbol,
            args.tf,
            runtime_user_id,
            trading_mode,
            strategy_version,
            rule_id,
            args.execute,
            args.confirm_live,
            signal_price,
            live_entry_price,
            price_check["allowed"],
            price_check["reason"],
            args.level if strategy_version == "v2" else None,
        )
        if not price_check["allowed"]:
            log.warning("[force-entry] entry price gate blocked forced signal: %s", price_check)
            return 2

        if not args.execute:
            log.info("[force-entry] dry-run only. Add --execute%s to open.", " --confirm-live" if trading_mode == "live" else "")
            return 0

        result = await entry_uc._try_open_for_user(
            tg_user=tg_user,
            symbol=symbol,
            tf=args.tf,
            now_ms=int(time.time() * 1000),
            bar=latest_bar,
            rule_id=rule_id,
            strategy_version=strategy_version,
            signal_meta=signal_meta,
            signal_entry_price=signal_price,
            live_entry_price=float(live_entry_price),
            deviation_pct=float(price_check["effective_deviation_pct"]),
            entry_price_check=price_check,
            open_trades_by_user=group_open_trades_by_user(open_trades),
            metrics={},
        )
        if result is None:
            log.warning("[force-entry] entry-cycle completed but trade was skipped. Check [entry-decision] / [LIVE_ENTRY_SKIPPED] logs above.")
            return 3

        log.info(
            "[force-entry] trade created trade_id=%s symbol=%s user=%s mode=%s qty=%s entry=%s",
            result.trade_id,
            result.symbol,
            result.user_id,
            result.mode,
            result.qty,
            result.entry_price,
        )
        return 0
    finally:
        await exchange.close()
        await mongo.close()
        await pg_pool.close()


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
