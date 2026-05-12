from __future__ import annotations

import logging
import math
import os
import time
from collections import defaultdict
from functools import partial

import asyncio
import anyio

from meowbot.core.configs.indicator_sets import get_indicator_set
from meowbot.core.domain.types import Bar
from meowbot.core.services.market_data.online_indicators_builder import OnlineIndicatorsBuilder


log = logging.getLogger("meowbot")


class AsyncEnsureMarketDataUseCase:
    """
    Старт:
    - якщо даних нема -> fetch 500 bars, build indicators, save all

    Runtime:
    - беремо 300 bars із Mongo
    - тягнемо нові bars із Binance
    - rebuild indicators на вікні
    - upsert назад
    """

    def __init__(
        self,
        *,
        bars_repo,
        exchange,
        features_ver: str,
        startup_history_bars: int = 500,
        runtime_window_bars: int = 300,
    ) -> None:
        self.bars_repo = bars_repo
        self.exchange = exchange
        self.features_ver = features_ver
        self.startup_history_bars = startup_history_bars
        self.runtime_window_bars = runtime_window_bars

        self.indicator_set = get_indicator_set(features_ver)
        self.builder = OnlineIndicatorsBuilder(self.indicator_set)
        indicator_concurrency = int(os.getenv("INDICATOR_BUILD_MAX_CONCURRENCY", "4"))
        self.indicator_build_max_concurrency = max(1, indicator_concurrency)
        self._indicator_build_sem = asyncio.Semaphore(self.indicator_build_max_concurrency)
        self._sync_locks: defaultdict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._build_counts_by_cache_key: defaultdict[str, int] = defaultdict(int)
        self._last_sync_metrics: dict[tuple[str, str], dict[str, object]] = {}
        log.info(
            "[indicator-config] features_ver=%s runtime_window_bars=%s build_max_concurrency=%s",
            self.features_ver,
            self.runtime_window_bars,
            self.indicator_build_max_concurrency,
        )

    async def warmup(self, symbol: str, tf: str) -> int | None:
        existing_last = await self.bars_repo.get_last_close_time(
            symbol=symbol,
            tf=tf,
            features_ver=self.features_ver,
        )

        if existing_last is not None and await self._latest_features_ready(symbol=symbol, tf=tf):
            return existing_last

        if existing_last is not None:
            log.warning(
                "[startup] feature self-test failed symbol=%s tf=%s features_ver=%s last_close=%s action=rebuild_history",
                symbol,
                tf,
                self.features_ver,
                existing_last,
            )

        latest_closed = await self.exchange.get_last_closed_time(symbol, tf)
        raw = await self.exchange.fetch_klines(
            symbol=symbol,
            tf=tf,
            start_ms=None,
            end_ms=latest_closed,
            limit=self.startup_history_bars,
        )
        if not raw:
            return None

        built = await anyio.to_thread.run_sync(
            partial(
                self.builder.build,
                raw,
                features_ver=self.features_ver,
            )
        )

        await self.bars_repo.upsert_many(built)
        return built[-1].close_time if built else None

    async def _latest_features_ready(self, *, symbol: str, tf: str) -> bool:
        tail = await self.bars_repo.get_tail(
            symbol=symbol,
            tf=tf,
            n=1,
            features_ver=self.features_ver,
            require_features_ok=False,
        )
        if not tail:
            return False

        latest = tail[-1]
        features = dict(latest.features or {})
        missing = [
            name
            for name in self.indicator_set.required_feature_names()
            if not self._is_finite_number(features.get(name))
        ]
        if missing or not bool(latest.features_ok):
            log.warning(
                "[startup] FEATURE_PIPELINE_MISSING symbol=%s tf=%s features_ver=%s close_time=%s features_ok=%s missing=%s",
                symbol,
                tf,
                self.features_ver,
                latest.close_time,
                latest.features_ok,
                missing[:20],
            )
            return False
        return True

    @staticmethod
    def _is_finite_number(value) -> bool:
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    async def sync_if_new_bar(self, symbol: str, tf: str) -> tuple[bool, int | None, str]:
        lock = self._sync_locks[(str(symbol).upper(), str(tf))]
        async with lock:
            return await self._sync_if_new_bar_locked(symbol, tf)

    def get_last_sync_metrics(self, symbol: str, tf: str) -> dict[str, object]:
        return dict(self._last_sync_metrics.get((str(symbol).upper(), str(tf)), {}))

    async def _sync_if_new_bar_locked(self, symbol: str, tf: str) -> tuple[bool, int | None, str]:
        total_started_at = time.perf_counter()
        metrics: dict[str, object] = {
            "symbol": symbol,
            "tf": tf,
            "features_ver": self.features_ver,
            "indicator_build_max_concurrency": self.indicator_build_max_concurrency,
            "last_closed_ms": 0,
            "get_tail_ms": 0,
            "fetch_klines_ms": 0,
            "merge_bars_ms": 0,
            "build_wait_ms": 0,
            "build_indicators_ms": 0,
            "upsert_bars_ms": 0,
            "total_ms": 0,
            "rows": 0,
            "cache_key": None,
            "cache_hit": False,
            "recalculated": False,
            "reason": None,
        }

        async def finish(result: tuple[bool, int | None, str]) -> tuple[bool, int | None, str]:
            metrics["reason"] = result[2]
            metrics["total_ms"] = int((time.perf_counter() - total_started_at) * 1000)
            self._last_sync_metrics[(str(symbol).upper(), str(tf))] = dict(metrics)
            total_ms = int(metrics.get("total_ms", 0) or 0)
            log_fn = (
                log.warning
                if total_ms > 1000
                else log.info
                if bool(metrics.get("recalculated")) or os.getenv("DEBUG_INDICATORS", "false").lower() == "true"
                else log.debug
            )
            log_fn(
                "[indicator-cache] symbol=%s tf=%s cache_key=%s cache_hit=%s recalculated=%s reason=%s total_ms=%s last_closed_ms=%s get_tail_ms=%s fetch_klines_ms=%s merge_bars_ms=%s build_wait_ms=%s build_indicators_ms=%s upsert_bars_ms=%s rows=%s build_count=%s",
                symbol,
                tf,
                metrics.get("cache_key"),
                metrics.get("cache_hit"),
                metrics.get("recalculated"),
                metrics.get("reason"),
                metrics.get("total_ms"),
                metrics.get("last_closed_ms"),
                metrics.get("get_tail_ms"),
                metrics.get("fetch_klines_ms"),
                metrics.get("merge_bars_ms"),
                metrics.get("build_wait_ms"),
                metrics.get("build_indicators_ms"),
                metrics.get("upsert_bars_ms"),
                metrics.get("rows"),
                metrics.get("build_count", 0),
            )
            if int(metrics.get("build_count", 0) or 0) > 1:
                log.warning(
                    "[indicator-cache] repeated_recalculation symbol=%s tf=%s cache_key=%s build_count=%s reason=%s",
                    symbol,
                    tf,
                    metrics.get("cache_key"),
                    metrics.get("build_count"),
                    metrics.get("reason"),
                )
            return result

        started_at = time.perf_counter()
        last_exchange_close = await self.exchange.get_last_closed_time(symbol, tf)
        metrics["last_closed_ms"] = int((time.perf_counter() - started_at) * 1000)
        if last_exchange_close is None:
            return await finish((False, None, "no_exchange_last_close"))
        metrics["cache_key"] = f"{str(symbol).upper()}:{tf}:{self.features_ver}:{last_exchange_close}"

        started_at = time.perf_counter()
        tail = await self.bars_repo.get_tail(
            symbol=symbol,
            tf=tf,
            n=self.runtime_window_bars,
            features_ver=self.features_ver,
            require_features_ok=False,
        )
        metrics["get_tail_ms"] = int((time.perf_counter() - started_at) * 1000)
        metrics["rows"] = len(tail)

        if not tail:
            warmed = await self.warmup(symbol, tf)
            if warmed is None:
                return await finish((False, None, "warmup_failed"))
            return await finish((True, warmed, "warmup_created"))

        last_stored_close = tail[-1].close_time
        if last_exchange_close <= last_stored_close:
            metrics["cache_hit"] = True
            return await finish((False, last_stored_close, "no_new_bar"))

        started_at = time.perf_counter()
        raw_new = await self.exchange.fetch_klines(
            symbol=symbol,
            tf=tf,
            start_ms=last_stored_close,
            end_ms=last_exchange_close,
            limit=20,
        )
        metrics["fetch_klines_ms"] = int((time.perf_counter() - started_at) * 1000)
        if not raw_new:
            return await finish((False, last_stored_close, "no_new_klines"))

        started_at = time.perf_counter()
        merged = self._merge_bars(tail, raw_new)
        merged = merged[-self.runtime_window_bars:]
        metrics["merge_bars_ms"] = int((time.perf_counter() - started_at) * 1000)
        metrics["rows"] = len(merged)

        wait_started_at = time.perf_counter()
        async with self._indicator_build_sem:
            metrics["build_wait_ms"] = int((time.perf_counter() - wait_started_at) * 1000)
            build_started_at = time.perf_counter()
            built, breakdown = await anyio.to_thread.run_sync(
                partial(
                    self.builder.build_with_breakdown,
                    merged,
                    features_ver=self.features_ver,
                )
            )
            metrics["build_indicators_ms"] = int((time.perf_counter() - build_started_at) * 1000)
        metrics["recalculated"] = True
        cache_key = str(metrics["cache_key"])
        self._build_counts_by_cache_key[cache_key] += 1
        metrics["build_count"] = self._build_counts_by_cache_key[cache_key]
        metrics["indicator_breakdown"] = dict(breakdown or {})

        started_at = time.perf_counter()
        await self.bars_repo.upsert_many(built)
        metrics["upsert_bars_ms"] = int((time.perf_counter() - started_at) * 1000)

        return await finish((True, built[-1].close_time, "new_bar_synced"))

    def _merge_bars(self, old_bars: list[Bar], new_bars: list[Bar]) -> list[Bar]:
        by_close: dict[int, Bar] = {}
        for bar in old_bars:
            by_close[bar.close_time] = bar
        for bar in new_bars:
            by_close[bar.close_time] = bar

        merged = list(by_close.values())
        merged.sort(key=lambda item: item.close_time)
        return merged
