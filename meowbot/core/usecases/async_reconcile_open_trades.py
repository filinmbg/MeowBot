from __future__ import annotations

import inspect
import logging
import os
import time
from collections import defaultdict, deque
from dataclasses import replace
from datetime import timedelta
from typing import Any

import anyio

from meowbot.core.configs.strategy_version_test_users import is_strategy_version_test_user
from meowbot.core.domain.enums import TradeStatus
from meowbot.core.services.runtime.active_trades_cache import ActiveTradesCache
from meowbot.core.services.runtime.symbol_validator import validate_binance_usdt_perp_symbol


log = logging.getLogger("meowbot")
ENTRY_NOTIFICATION_RECOVERY_DELAY_MS = 60_000


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return int(default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


ENTRY_NOTIFICATION_RECOVERY_RETRY_MS = _env_int("TELEGRAM_ENTRY_NOTIFICATION_RECOVERY_RETRY_MS", 60_000)
ENTRY_NOTIFICATION_MAX_RECOVERY_RETRIES = _env_int("TELEGRAM_ENTRY_NOTIFICATION_MAX_RECOVERY_RETRIES", 0)


class AsyncReconcileOpenTradesUseCase:
    def __init__(
        self,
        *,
        trades_repo,
        broker,
        trade_events_repo,
        price_provider,
        max_concurrency: int = 8,
        cooldowns_repo=None,
        telegram_users_repo=None,
        live_sync_service=None,
        exchange=None,
        bars_repo=None,
        active_trades_cache=None,
        admin_alert_service=None,
        max_bars_per_request: int = 500,
        reconcile_tf: str = "1m",
        features_ver: str = "v2_core",
        max_replay_trades_per_tick: int = 20,
        max_replay_runtime_ms: int | None = None,
    ) -> None:
        self.trades_repo = trades_repo
        self.broker = broker
        self.trade_events_repo = trade_events_repo
        self.price_provider = price_provider
        self.cooldowns_repo = cooldowns_repo
        self.telegram_users_repo = telegram_users_repo
        self.live_sync_service = live_sync_service
        self.exchange = exchange
        self.bars_repo = bars_repo
        self.active_trades_cache = active_trades_cache or ActiveTradesCache()
        self.admin_alert_service = admin_alert_service
        self.max_bars_per_request = int(max_bars_per_request)
        self.reconcile_tf = reconcile_tf
        self.features_ver = str(features_ver or "v2_core")
        self.max_replay_trades_per_tick = max(0, int(max_replay_trades_per_tick))
        self.max_replay_runtime_ms = max(
            0,
            int(
                max_replay_runtime_ms
                if max_replay_runtime_ms is not None
                else os.getenv("EXIT_REPLAY_MAX_RUNTIME_MS", "5000")
            ),
        )
        self._replay_cursor = 0
        self._replay_avg_ms_per_trade = 0.0
        self.debug_exit_replay = _env_bool("DEBUG_EXIT_REPLAY", False)
        self.exit_summary_interval_seconds = max(
            1.0,
            float(os.getenv("EXIT_SUMMARY_INTERVAL_SECONDS", "60")),
        )
        self.slow_replay_critical_ms = max(
            1,
            int(os.getenv("EXIT_REPLAY_CRITICAL_MS", "30000")),
        )
        self.slow_replay_alert_window_seconds = max(
            1.0,
            float(os.getenv("EXIT_REPLAY_ALERT_WINDOW_SECONDS", "300")),
        )
        self.slow_replay_alert_count = max(
            1,
            int(os.getenv("EXIT_REPLAY_ALERT_COUNT", "3")),
        )
        self._last_exit_summary_at = 0.0
        self._summary_count = 0
        self._summary_duration_total_ms = 0
        self._summary_max_duration_ms = 0
        self._summary_counters: dict[str, int] = defaultdict(int)
        self._slow_replay_events: deque[float] = deque()
        self._last_replay_candles_by_trade_id: dict[str, int] = {}
        self.replay_cache_tail_bars = max(50, _env_int("EXIT_REPLAY_CACHE_TAIL_BARS", 500))
        self.slow_replay_trade_ms = max(1, _env_int("EXIT_REPLAY_SLOW_TRADE_MS", 1000))
        self.replay_metrics_min_ms = max(0, _env_int("EXIT_REPLAY_METRICS_MIN_MS", 500))
        self.log_noop_replay = _env_bool("EXIT_REPLAY_LOG_NOOP", False)
        self.last_metrics: dict[str, Any] = {}
        self._sem = anyio.Semaphore(max_concurrency)

        # TP1 = 0.5%, TP2 = 1.0%, TP3 = 1.5%
        self.tp_levels = [0.005, 0.01, 0.015]

    async def run(self, now_ms: int) -> None:
        started_at = time.perf_counter()
        open_trades = self.active_trades_cache.get_all_open_trades()
        live_trades = [
            trade for trade in open_trades
            if str(getattr(trade, "mode", "") or "").lower() == "live"
        ]
        replay_candidates_all = [
            trade for trade in open_trades
            if str(getattr(trade, "mode", "") or "").lower() != "live"
        ]
        replay_candidates = [
            trade for trade in replay_candidates_all
            if not self._is_replay_backed_off(trade, now_ms)
        ]
        replay_backed_off = len(replay_candidates_all) - len(replay_candidates)
        replay_trades = self._select_replay_batch(replay_candidates)

        if open_trades:
            self._log_exit_replay_debug(
                "[exit-async] open_trades=%d live=%d replay_candidates=%d replay_selected=%d replay_backed_off=%d end=%s",
                len(open_trades),
                len(live_trades),
                len(replay_candidates_all),
                len(replay_trades),
                replay_backed_off,
                now_ms,
            )

        live_timings: list[dict[str, Any]] = []
        if live_trades:
            async with anyio.create_task_group() as tg:
                for trade in live_trades:
                    tg.start_soon(
                        self._process_trade_safe,
                        trade,
                        now_ms,
                        False,
                        live_timings,
                    )

        replay_started_at = time.perf_counter()
        replay_trade_timings: list[dict[str, Any]] = []
        replay_processed = 0
        replay_budget_exhausted = False
        replay_candle_cache = await self._load_cycle_replay_candle_cache(replay_trades, now_ms)

        for trade in replay_trades:
            replay_elapsed_ms = int((time.perf_counter() - replay_started_at) * 1000)
            if self.max_replay_runtime_ms > 0 and replay_elapsed_ms >= self.max_replay_runtime_ms:
                replay_budget_exhausted = True
                break

            timing = await self._process_replay_trade_safe(trade, now_ms, replay_candle_cache)
            replay_trade_timings.append(timing)
            replay_processed += 1

        replay_duration_ms = int((time.perf_counter() - replay_started_at) * 1000) if replay_trades else 0
        if replay_processed:
            current_avg = replay_duration_ms / max(replay_processed, 1)
            self._replay_avg_ms_per_trade = (
                current_avg
                if self._replay_avg_ms_per_trade <= 0
                else (self._replay_avg_ms_per_trade * 0.7) + (current_avg * 0.3)
            )
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        replay_skipped = max(0, len(replay_candidates_all) - replay_processed)
        self.last_metrics = {
            "duration_ms": duration_ms,
            "open_trades": len(open_trades),
            "live_trades": len(live_trades),
            "replay_candidates": len(replay_candidates_all),
            "replay_processed": replay_processed,
            "replay_skipped": replay_skipped,
            "replay_backed_off": replay_backed_off,
            "replay_budget_exhausted": replay_budget_exhausted,
            "replay_duration_ms": replay_duration_ms,
            "replay_avg_ms_per_trade": int(self._replay_avg_ms_per_trade),
            "replay_budget_ms": self.max_replay_runtime_ms,
        }
        if open_trades:
            self._log_exit_replay_debug(
                "[exit-metrics] duration_ms=%s open_trades=%s live_trades=%s replay_candidates=%s replay_processed=%s replay_skipped=%s replay_backed_off=%s replay_duration_ms=%s replay_avg_ms_per_trade=%s replay_budget_ms=%s replay_budget_exhausted=%s",
                duration_ms,
                len(open_trades),
                len(live_trades),
                len(replay_candidates_all),
                replay_processed,
                replay_skipped,
                replay_backed_off,
                replay_duration_ms,
                int(self._replay_avg_ms_per_trade),
                self.max_replay_runtime_ms,
                replay_budget_exhausted,
            )
            self._maybe_log_exit_summary(
                duration_ms=duration_ms,
                open_trades=len(open_trades),
                live_trades=len(live_trades),
                replay_candidates=len(replay_candidates_all),
                replay_processed=replay_processed,
                replay_skipped=replay_skipped,
            )
        await self._warn_if_replay_slow(
            duration_ms=replay_duration_ms,
            replay_budget_ms=self.max_replay_runtime_ms,
            replay_candidates=len(replay_candidates_all),
            trade_timings=replay_trade_timings,
        )

    def _select_replay_batch(self, trades: list) -> list:
        if not trades or self.max_replay_trades_per_tick <= 0:
            return []
        limit = self._current_replay_batch_limit()
        if len(trades) <= limit:
            self._replay_cursor = 0
            return list(trades)

        selected = []
        start = self._replay_cursor % len(trades)
        for offset in range(limit):
            selected.append(trades[(start + offset) % len(trades)])
        self._replay_cursor = (start + limit) % len(trades)
        return selected

    def _current_replay_batch_limit(self) -> int:
        limit = max(1, int(self.max_replay_trades_per_tick))
        if self.max_replay_runtime_ms <= 0 or self._replay_avg_ms_per_trade <= 0:
            return limit
        budget_limit = max(1, int(self.max_replay_runtime_ms / max(self._replay_avg_ms_per_trade, 1.0)))
        return max(1, min(limit, budget_limit))

    async def _process_trade_safe(
        self,
        trade,
        now_ms: int,
        is_replay_trade: bool = False,
        trade_timings: list[dict[str, Any]] | None = None,
    ) -> None:
        started_at = time.perf_counter()
        trade_id = str(getattr(trade, "trade_id", "-"))
        try:
            await self._process_trade(trade, now_ms)
        except Exception:
            log.exception(
                "[exit-async] trade_id=%s symbol=%s unexpected error",
                getattr(trade, "trade_id", "-"),
                getattr(trade, "symbol", "-"),
            )
        finally:
            if trade_timings is not None:
                trade_timings.append(
                    {
                        "trade_id": trade_id,
                        "symbol": str(getattr(trade, "symbol", "-")),
                        "duration_ms": int((time.perf_counter() - started_at) * 1000),
                        "is_replay_trade": bool(is_replay_trade),
                        "candles": int(self._last_replay_candles_by_trade_id.pop(trade_id, 0) or 0),
                    }
                )

    async def _process_replay_trade_safe(
        self,
        trade,
        now_ms: int,
        replay_candle_cache: dict[tuple[str, str], dict[str, Any]],
    ) -> dict[str, Any]:
        metrics = self._new_replay_trade_metrics(trade)
        started_at = time.perf_counter()
        current = trade
        try:
            current, metrics = await self._process_replay_trade(
                trade,
                now_ms,
                replay_candle_cache,
                metrics,
            )
        except Exception as exc:
            metrics["error"] = f"{type(exc).__name__}:{exc}"
            log.exception(
                "[exit-async] replay trade_id=%s symbol=%s unexpected error",
                getattr(trade, "trade_id", "-"),
                getattr(trade, "symbol", "-"),
            )
        finally:
            metrics["total_ms"] = int((time.perf_counter() - started_at) * 1000)
            if self._status(current) == TradeStatus.OPEN.value:
                current = await self._apply_replay_backoff_if_needed(current, metrics, now_ms)
            metrics["total_ms"] = int((time.perf_counter() - started_at) * 1000)
            self._finalize_replay_metrics(metrics, trade, current)
            self._log_replay_trade_metrics(metrics)
        return metrics

    async def _process_replay_trade(
        self,
        trade,
        now_ms: int,
        replay_candle_cache: dict[tuple[str, str], dict[str, Any]],
        metrics: dict[str, Any],
    ):
        invalid_trade = await self._quarantine_invalid_symbol_if_needed(trade, now_ms)
        if invalid_trade is not None:
            metrics["cache_update_ms"] += self._timed_cache_update(invalid_trade)
            metrics["cache_updated"] = True
            metrics["mongo_write_ms"] += await self._timed_persist_trade(invalid_trade)
            metrics["mongo_updated"] = True
            return invalid_trade, metrics

        current = await self._replay_closed_candles_from_cache_if_available(
            trade,
            now_ms,
            replay_candle_cache,
            metrics,
        )
        if current is None:
            lookup_started_at = time.perf_counter()
            price = self.price_provider.get_price(trade.symbol)
            metrics["price_lookup_ms"] += int((time.perf_counter() - lookup_started_at) * 1000)
            if price is None:
                log.debug(
                    "[exit-async] no cached replay candles or ws price trade_id=%s symbol=%s",
                    getattr(trade, "trade_id", "-"),
                    getattr(trade, "symbol", "-"),
                )
                return trade, metrics

            current = trade
            rule_started_at = time.perf_counter()
            current = await self._apply_price(current, float(price), now_ms)
            metrics["exit_rule_ms"] += int((time.perf_counter() - rule_started_at) * 1000)

        if self._should_persist_replay_result(trade, current, metrics):
            metrics["cache_update_ms"] += self._timed_cache_update(current)
            metrics["cache_updated"] = True
            metrics["mongo_write_ms"] += await self._timed_persist_trade(current)
            metrics["mongo_updated"] = True
        return current, metrics

    def _new_replay_trade_metrics(self, trade) -> dict[str, Any]:
        status = self._status(trade)
        return {
            "trade_id": str(getattr(trade, "trade_id", "-")),
            "symbol": str(getattr(trade, "symbol", "-")),
            "tf": str(getattr(trade, "tf_entry", None) or self.reconcile_tf),
            "is_replay_trade": True,
            "start_status": status,
            "end_status": status,
            "start_tp_hit_count": int(getattr(trade, "tp_hit_count", 0) or 0),
            "end_tp_hit_count": int(getattr(trade, "tp_hit_count", 0) or 0),
            "start_qty_remaining": self._float_or_none(getattr(trade, "qty_remaining", None)),
            "end_qty_remaining": self._float_or_none(getattr(trade, "qty_remaining", None)),
            "start_soft_stop_activated_at": getattr(trade, "soft_stop_activated_at", None),
            "end_soft_stop_activated_at": getattr(trade, "soft_stop_activated_at", None),
            "start_soft_stop_current_pct": self._float_or_none(getattr(trade, "soft_stop_current_pct", None)),
            "end_soft_stop_current_pct": self._float_or_none(getattr(trade, "soft_stop_current_pct", None)),
            "candles": 0,
            "total_ms": 0,
            "fetch_candles_ms": 0,
            "price_lookup_ms": 0,
            "indicator_calc_ms": 0,
            "exit_rule_ms": 0,
            "mongo_write_ms": 0,
            "telegram_ms": 0,
            "binance_sync_ms": 0,
            "cache_update_ms": 0,
            "cache_updated": False,
            "mongo_updated": False,
            "source": "cache",
            "error": None,
            "trade_status_changed": False,
            "tp_hit": False,
            "sl_hit": False,
            "soft_stop_activated": False,
            "soft_stop_moved": False,
            "soft_stop_closed": False,
            "trade_closed": False,
            "state_changed": False,
            "exit_reason": getattr(trade, "exit_reason", None),
        }

    async def _load_cycle_replay_candle_cache(
        self,
        replay_trades: list,
        now_ms: int,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        if not replay_trades or self.bars_repo is None:
            return {}

        grouped: dict[tuple[str, str], int] = defaultdict(int)
        for trade in replay_trades:
            grouped[(str(getattr(trade, "symbol", "")).upper(), self.reconcile_tf)] += 1

        cache: dict[tuple[str, str], dict[str, Any]] = {}
        to_ts = self._floor_to_closed_minute(now_ms)
        for (symbol, tf), trade_count in grouped.items():
            started_at = time.perf_counter()
            rows: list[Any] = []
            try:
                rows = await self.bars_repo.get_tail(
                    symbol=symbol,
                    tf=tf,
                    n=self.replay_cache_tail_bars,
                    features_ver=self.features_ver,
                    require_features_ok=False,
                )
            except Exception as exc:
                log.warning(
                    "[exit-replay-cache] failed symbol=%s tf=%s error=%s:%s",
                    symbol,
                    tf,
                    type(exc).__name__,
                    exc,
                )
            load_ms = int((time.perf_counter() - started_at) * 1000)
            candles = [
                self._normalize_kline(row)
                for row in rows
                if int(getattr(row, "close_time", 0) or (row.get("close_time", 0) if isinstance(row, dict) else 0)) <= to_ts
            ]
            candles.sort(key=lambda item: int(item["close_time"]))
            cache[(symbol, tf)] = {
                "candles": candles,
                "load_ms": load_ms,
                "trade_count": max(1, int(trade_count)),
            }
            log.debug(
                "[exit-replay-cache] symbol=%s tf=%s rows=%s duration_ms=%s trades=%s",
                symbol,
                tf,
                len(candles),
                load_ms,
                trade_count,
            )
        return cache

    async def _replay_closed_candles_from_cache_if_available(
        self,
        trade,
        now_ms: int,
        replay_candle_cache: dict[tuple[str, str], dict[str, Any]],
        metrics: dict[str, Any],
    ):
        key = (str(getattr(trade, "symbol", "")).upper(), self.reconcile_tf)
        entry = replay_candle_cache.get(key)
        if not entry:
            metrics["source"] = "price_provider"
            return None

        started_at = time.perf_counter()
        candles_all = list(entry.get("candles") or [])
        load_share_ms = int((entry.get("load_ms") or 0) / max(1, int(entry.get("trade_count") or 1)))
        from_ts = self._replay_from_ts(trade)
        to_ts = self._floor_to_closed_minute(now_ms)
        if from_ts <= 0 or from_ts >= to_ts:
            metrics["fetch_candles_ms"] += load_share_ms + int((time.perf_counter() - started_at) * 1000)
            return None

        candles = [
            candle for candle in candles_all
            if int(candle["close_time"]) > from_ts and int(candle["close_time"]) <= to_ts
        ]
        metrics["fetch_candles_ms"] += load_share_ms + int((time.perf_counter() - started_at) * 1000)
        if not candles:
            metrics["source"] = "price_provider"
            return None

        current = trade
        side = current.side.value if hasattr(current.side, "value") else str(current.side)
        rule_started_at = time.perf_counter()
        processed_candles = 0

        for candle in candles:
            if self._status(current) == TradeStatus.CLOSED.value:
                break

            processed_candles += 1
            candle_close_time = int(candle["close_time"])
            high_price = float(candle["high"])
            low_price = float(candle["low"])

            if side == "LONG":
                current = await self._apply_price(current, high_price, candle_close_time)
                if self._status(current) != TradeStatus.CLOSED.value:
                    current = await self._apply_price(current, low_price, candle_close_time)
            else:
                current = await self._apply_price(current, low_price, candle_close_time)
                if self._status(current) != TradeStatus.CLOSED.value:
                    current = await self._apply_price(current, high_price, candle_close_time)

            if self._status(current) != TradeStatus.CLOSED.value:
                current = replace(
                    current,
                    exit_last_check_at=candle_close_time,
                    last_replayed_candle_close=candle_close_time,
                )

        metrics["exit_rule_ms"] += int((time.perf_counter() - rule_started_at) * 1000)
        metrics["candles"] = processed_candles
        metrics["last_replayed_candle_close"] = getattr(current, "last_replayed_candle_close", None)
        self._last_replay_candles_by_trade_id[str(getattr(current, "trade_id", "-"))] = processed_candles
        self._log_exit_replay_debug(
            "[exit-async] replayed closed candles trade_id=%s symbol=%s candles=%d status=%s source=cache",
            getattr(current, "trade_id", "-"),
            getattr(current, "symbol", "-"),
            processed_candles,
            self._status(current),
        )
        return current

    def _replay_from_ts(self, trade) -> int:
        last_replayed = getattr(trade, "last_replayed_candle_close", None)
        if last_replayed:
            return int(last_replayed)
        return int(getattr(trade, "exit_last_check_at", 0) or getattr(trade, "opened_at", 0) or 0)

    def _timed_cache_update(self, trade) -> int:
        started_at = time.perf_counter()
        self._update_cache(trade)
        return int((time.perf_counter() - started_at) * 1000)

    async def _timed_persist_trade(self, trade) -> int:
        started_at = time.perf_counter()
        await self._persist_trade(trade)
        return int((time.perf_counter() - started_at) * 1000)

    def _should_persist_replay_result(self, before, after, metrics: dict[str, Any]) -> bool:
        if metrics.get("error") is not None:
            return True
        if int(metrics.get("candles", 0) or 0) > 0:
            return True
        return self._has_meaningful_trade_state_change(before, after)

    def _has_meaningful_trade_state_change(self, before, after) -> bool:
        if self._status(before) != self._status(after):
            return True
        comparable_fields = (
            "tp_hit_count",
            "qty_remaining",
            "remaining_pct",
            "closed_at",
            "close_price",
            "exit_reason",
            "realized_pnl_usd",
            "sl_price",
            "soft_stop_activated_at",
            "soft_stop_current_pct",
            "soft_stop_trigger_price",
            "exchange_position_confirmed_flat",
            "cleanup_completed",
        )
        return any(getattr(before, field, None) != getattr(after, field, None) for field in comparable_fields)

    def _finalize_replay_metrics(self, metrics: dict[str, Any], before, after) -> None:
        start_status = self._status(before)
        end_status = self._status(after)
        start_tp = int(getattr(before, "tp_hit_count", 0) or 0)
        end_tp = int(getattr(after, "tp_hit_count", 0) or 0)
        start_soft_pct = self._float_or_none(getattr(before, "soft_stop_current_pct", None))
        end_soft_pct = self._float_or_none(getattr(after, "soft_stop_current_pct", None))
        start_soft_activated_at = getattr(before, "soft_stop_activated_at", None)
        end_soft_activated_at = getattr(after, "soft_stop_activated_at", None)
        exit_reason = str(getattr(after, "exit_reason", "") or "")

        metrics["end_status"] = end_status
        metrics["end_tp_hit_count"] = end_tp
        metrics["end_qty_remaining"] = self._float_or_none(getattr(after, "qty_remaining", None))
        metrics["end_soft_stop_activated_at"] = end_soft_activated_at
        metrics["end_soft_stop_current_pct"] = end_soft_pct
        metrics["exit_reason"] = exit_reason or None
        metrics["trade_status_changed"] = start_status != end_status
        metrics["tp_hit"] = end_tp > start_tp
        metrics["sl_hit"] = end_status == TradeStatus.CLOSED.value and exit_reason == "STOP_LOSS_HIT"
        metrics["soft_stop_activated"] = start_soft_activated_at is None and end_soft_activated_at is not None
        metrics["soft_stop_moved"] = (
            start_soft_pct is not None
            and end_soft_pct is not None
            and abs(end_soft_pct - start_soft_pct) > 1e-12
        )
        metrics["soft_stop_closed"] = end_status == TradeStatus.CLOSED.value and exit_reason in {
            "SOFT_TRAILING_STOP",
            "SOFT_STOP_IMMEDIATE",
        }
        metrics["trade_closed"] = start_status != TradeStatus.CLOSED.value and end_status == TradeStatus.CLOSED.value
        metrics["state_changed"] = self._has_meaningful_trade_state_change(before, after)

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _is_replay_backed_off(self, trade, now_ms: int) -> bool:
        next_replay_at = getattr(trade, "next_replay_at", None)
        if next_replay_at is None:
            return False
        try:
            return int(next_replay_at) > int(now_ms)
        except (TypeError, ValueError):
            return False

    async def _apply_replay_backoff_if_needed(self, trade, metrics: dict[str, Any], now_ms: int):
        total_ms = int(metrics.get("total_ms", 0) or 0)
        if total_ms <= self.slow_replay_trade_ms:
            if int(getattr(trade, "replay_slow_count", 0) or 0) <= 0 or getattr(trade, "next_replay_at", None) is None:
                return trade
            current = replace(trade, replay_slow_count=0, next_replay_at=None)
            self._update_cache(current)
            metrics["cache_updated"] = True
            await self._persist_trade(current)
            metrics["mongo_updated"] = True
            return current

        slow_count = int(getattr(trade, "replay_slow_count", 0) or 0) + 1
        delay_ms = 60_000 if slow_count == 1 else 300_000 if slow_count == 2 else 900_000
        next_replay_at = int(now_ms) + delay_ms
        current = replace(trade, replay_slow_count=slow_count, next_replay_at=next_replay_at)
        log.warning(
            "[exit-replay-backoff] trade_id=%s slow_count=%s next_replay_at=%s total_ms=%s threshold_ms=%s",
            getattr(trade, "trade_id", "-"),
            slow_count,
            next_replay_at,
            total_ms,
            self.slow_replay_trade_ms,
        )
        self._update_cache(current)
        metrics["cache_updated"] = True
        await self._persist_trade(current)
        metrics["mongo_updated"] = True
        return current

    def _log_replay_trade_metrics(self, metrics: dict[str, Any]) -> None:
        self._record_replay_summary_counters(metrics)
        if int(metrics.get("candles", 0) or 0) > 0:
            log.info(
                "[exit-replay-caught-up] trade_id=%s symbol=%s tf=%s candles=%s last_replayed_candle_close=%s",
                metrics.get("trade_id"),
                metrics.get("symbol"),
                metrics.get("tf"),
                metrics.get("candles", 0),
                metrics.get("last_replayed_candle_close"),
            )

        if not self._should_log_replay_trade_metrics(metrics):
            self._summary_counters["noop_skipped"] += 1
            return

        log_fn = log.warning if self._is_replay_trade_slow(metrics) else log.info
        log_fn(
            "[exit-replay-trade-metrics] trade_id=%s symbol=%s tf=%s candles=%s total_ms=%s fetch_candles_ms=%s price_lookup_ms=%s indicator_calc_ms=%s exit_rule_ms=%s mongo_write_ms=%s telegram_ms=%s binance_sync_ms=%s cache_update_ms=%s status_changed=%s tp_hit=%s sl_hit=%s trade_closed=%s source=%s error=%s",
            metrics.get("trade_id"),
            metrics.get("symbol"),
            metrics.get("tf"),
            metrics.get("candles", 0),
            metrics.get("total_ms", 0),
            metrics.get("fetch_candles_ms", 0),
            metrics.get("price_lookup_ms", 0),
            metrics.get("indicator_calc_ms", 0),
            metrics.get("exit_rule_ms", 0),
            metrics.get("mongo_write_ms", 0),
            metrics.get("telegram_ms", 0),
            metrics.get("binance_sync_ms", 0),
            metrics.get("cache_update_ms", 0),
            metrics.get("trade_status_changed", False),
            metrics.get("tp_hit", False),
            metrics.get("sl_hit", False),
            metrics.get("trade_closed", False),
            metrics.get("source"),
            metrics.get("error"),
        )
        self._summary_counters["events_logged"] += 1

    def _should_log_replay_trade_metrics(self, metrics: dict[str, Any]) -> bool:
        if self.debug_exit_replay or self.log_noop_replay:
            return True
        if metrics.get("error") is not None:
            return True
        if int(metrics.get("total_ms", 0) or 0) >= self.replay_metrics_min_ms:
            return True
        if int(metrics.get("candles", 0) or 0) > 0:
            return True
        meaningful_flags = (
            "trade_status_changed",
            "tp_hit",
            "sl_hit",
            "soft_stop_activated",
            "soft_stop_moved",
            "soft_stop_closed",
            "trade_closed",
            "cache_updated",
            "mongo_updated",
        )
        return any(bool(metrics.get(flag)) for flag in meaningful_flags)

    def _is_replay_trade_slow(self, metrics: dict[str, Any]) -> bool:
        total_ms = int(metrics.get("total_ms", 0) or 0)
        return self.max_replay_runtime_ms > 0 and total_ms >= self.max_replay_runtime_ms

    def _record_replay_summary_counters(self, metrics: dict[str, Any]) -> None:
        if metrics.get("error") is not None:
            self._summary_counters["errors"] += 1
        if bool(metrics.get("tp_hit")):
            self._summary_counters["tp_hits"] += 1
        if bool(metrics.get("sl_hit")):
            self._summary_counters["sl_hits"] += 1
        if bool(metrics.get("soft_stop_activated")):
            self._summary_counters["soft_stop_activated"] += 1
        if bool(metrics.get("soft_stop_moved")):
            self._summary_counters["soft_stop_moved"] += 1
        if bool(metrics.get("soft_stop_closed")):
            self._summary_counters["soft_stop_closed"] += 1
        if bool(metrics.get("trade_closed")):
            self._summary_counters["trades_closed"] += 1
        if bool(metrics.get("mongo_updated")):
            self._summary_counters["mongo_updates"] += 1
        if int(metrics.get("telegram_ms", 0) or 0) > 0:
            self._summary_counters["telegram_messages"] += 1
        if int(metrics.get("binance_sync_ms", 0) or 0) > 0:
            self._summary_counters["binance_syncs"] += 1
        if self._is_replay_trade_slow(metrics):
            self._summary_counters["slow_replays"] += 1

    async def _process_trade(self, trade, now_ms: int) -> None:
        async with self._sem:
            invalid_trade = await self._quarantine_invalid_symbol_if_needed(trade, now_ms)
            if invalid_trade is not None:
                self._update_cache(invalid_trade)
                await self._persist_trade(invalid_trade)
                return

            trade = await self._queue_entry_notification_recovery_if_needed(trade, now_ms)

            if str(getattr(trade, "mode", "")) == "live" and self.live_sync_service is not None:
                current = await self.live_sync_service.sync_trade(
                    trade,
                    now_ms=now_ms,
                    reason="poll",
                )
                if self._status(current) == TradeStatus.CLOSED.value and str(getattr(current, "exit_reason", "")) == "STOP_LOSS_HIT":
                    await self._apply_loss_cooldown_if_needed(current, now_ms)
                self._update_cache(current)
                await self._persist_trade(current)
                return

            current = await self._replay_closed_candles_if_available(trade, now_ms)
            if current is not None:
                self._update_cache(current)
                await self._persist_trade(current)
                return

            symbol = trade.symbol
            price = self.price_provider.get_price(symbol)

            if price is None:
                log.debug(
                    "[exit-async] no ws price yet trade_id=%s symbol=%s",
                    trade.trade_id,
                    symbol,
                )
                return

            current = trade
            current = await self._apply_price(current, price, now_ms)

            if self._status(current) != TradeStatus.CLOSED.value:
                current = replace(current, exit_last_check_at=now_ms)

            self._update_cache(current)
            await self._persist_trade(current)

    async def _quarantine_invalid_symbol_if_needed(self, trade, now_ms: int):
        validation = validate_binance_usdt_perp_symbol(getattr(trade, "symbol", ""))
        if validation.valid:
            return None

        current = replace(
            trade,
            status=TradeStatus.ERROR_INVALID_SYMBOL,
            closed_at=now_ms,
            exit_last_check_at=now_ms,
            exit_reason="ERROR_INVALID_SYMBOL",
            exchange_sync_status="invalid_symbol",
            exchange_sync_error=validation.reason,
            qty_remaining=0.0,
            remaining_pct=0.0,
        )
        log.warning(
            "[exit-async] INVALID_SYMBOL_SKIPPED trade_id=%s symbol=%s user=%s reason=%s action=quarantined",
            getattr(trade, "trade_id", "-"),
            validation.normalized_symbol or validation.symbol,
            getattr(trade, "user_id", "-"),
            validation.reason,
        )
        await self.trade_events_repo.add_event(
            trade_id=getattr(trade, "trade_id", f"invalid:{validation.normalized_symbol}"),
            event_type="INVALID_SYMBOL_SKIPPED",
            ts=now_ms,
            symbol=validation.normalized_symbol or validation.symbol,
            user_id=str(getattr(trade, "user_id", "system") or "system"),
            mode=str(getattr(trade, "mode", "sandbox") or "sandbox"),
            payload={
                "reason": "invalid_symbol",
                "invalid_reason": validation.reason,
                "action": "quarantined_without_binance_call",
                "status": TradeStatus.ERROR_INVALID_SYMBOL.value,
            },
        )
        return current

    async def _queue_entry_notification_recovery_if_needed(self, trade, now_ms: int):
        if not self._needs_entry_notification_recovery(trade, now_ms):
            return trade

        payload = self._build_entry_notification_recovery_payload(trade)
        event_created = True
        try:
            if hasattr(self.trade_events_repo, "add_event_once"):
                _, event_created = await self.trade_events_repo.add_event_once(
                    idempotency_key=f"entry-open-recovery:{getattr(trade, 'trade_id', '')}",
                    trade_id=str(getattr(trade, "trade_id", "")),
                    event_type="OPENED",
                    ts=now_ms,
                    symbol=str(getattr(trade, "symbol", "")),
                    user_id=str(getattr(trade, "user_id", "")),
                    mode=str(getattr(trade, "mode", "live") or "live"),
                    payload=payload,
                )
            else:
                await self.trade_events_repo.add_event(
                    trade_id=str(getattr(trade, "trade_id", "")),
                    event_type="OPENED",
                    ts=now_ms,
                    symbol=str(getattr(trade, "symbol", "")),
                    user_id=str(getattr(trade, "user_id", "")),
                    mode=str(getattr(trade, "mode", "live") or "live"),
                    payload=payload,
                )
        except Exception as exc:
            log.warning(
                "[notification-flow] entry notification recovery enqueue failed trade_id=%s symbol=%s user=%s error=%s:%s",
                getattr(trade, "trade_id", "-"),
                getattr(trade, "symbol", "-"),
                getattr(trade, "user_id", "-"),
                type(exc).__name__,
                exc,
            )
            return trade

        current = replace(trade, entry_notification_recovery_queued_at=now_ms)
        log.warning(
            "[notification-flow] trade_id=%s symbol=%s user=%s open_message_queued=True open_message_sent=False risk_warning_queued=False risk_warning_sent=False recovery=True created=%s",
            getattr(trade, "trade_id", "-"),
            getattr(trade, "symbol", "-"),
            getattr(trade, "user_id", "-"),
            event_created,
        )
        return current

    def _needs_entry_notification_recovery(self, trade, now_ms: int) -> bool:
        if self._status(trade) != TradeStatus.OPEN.value:
            return False
        if str(getattr(trade, "mode", "") or "").lower() != "live":
            return False
        if bool(getattr(trade, "entry_notification_sent", False)):
            return False
        if is_strategy_version_test_user(user_id=getattr(trade, "user_id", None)):
            return False
        if not self._is_bot_opened_trade(trade):
            return False
        opened_at = int(getattr(trade, "opened_at", 0) or 0)
        if opened_at > 0 and (int(now_ms) - opened_at) < ENTRY_NOTIFICATION_RECOVERY_DELAY_MS:
            return False
        attempts = int(getattr(trade, "entry_notification_send_attempt_count", 0) or 0)
        if ENTRY_NOTIFICATION_MAX_RECOVERY_RETRIES > 0 and attempts >= ENTRY_NOTIFICATION_MAX_RECOVERY_RETRIES:
            log.warning(
                "[notification-flow] entry notification recovery max retries reached trade_id=%s symbol=%s user=%s attempts=%s",
                getattr(trade, "trade_id", "-"),
                getattr(trade, "symbol", "-"),
                getattr(trade, "user_id", "-"),
                attempts,
            )
            return False
        queued_at = int(getattr(trade, "entry_notification_recovery_queued_at", 0) or 0)
        if queued_at > 0 and (int(now_ms) - queued_at) < ENTRY_NOTIFICATION_RECOVERY_RETRY_MS:
            return False
        return True

    def _is_bot_opened_trade(self, trade) -> bool:
        engine = str(getattr(trade, "execution_engine", "") or "").lower()
        if engine in {"binance_futures_live", "paper"}:
            return True
        return bool(getattr(trade, "model_id", None) or getattr(trade, "entry_bar_close_time", None))

    def _build_entry_notification_recovery_payload(self, trade) -> dict[str, Any]:
        exit_profile = getattr(trade, "exit_profile", None) or {}
        protection_details = getattr(trade, "protection_details", None) or {}
        return {
            "side": getattr(getattr(trade, "side", None), "value", str(getattr(trade, "side", "LONG"))),
            "entry_price": getattr(trade, "entry_price", None),
            "qty": getattr(trade, "qty", None),
            "stake_usd": getattr(trade, "stake_usd", None),
            "sl_price": getattr(trade, "sl_price", None),
            "tf_entry": getattr(trade, "tf_entry", None),
            "model_id": getattr(trade, "model_id", None),
            "rule_id": getattr(trade, "model_id", None),
            "strategy_version": getattr(trade, "strategy_version", "v1"),
            "subscription_type": getattr(trade, "subscription_type", None),
            "signal_level": getattr(trade, "signal_level", None),
            "signal_score": getattr(trade, "signal_score", None),
            "position_size_multiplier": getattr(trade, "position_size_multiplier", 1.0),
            "exit_profile": exit_profile,
            "tp_step_pct": exit_profile.get("tp_step_pct") if isinstance(exit_profile, dict) else None,
            "soft_stop_enabled": getattr(trade, "soft_stop_enabled", False),
            "soft_stop_activation_pct": exit_profile.get("soft_stop_activation_pct") if isinstance(exit_profile, dict) else None,
            "soft_stop_start_pct": exit_profile.get("soft_stop_start_pct") if isinstance(exit_profile, dict) else None,
            "soft_stop_increment_pct": exit_profile.get("soft_stop_increment_pct") if isinstance(exit_profile, dict) else None,
            "soft_stop_increment_interval_seconds": (
                exit_profile.get("soft_stop_increment_interval_seconds") if isinstance(exit_profile, dict) else None
            ),
            "exchange_safety_sl_price": getattr(trade, "exchange_safety_sl_price", None),
            "entry_bar_close_time": getattr(trade, "entry_bar_close_time", None),
            "entry_indicators": getattr(trade, "entry_indicators", None),
            "signal_debug": getattr(trade, "signal_debug", None),
            "leverage": getattr(trade, "leverage", None),
            "exchange_name": getattr(trade, "exchange_name", None),
            "exchange_entry_order_id": getattr(trade, "exchange_entry_order_id", None),
            "exchange_entry_status": getattr(trade, "exchange_entry_status", None),
            "protection_status": getattr(trade, "protection_status", "protected"),
            "protection_error": getattr(trade, "protection_error", None),
            "tp_count_original": protection_details.get("tp_count_original") if isinstance(protection_details, dict) else None,
            "tp_count_forced_by_small_margin": bool(
                protection_details.get("tp_count_forced_by_small_margin") if isinstance(protection_details, dict) else False
            ),
            "tp_count_force_reason": protection_details.get("tp_count_force_reason") if isinstance(protection_details, dict) else None,
            "entry_margin_usdt": protection_details.get("entry_margin_usdt") if isinstance(protection_details, dict) else None,
            "position_notional": protection_details.get("position_notional") if isinstance(protection_details, dict) else None,
            "tp_count": getattr(trade, "tp_count", 0),
            "tp_levels": list(getattr(trade, "tp_levels", []) or []),
            "tp_close_fractions": list(getattr(trade, "tp_close_fractions", []) or []),
            "tp_plan": list(getattr(trade, "tp_plan", []) or []),
            "tp_order_ids": list(getattr(trade, "tp_order_ids", []) or []),
            "tp_algo_ids": list(getattr(trade, "tp_algo_ids", []) or []),
            "recovered_notification": True,
            "recovery_note": "Повідомлення відновлено після перевірки стану біржі",
        }

    def _update_cache(self, trade) -> None:
        self.active_trades_cache.update_from_trade(trade)

    async def _persist_trade(self, trade) -> None:
        try:
            await self.trades_repo.update_trade(trade)
        except Exception as exc:
            log.warning(
                "[exit-async] trade persistence skipped trade_id=%s symbol=%s error=%s:%s",
                getattr(trade, "trade_id", "-"),
                getattr(trade, "symbol", "-"),
                type(exc).__name__,
                exc,
            )

    async def _replay_closed_candles_if_available(self, trade, now_ms: int):
        if self.exchange is None:
            return None

        from_ts = int(getattr(trade, "exit_last_check_at", 0) or getattr(trade, "opened_at", 0) or 0)
        to_ts = self._floor_to_closed_minute(now_ms)
        if from_ts <= 0 or from_ts >= to_ts:
            return None

        try:
            candles = await self._fetch_closed_candles(
                symbol=str(trade.symbol),
                start_ms=from_ts,
                end_ms=to_ts,
            )
        except Exception:
            log.exception(
                "[exit-async] candle replay failed trade_id=%s symbol=%s; falling back to ws price",
                getattr(trade, "trade_id", "-"),
                getattr(trade, "symbol", "-"),
            )
            return None
        if not candles:
            return None

        current = trade
        side = current.side.value if hasattr(current.side, "value") else str(current.side)

        for candle in candles:
            if self._status(current) == TradeStatus.CLOSED.value:
                break

            candle_close_time = int(candle["close_time"])
            high_price = float(candle["high"])
            low_price = float(candle["low"])

            if side == "LONG":
                current = await self._apply_price(current, high_price, candle_close_time)
                if self._status(current) != TradeStatus.CLOSED.value:
                    current = await self._apply_price(current, low_price, candle_close_time)
            else:
                current = await self._apply_price(current, low_price, candle_close_time)
                if self._status(current) != TradeStatus.CLOSED.value:
                    current = await self._apply_price(current, high_price, candle_close_time)

            if self._status(current) != TradeStatus.CLOSED.value:
                current = replace(current, exit_last_check_at=candle_close_time)

        self._last_replay_candles_by_trade_id[str(getattr(current, "trade_id", "-"))] = len(candles)
        self._log_exit_replay_debug(
            "[exit-async] replayed closed candles trade_id=%s symbol=%s candles=%d status=%s",
            getattr(current, "trade_id", "-"),
            getattr(current, "symbol", "-"),
            len(candles),
            self._status(current),
        )
        return current

    def _log_exit_replay_debug(self, message: str, *args: Any) -> None:
        if self.debug_exit_replay:
            log.info(message, *args)
        else:
            log.debug(message, *args)

    def _maybe_log_exit_summary(
        self,
        *,
        duration_ms: int,
        open_trades: int,
        live_trades: int,
        replay_candidates: int,
        replay_processed: int,
        replay_skipped: int,
    ) -> None:
        self._summary_count += 1
        self._summary_duration_total_ms += int(duration_ms)
        self._summary_max_duration_ms = max(self._summary_max_duration_ms, int(duration_ms))

        now = time.monotonic()
        if self._last_exit_summary_at and (now - self._last_exit_summary_at) < self.exit_summary_interval_seconds:
            return

        avg_duration_ms = int(self._summary_duration_total_ms / max(self._summary_count, 1))
        counters = defaultdict(int, self._summary_counters)
        log.info(
            "[exit-summary] open_trades=%s live_trades=%s replay_candidates=%s replay_processed=%s replay_skipped=%s avg_duration_ms=%s max_duration_ms=%s events_logged=%s noop_skipped=%s tp_hits=%s sl_hits=%s soft_stop_activated=%s soft_stop_moved=%s soft_stop_closed=%s trades_closed=%s mongo_updates=%s telegram_messages=%s binance_syncs=%s slow_replays=%s errors=%s",
            open_trades,
            live_trades,
            replay_candidates,
            replay_processed,
            replay_skipped,
            avg_duration_ms,
            self._summary_max_duration_ms,
            counters["events_logged"],
            counters["noop_skipped"],
            counters["tp_hits"],
            counters["sl_hits"],
            counters["soft_stop_activated"],
            counters["soft_stop_moved"],
            counters["soft_stop_closed"],
            counters["trades_closed"],
            counters["mongo_updates"],
            counters["telegram_messages"],
            counters["binance_syncs"],
            counters["slow_replays"],
            counters["errors"],
        )
        self._last_exit_summary_at = now
        self._summary_count = 0
        self._summary_duration_total_ms = 0
        self._summary_max_duration_ms = 0
        self._summary_counters.clear()

    async def _warn_if_replay_slow(
        self,
        *,
        duration_ms: int,
        replay_budget_ms: int,
        replay_candidates: int,
        trade_timings: list[dict[str, Any]],
    ) -> None:
        if duration_ms <= 0 or replay_budget_ms <= 0 or duration_ms <= replay_budget_ms:
            return

        slowest = self._slowest_replay_timing(trade_timings)
        log.warning(
            "[exit-replay-slow] duration_ms=%s replay_budget_ms=%s trade_id=%s symbol=%s tf=%s candles=%s total_ms=%s fetch_candles_ms=%s price_lookup_ms=%s exit_rule_ms=%s mongo_write_ms=%s telegram_ms=%s binance_sync_ms=%s replay_candidates=%s",
            duration_ms,
            replay_budget_ms,
            slowest.get("trade_id"),
            slowest.get("symbol"),
            slowest.get("tf"),
            slowest.get("candles", 0),
            slowest.get("total_ms", slowest.get("duration_ms", 0)),
            slowest.get("fetch_candles_ms", 0),
            slowest.get("price_lookup_ms", 0),
            slowest.get("exit_rule_ms", 0),
            slowest.get("mongo_write_ms", 0),
            slowest.get("telegram_ms", 0),
            slowest.get("binance_sync_ms", 0),
            replay_candidates,
        )
        await self._alert_if_replay_critically_slow(
            duration_ms=duration_ms,
            replay_budget_ms=replay_budget_ms,
            replay_candidates=replay_candidates,
            slowest=slowest,
        )

    def _slowest_replay_timing(self, trade_timings: list[dict[str, Any]]) -> dict[str, Any]:
        replay_timings = [row for row in trade_timings if row.get("is_replay_trade")]
        if not replay_timings:
            return {"trade_id": None, "symbol": None, "candles": 0, "duration_ms": 0}
        return max(replay_timings, key=lambda row: int(row.get("duration_ms", 0) or 0))

    async def _alert_if_replay_critically_slow(
        self,
        *,
        duration_ms: int,
        replay_budget_ms: int,
        replay_candidates: int,
        slowest: dict[str, Any],
    ) -> None:
        now = time.monotonic()
        while self._slow_replay_events and (now - self._slow_replay_events[0]) > self.slow_replay_alert_window_seconds:
            self._slow_replay_events.popleft()
        self._slow_replay_events.append(now)

        critical = duration_ms > self.slow_replay_critical_ms or len(self._slow_replay_events) >= self.slow_replay_alert_count
        if not critical:
            return

        log.critical(
            "[exit-replay-critical] duration_ms=%s replay_budget_ms=%s slow_count_window=%s trade_id=%s symbol=%s replay_candidates=%s",
            duration_ms,
            replay_budget_ms,
            len(self._slow_replay_events),
            slowest.get("trade_id"),
            slowest.get("symbol"),
            replay_candidates,
        )
        if self.admin_alert_service is None:
            return
        await self.admin_alert_service.send_alert(
            alert_key="exit_replay_too_slow",
            component="exit-replay",
            severity="CRITICAL",
            error="Exit replay is too slow and may delay entries/notifications",
            action_taken="kept runtime alive; review replay batch size/API latency",
            symbol=slowest.get("symbol"),
            details={
                "duration_ms": duration_ms,
                "replay_budget_ms": replay_budget_ms,
                "slow_count_5m": len(self._slow_replay_events),
                "trade_id": slowest.get("trade_id"),
                "candles": slowest.get("candles", 0),
                "replay_candidates": replay_candidates,
            },
        )

    async def _fetch_closed_candles(
        self,
        *,
        symbol: str,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        all_rows: list[dict[str, Any]] = []
        cursor = int(start_ms)

        while cursor < end_ms:
            rows = await self._fetch_klines_page(
                symbol=symbol,
                tf=self.reconcile_tf,
                start_ms=cursor,
                end_ms=end_ms,
                limit=self.max_bars_per_request,
            )
            if not rows:
                break

            normalized = [self._normalize_kline(row) for row in rows]
            normalized = [
                row for row in normalized
                if row["close_time"] > start_ms and row["close_time"] <= end_ms
            ]
            if not normalized:
                break

            all_rows.extend(normalized)
            last_close = int(normalized[-1]["close_time"])
            next_cursor = last_close + 1
            if next_cursor <= cursor:
                break
            cursor = next_cursor

            if len(rows) < self.max_bars_per_request:
                break

        return all_rows

    async def _fetch_klines_page(
        self,
        *,
        symbol: str,
        tf: str,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> list[Any]:
        method_names = ("get_klines", "fetch_klines", "get_futures_klines")

        for method_name in method_names:
            method = getattr(self.exchange, method_name, None)
            if not callable(method):
                continue

            call_attempts = (
                lambda: method(symbol=symbol, tf=tf, start_ms=start_ms, end_ms=end_ms, limit=limit),
                lambda: method(symbol=symbol, interval=tf, start_time=start_ms, end_time=end_ms, limit=limit),
                lambda: method(symbol, tf, start_ms, end_ms, limit),
            )
            for call in call_attempts:
                try:
                    return await self._maybe_await(call())
                except TypeError:
                    continue

        raise RuntimeError("No supported kline fetch method found on exchange.")

    def _normalize_kline(self, row) -> dict[str, float | int]:
        if hasattr(row, "open_time"):
            return {
                "open_time": int(row.open_time),
                "close_time": int(row.close_time),
                "open": float(row.o),
                "high": float(row.h),
                "low": float(row.l),
                "close": float(row.c),
            }

        if isinstance(row, (list, tuple)):
            open_time = int(row[0])
            close_time = int(row[6]) if len(row) > 6 else open_time + 60_000
            return {
                "open_time": open_time,
                "close_time": close_time,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
            }

        if isinstance(row, dict):
            return {
                "open_time": int(row.get("open_time") or row.get("openTime") or 0),
                "close_time": int(row.get("close_time") or row.get("closeTime") or 0),
                "open": float(row.get("open") or row.get("o")),
                "high": float(row.get("high") or row.get("h")),
                "low": float(row.get("low") or row.get("l")),
                "close": float(row.get("close") or row.get("c")),
            }

        raise TypeError(f"Unsupported kline format: {type(row)}")

    @staticmethod
    def _floor_to_closed_minute(now_ms: int) -> int:
        minute_ms = 60_000
        return (int(now_ms) // minute_ms) * minute_ms - 1

    async def _apply_price(self, trade, price: float, now_ms: int):
        current = trade
        side = trade.side.value if hasattr(trade.side, "value") else str(trade.side)

        if side == "LONG":
            current, tp_triggered = await self._apply_long_tp_logic(current, price, now_ms)
            if self._status(current) == TradeStatus.CLOSED.value:
                return current

            if tp_triggered:
                return current

            if current.qty_remaining > 0 and price <= current.sl_price:
                current = await self._maybe_await(self.broker.close_position(
                    current,
                    price=current.sl_price,
                    reason="STOP_LOSS_HIT",
                ))
                await self.trade_events_repo.add_event(
                    trade_id=current.trade_id,
                    event_type="CLOSED",
                    ts=now_ms,
                    symbol=current.symbol,
                    user_id=current.user_id,
                    mode=current.mode,
                    payload={
                        "reason": "STOP_LOSS_HIT",
                        "close_price": current.close_price,
                        "realized_pnl_usd": current.realized_pnl_usd,
                        "tp_hit_count": current.tp_hit_count,
                    },
                )
                await self._apply_loss_cooldown_if_needed(current, now_ms)
                log.info(
                    "[exit-sl-hit] trade_id=%s symbol=%s stop_price=%s reason=%s final_pnl=%s status=%s",
                    current.trade_id,
                    current.symbol,
                    current.sl_price,
                    "STOP_LOSS_HIT",
                    current.realized_pnl_usd,
                    self._status(current),
                )
                return current

            return current

        current, tp_triggered = await self._apply_short_tp_logic(current, price, now_ms)
        if self._status(current) == TradeStatus.CLOSED.value:
            return current

        if tp_triggered:
            return current

        if current.qty_remaining > 0 and price >= current.sl_price:
            current = await self._maybe_await(self.broker.close_position(
                current,
                price=current.sl_price,
                reason="STOP_LOSS_HIT",
            ))
            await self.trade_events_repo.add_event(
                trade_id=current.trade_id,
                event_type="CLOSED",
                ts=now_ms,
                symbol=current.symbol,
                user_id=current.user_id,
                mode=current.mode,
                payload={
                    "reason": "STOP_LOSS_HIT",
                    "close_price": current.close_price,
                    "realized_pnl_usd": current.realized_pnl_usd,
                    "tp_hit_count": current.tp_hit_count,
                },
            )
            await self._apply_loss_cooldown_if_needed(current, now_ms)
            log.info(
                "[exit-sl-hit] trade_id=%s symbol=%s stop_price=%s reason=%s final_pnl=%s status=%s",
                current.trade_id,
                current.symbol,
                current.sl_price,
                "STOP_LOSS_HIT",
                current.realized_pnl_usd,
                self._status(current),
            )
        return current

    async def _apply_loss_cooldown_if_needed(self, trade, now_ms: int) -> None:
        if self.cooldowns_repo is None:
            return

        realized_pnl = float(getattr(trade, "realized_pnl_usd", 0.0) or 0.0)
        if realized_pnl >= 0:
            return

        cooldown_enabled = True
        cooldown_minutes = 120
        user_uuid = None

        if self.telegram_users_repo is not None:
            tg_user = await self.telegram_users_repo.get_by_trading_user_id(str(trade.user_id))
            if tg_user:
                cooldown_enabled = bool(tg_user.get("loss_cooldown_enabled", True))
                cooldown_minutes = int(tg_user.get("loss_cooldown_minutes", 120) or 120)

        if not cooldown_enabled or cooldown_minutes <= 0:
            return

        cooldown_until = self.cooldowns_repo.utc_now() + timedelta(minutes=cooldown_minutes)

        await self.cooldowns_repo.upsert_loss_stop_cooldown(
            runtime_user_id=str(trade.user_id),
            user_id=user_uuid,
            symbol=str(trade.symbol),
            cooldown_until=cooldown_until,
        )

        await self.trade_events_repo.add_event(
            trade_id=trade.trade_id,
            event_type="LOSS_STOP_COOLDOWN_SET",
            ts=now_ms,
            symbol=trade.symbol,
            user_id=trade.user_id,
            mode=trade.mode,
            payload={
                "cooldown_minutes": cooldown_minutes,
                "cooldown_until": cooldown_until.isoformat(),
                "realized_pnl_usd": realized_pnl,
            },
        )

        log.info(
            "[exit-async] cooldown set user=%s symbol=%s until=%s pnl=%s",
            trade.user_id,
            trade.symbol,
            cooldown_until,
            realized_pnl,
        )

    async def _apply_long_tp_logic(self, trade, price: float, now_ms: int):
        current = trade
        tp_triggered = False

        while current.tp_hit_count < 3 and current.qty_remaining > 0:
            next_tp_index = current.tp_hit_count
            tp_price = current.entry_price * (1.0 + self.tp_levels[next_tp_index])

            if price < tp_price:
                break

            tp_triggered = True

            if next_tp_index == 0:
                qty_to_reduce = current.qty_remaining * 0.70
                current = await self._maybe_await(self.broker.reduce_position(
                    current,
                    qty_to_reduce=qty_to_reduce,
                    price=tp_price,
                    reason="TP1_HIT",
                ))
                current = replace(
                    current,
                    tp_hit_count=1,
                    sl_price=current.entry_price,
                )
                await self.trade_events_repo.add_event(
                    trade_id=current.trade_id,
                    event_type="TP_HIT",
                    ts=now_ms,
                    symbol=current.symbol,
                    user_id=current.user_id,
                    mode=current.mode,
                    payload={
                        "tp_index": 1,
                        "tp_price": tp_price,
                        "qty_remaining": current.qty_remaining,
                        "realized_pnl_usd": current.realized_pnl_usd,
                        "new_sl_price": current.sl_price,
                    },
                )
                log.info(
                    "[exit-tp-hit] trade_id=%s symbol=%s tp_index=%s price=%s qty_closed=%s qty_remaining=%s realized_pnl=%s",
                    current.trade_id,
                    current.symbol,
                    1,
                    tp_price,
                    qty_to_reduce,
                    current.qty_remaining,
                    current.realized_pnl_usd,
                )
                continue

            if next_tp_index == 1:
                qty_to_reduce = min(current.qty_remaining, current.qty * 0.20)
                current = await self._maybe_await(self.broker.reduce_position(
                    current,
                    qty_to_reduce=qty_to_reduce,
                    price=tp_price,
                    reason="TP2_HIT",
                ))
                current = replace(
                    current,
                    tp_hit_count=2,
                    sl_price=current.entry_price * 1.002,
                )
                await self.trade_events_repo.add_event(
                    trade_id=current.trade_id,
                    event_type="TP_HIT",
                    ts=now_ms,
                    symbol=current.symbol,
                    user_id=current.user_id,
                    mode=current.mode,
                    payload={
                        "tp_index": 2,
                        "tp_price": tp_price,
                        "qty_remaining": current.qty_remaining,
                        "realized_pnl_usd": current.realized_pnl_usd,
                        "new_sl_price": current.sl_price,
                    },
                )
                log.info(
                    "[exit-tp-hit] trade_id=%s symbol=%s tp_index=%s price=%s qty_closed=%s qty_remaining=%s realized_pnl=%s",
                    current.trade_id,
                    current.symbol,
                    2,
                    tp_price,
                    qty_to_reduce,
                    current.qty_remaining,
                    current.realized_pnl_usd,
                )
                continue

            current = await self._maybe_await(self.broker.close_position(
                current,
                price=tp_price,
                reason="TP3_HIT",
            ))
            current = replace(current, tp_hit_count=3)
            await self.trade_events_repo.add_event(
                trade_id=current.trade_id,
                event_type="CLOSED",
                ts=now_ms,
                symbol=current.symbol,
                user_id=current.user_id,
                mode=current.mode,
                payload={
                    "reason": "TP3_HIT",
                    "close_price": current.close_price,
                    "realized_pnl_usd": current.realized_pnl_usd,
                    "tp_hit_count": current.tp_hit_count,
                },
            )
            log.info(
                "[exit-trade-closed] trade_id=%s symbol=%s reason=%s final_pnl=%s exchange_net_pnl=%s status=%s",
                current.trade_id,
                current.symbol,
                "TP3_HIT",
                current.realized_pnl_usd,
                getattr(current, "exchange_net_realized_pnl_usdt", None),
                self._status(current),
            )
            break

        return current, tp_triggered

    async def _apply_short_tp_logic(self, trade, price: float, now_ms: int):
        current = trade
        tp_triggered = False

        while current.tp_hit_count < 3 and current.qty_remaining > 0:
            next_tp_index = current.tp_hit_count
            tp_price = current.entry_price * (1.0 - self.tp_levels[next_tp_index])

            if price > tp_price:
                break

            tp_triggered = True

            if next_tp_index == 0:
                qty_to_reduce = current.qty_remaining * 0.70
                current = await self._maybe_await(self.broker.reduce_position(
                    current,
                    qty_to_reduce=qty_to_reduce,
                    price=tp_price,
                    reason="TP1_HIT",
                ))
                current = replace(
                    current,
                    tp_hit_count=1,
                    sl_price=current.entry_price,
                )
                await self.trade_events_repo.add_event(
                    trade_id=current.trade_id,
                    event_type="TP_HIT",
                    ts=now_ms,
                    symbol=current.symbol,
                    user_id=current.user_id,
                    mode=current.mode,
                    payload={
                        "tp_index": 1,
                        "tp_price": tp_price,
                        "qty_remaining": current.qty_remaining,
                        "realized_pnl_usd": current.realized_pnl_usd,
                        "new_sl_price": current.sl_price,
                    },
                )
                log.info(
                    "[exit-tp-hit] trade_id=%s symbol=%s tp_index=%s price=%s qty_closed=%s qty_remaining=%s realized_pnl=%s",
                    current.trade_id,
                    current.symbol,
                    1,
                    tp_price,
                    qty_to_reduce,
                    current.qty_remaining,
                    current.realized_pnl_usd,
                )
                continue

            if next_tp_index == 1:
                qty_to_reduce = min(current.qty_remaining, current.qty * 0.20)
                current = await self._maybe_await(self.broker.reduce_position(
                    current,
                    qty_to_reduce=qty_to_reduce,
                    price=tp_price,
                    reason="TP2_HIT",
                ))
                current = replace(
                    current,
                    tp_hit_count=2,
                    sl_price=current.entry_price * 0.998,
                )
                await self.trade_events_repo.add_event(
                    trade_id=current.trade_id,
                    event_type="TP_HIT",
                    ts=now_ms,
                    symbol=current.symbol,
                    user_id=current.user_id,
                    mode=current.mode,
                    payload={
                        "tp_index": 2,
                        "tp_price": tp_price,
                        "qty_remaining": current.qty_remaining,
                        "realized_pnl_usd": current.realized_pnl_usd,
                        "new_sl_price": current.sl_price,
                    },
                )
                log.info(
                    "[exit-tp-hit] trade_id=%s symbol=%s tp_index=%s price=%s qty_closed=%s qty_remaining=%s realized_pnl=%s",
                    current.trade_id,
                    current.symbol,
                    2,
                    tp_price,
                    qty_to_reduce,
                    current.qty_remaining,
                    current.realized_pnl_usd,
                )
                continue

            current = await self._maybe_await(self.broker.close_position(
                current,
                price=tp_price,
                reason="TP3_HIT",
            ))
            current = replace(current, tp_hit_count=3)
            await self.trade_events_repo.add_event(
                trade_id=current.trade_id,
                event_type="CLOSED",
                ts=now_ms,
                symbol=current.symbol,
                user_id=current.user_id,
                mode=current.mode,
                payload={
                    "reason": "TP3_HIT",
                    "close_price": current.close_price,
                    "realized_pnl_usd": current.realized_pnl_usd,
                    "tp_hit_count": current.tp_hit_count,
                },
            )
            log.info(
                "[exit-trade-closed] trade_id=%s symbol=%s reason=%s final_pnl=%s exchange_net_pnl=%s status=%s",
                current.trade_id,
                current.symbol,
                "TP3_HIT",
                current.realized_pnl_usd,
                getattr(current, "exchange_net_realized_pnl_usdt", None),
                self._status(current),
            )
            break

        return current, tp_triggered

    def _status(self, trade) -> str:
        return trade.status.value if hasattr(trade.status, "value") else str(trade.status)

    async def _maybe_await(self, value):
        if inspect.isawaitable(value):
            return await value
        return value
