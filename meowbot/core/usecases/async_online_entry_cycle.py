from __future__ import annotations

import asyncio
import inspect
import logging
import math
import os
import re
import time
from collections import defaultdict
from typing import Any

from meowbot.core.configs.strategy_version_test_users import detect_strategy_version, normalize_strategy_plan_code
from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Trade
from meowbot.core.services.entry.entry_policy_service import EntryPolicyService
from meowbot.core.services.entry.long_breakout_v18_detector import (
    LONG_BREAKOUT_V18_RULE_ID,
    LongBreakoutV18Config,
    LongBreakoutV18Detector,
    V2_REQUIRED_INDICATORS,
)
from meowbot.core.services.entry.rsi_rebound_supertrend_detector import (
    DetectorResult,
    RsiReboundSupertrendConfig,
    RsiReboundSupertrendDetector,
)
from meowbot.core.services.execution.exit.exit_profile import exit_profile_to_dict, get_exit_profile
from meowbot.core.services.runtime.active_trades_cache import ActiveTradesCache
from meowbot.core.services.runtime.symbol_validator import validate_binance_usdt_perp_symbol
from meowbot.core.services.trade_manager import TradeManager


log = logging.getLogger("meowbot")


LONG_ENTRY_PRICE_RULE_ID = "LONG_ENTRY_ASYM_UP_0_5_DOWN_2_0_V2"
LONG_ENTRY_PRICE_RULE_VERSION = "v2"
LONG_MAX_UPWARD_DEVIATION_PCT = 0.5
LONG_MAX_DOWNWARD_DEVIATION_PCT = 2.0
MONGO_RUNTIME_TIMEOUT_SECONDS = 2.0
STRATEGY_VERSION_V1 = "v1"
STRATEGY_VERSION_V2 = "v2"
V2_STRATEGY_MODE_EXISTING_PLUS_V18 = "existing_plus_v18"
V2_STRATEGY_MODE_ONLY_V18 = "only_v18"
V2_REQUIRED_INDICATOR_LABELS = {
    "rsi_14": "RSI",
    "dist_to_ema_50_pct": "EMA50 distance",
    "volume_ratio_sma_20": "Volume ratio",
    "atr_14_pct": "ATR",
    "vol_peak_offset_10": "Volume peak",
    "close_position_in_candle": "Close position",
    "adx_14": "ADX",
}


class AsyncOnlineEntryCycleUseCase:
    def __init__(
        self,
        *,
        bars_repo,
        trades_repo,
        bot_state_repo,
        trade_events_repo,
        broker,
        exchange,
        telegram_users_repo,
        features_ver: str,
        max_entry_price_deviation_pct: float = 0.5,
        sandbox_start_balance_usd: float = 1000.0,
        cooldowns_repo=None,
        per_user_live_risk_service=None,
        active_trades_cache=None,
        entry_lock_ttl_ms: int = 120_000,
        enable_v18_for_v2: bool | None = None,
        v2_strategy_mode: str | None = None,
    ) -> None:
        self.bars_repo = bars_repo
        self.trades_repo = trades_repo
        self.bot_state_repo = bot_state_repo
        self.trade_events_repo = trade_events_repo
        self.broker = broker
        self.exchange = exchange
        self.telegram_users_repo = telegram_users_repo
        self.features_ver = features_ver
        self.max_entry_price_deviation_pct = max_entry_price_deviation_pct
        self.sandbox_start_balance_usd = sandbox_start_balance_usd
        self.cooldowns_repo = cooldowns_repo
        self.per_user_live_risk_service = per_user_live_risk_service
        self.active_trades_cache = active_trades_cache or ActiveTradesCache()
        self.entry_lock_ttl_ms = int(entry_lock_ttl_ms)

        self.detector = RsiReboundSupertrendDetector(
            RsiReboundSupertrendConfig()
        )
        self.v18_detector = LongBreakoutV18Detector(LongBreakoutV18Config())
        self.enable_v18_for_v2 = (
            self._env_bool("ENABLE_V18_FOR_V2", default=True)
            if enable_v18_for_v2 is None
            else bool(enable_v18_for_v2)
        )
        self.v2_strategy_mode = self._normalize_v2_strategy_mode(
            v2_strategy_mode or os.getenv("V2_STRATEGY_MODE")
        )
        self.entry_policy = EntryPolicyService(test_user_ids=set())
        self.trade_manager = TradeManager()
        self.enabled_users_cache_ttl_ms = int(os.getenv("ENTRY_ENABLED_USERS_CACHE_TTL_MS", "30000"))
        self.debug_signals = self._env_bool("DEBUG_SIGNALS", default=False)
        self.signal_tail_window = max(50, int(os.getenv("SIGNAL_TAIL_WINDOW", "200")))
        self.signal_result_cache_max = max(100, int(os.getenv("SIGNAL_RESULT_CACHE_MAX", "2000")))
        self._signal_result_cache: dict[tuple[str, str, str, int, str], Any] = {}
        self._enabled_users_cache: tuple[int, list[dict[str, Any]]] | None = None
        self.last_metrics: dict[str, Any] = {}

    def _cursor_key(self, symbol: str, tf: str) -> str:
        return f"entry_cursor:{symbol}:{tf}:{self.features_ver}"

    def _entry_lock_key(self, user_id: str, symbol: str) -> str:
        return f"entry_lock:{user_id}:{symbol.upper()}"

    def _portfolio_lock_key(self, user_id: str) -> str:
        return f"entry_portfolio_lock:{user_id}"

    @staticmethod
    def _perf_ms_since(started_at: float) -> int:
        return int((time.perf_counter() - started_at) * 1000)

    def _new_metrics(self, *, symbol: str, tf: str, now_ms: int) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "tf": tf,
            "now_ms": now_ms,
            "duration_ms": 0,
            "indicator_calculation_ms": 0,
            "feature_tail_ms": 0,
            "feature_rows": 0,
            "cache_mode": "memory",
            "v1_signal_check_ms": 0,
            "v2_signal_check_ms": 0,
            "user_routing_ms": 0,
            "entry_checks_ms": 0,
            "order_trade_creation_ms": 0,
            "exit_replay_ms": 0,
            "signals_computed": 0,
            "active_signals": 0,
            "v1_checked": 0,
            "v2_checked": 0,
            "v1_skipped": 0,
            "v2_skipped": 0,
            "signal_cache_hits": 0,
            "user_checks": 0,
            "created": 0,
            "blocked": 0,
            "active_signal_breakdown": {},
            "created_breakdown": {},
            "blocked_breakdown": {},
            "blocked_reasons": {},
            "enabled_users_count": 0,
            "v1_users": 0,
            "v2_users": 0,
            "reason": "",
            "processed": False,
        }

    def _finish_metrics(
        self,
        metrics: dict[str, Any],
        *,
        started_at: float,
        processed: bool,
        reason: str,
    ) -> None:
        metrics["duration_ms"] = self._perf_ms_since(started_at)
        metrics["processed"] = bool(processed)
        metrics["reason"] = reason
        self.last_metrics = dict(metrics)
        if (
            int(metrics.get("signals_computed", 0))
            or int(metrics.get("v1_checked", 0))
            or int(metrics.get("v2_checked", 0))
            or int(metrics.get("v1_skipped", 0))
            or int(metrics.get("v2_skipped", 0))
        ):
            log.info(
                "[signal-metrics] symbols=1 timeframes=1 symbol=%s tf=%s v1_checked=%s v2_checked=%s v1_skipped=%s v2_skipped=%s signal_cache_hits=%s duration_ms=%s v1_users=%s v2_users=%s active_signals=%s",
                metrics.get("symbol"),
                metrics.get("tf"),
                metrics.get("v1_checked", 0),
                metrics.get("v2_checked", 0),
                metrics.get("v1_skipped", 0),
                metrics.get("v2_skipped", 0),
                metrics.get("signal_cache_hits", 0),
                metrics.get("duration_ms", 0),
                metrics.get("v1_users", 0),
                metrics.get("v2_users", 0),
                metrics.get("active_signals", 0),
            )

    async def _list_enabled_trading_users_cached(self, now_ms: int) -> list[dict[str, Any]]:
        if self._enabled_users_cache is not None:
            expires_at, cached = self._enabled_users_cache
            if int(now_ms) < expires_at:
                return list(cached)

        users = await self.telegram_users_repo.list_enabled_trading_users()
        self._enabled_users_cache = (
            int(now_ms) + max(0, int(self.enabled_users_cache_ttl_ms)),
            list(users),
        )
        return list(users)

    @staticmethod
    def _env_bool(name: str, *, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _normalize_strategy_version(value: Any) -> str:
        normalized = str(value or STRATEGY_VERSION_V1).strip().lower()
        return normalized if normalized in {STRATEGY_VERSION_V1, STRATEGY_VERSION_V2} else STRATEGY_VERSION_V1

    @staticmethod
    def _normalize_signal_level(value: Any) -> str | None:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in {"weak", "medium", "strong"} else None

    def _signal_level_from_meta(self, strategy_version: str, signal_meta: dict[str, Any]) -> str | None:
        if self._normalize_strategy_version(strategy_version) != STRATEGY_VERSION_V2:
            return None
        return self._normalize_signal_level(signal_meta.get("signal_level"))

    def _signal_level_from_result(self, strategy_version: str, result: Any) -> str | None:
        meta = getattr(result, "meta", None)
        if not isinstance(meta, dict):
            return None
        return self._signal_level_from_meta(strategy_version, meta)

    @staticmethod
    def _metric_breakdown_key(
        *,
        strategy_version: str,
        rule_id: str,
        signal_level: str | None,
        tf: str,
        mode: str,
    ) -> str:
        return "|".join(
            [
                str(strategy_version or STRATEGY_VERSION_V1).lower(),
                str(rule_id or "-"),
                str(signal_level or "-").lower(),
                str(tf or "-"),
                str(mode or "-").lower(),
            ]
        )

    def _increment_metric_breakdown(
        self,
        metrics: dict[str, Any] | None,
        bucket: str,
        *,
        strategy_version: str,
        rule_id: str,
        signal_level: str | None,
        tf: str,
        mode: str,
        count: int = 1,
    ) -> None:
        if metrics is None:
            return
        breakdown = metrics.setdefault(bucket, {})
        if not isinstance(breakdown, dict):
            breakdown = {}
            metrics[bucket] = breakdown
        normalized_version = self._normalize_strategy_version(strategy_version)
        normalized_level = self._normalize_signal_level(signal_level) if normalized_version == STRATEGY_VERSION_V2 else None
        key = self._metric_breakdown_key(
            strategy_version=normalized_version,
            rule_id=rule_id,
            signal_level=normalized_level,
            tf=tf,
            mode=mode,
        )
        item = breakdown.setdefault(
            key,
            {
                "strategy_version": normalized_version,
                "rule_id": str(rule_id or "-"),
                "signal_level": normalized_level,
                "tf": str(tf or "-"),
                "mode": str(mode or "-").lower(),
                "count": 0,
            },
        )
        item["count"] = int(item.get("count", 0) or 0) + int(count)

    @staticmethod
    def _metric_breakdown_total(metrics: dict[str, Any] | None, bucket: str) -> int:
        if metrics is None:
            return 0
        breakdown = metrics.get(bucket)
        if not isinstance(breakdown, dict):
            return 0
        total = 0
        for item in breakdown.values():
            if isinstance(item, dict):
                total += int(item.get("count", 0) or 0)
        return total

    def _increment_blocked_metric(
        self,
        metrics: dict[str, Any] | None,
        *,
        strategy_version: str,
        rule_id: str,
        signal_level: str | None,
        tf: str,
        mode: str,
        reason: str,
    ) -> None:
        if metrics is None:
            return
        self._increment_metric_breakdown(
            metrics,
            "blocked_breakdown",
            strategy_version=strategy_version,
            rule_id=rule_id,
            signal_level=signal_level,
            tf=tf,
            mode=mode,
        )
        reasons = metrics.setdefault("blocked_reasons", {})
        if not isinstance(reasons, dict):
            reasons = {}
            metrics["blocked_reasons"] = reasons
        normalized_reason = str(reason or "unknown").strip() or "unknown"
        reasons[normalized_reason] = int(reasons.get(normalized_reason, 0) or 0) + 1

    def _position_size_multiplier(self, strategy_version: str, signal_meta: dict[str, Any]) -> float:
        if self._normalize_strategy_version(strategy_version) != STRATEGY_VERSION_V2:
            return 1.0
        try:
            multiplier = float(signal_meta.get("position_size_multiplier") or 1.0)
        except (TypeError, ValueError):
            multiplier = 1.0
        return multiplier if multiplier > 0 else 1.0

    def _v2_strict_entry_reject_reason(
        self,
        *,
        rule_id: str,
        signal_level: str | None,
        signal_meta: dict[str, Any],
    ) -> str | None:
        if str(rule_id or "") != LONG_BREAKOUT_V18_RULE_ID:
            return "v2_legacy_rule_not_allowed"
        if signal_level not in {"strong", "medium", "weak"}:
            return "v2_missing_signal_level"

        final_signal = self._normalize_signal_level(signal_meta.get("final_signal"))
        if final_signal != signal_level:
            return "v2_final_signal_mismatch"

        level_key = f"{signal_level}_result"
        if signal_meta.get(level_key) is not True:
            return f"v2_{signal_level}_result_not_true"

        if not any(
            signal_meta.get(key) is True
            for key in ("strong_result", "medium_result", "weak_result")
        ):
            return "v2_no_level_passed"

        return None

    def _v2_required_indicator_values(
        self,
        *,
        bar,
        signal_meta: dict[str, Any] | None = None,
    ) -> tuple[dict[str, float | None], list[str]]:
        signal_meta = dict(signal_meta or {})
        features = dict(getattr(bar, "features", None) or {})
        indicator_values = dict(signal_meta.get("indicator_values") or {})
        merged = {
            **features,
            **{key: value for key, value in indicator_values.items() if value is not None},
        }
        close = self._finite_float(getattr(bar, "c", None))
        high = self._finite_float(getattr(bar, "h", None))
        low = self._finite_float(getattr(bar, "l", None))
        values: dict[str, float | None] = {
            "rsi_14": self._first_finite_number(merged, "rsi_14", "rsi14"),
            "dist_to_ema_50_pct": self._v2_ema_distance_pct(merged, close),
            "volume_ratio_sma_20": self._first_finite_number(merged, "volume_ratio_sma_20", "relative_volume20"),
            "atr_14_pct": self._v2_atr_percent(merged),
            "vol_peak_offset_10": self._first_finite_number(merged, "vol_peak_offset_10"),
            "close_position_in_candle": self._v2_close_position(merged, close=close, high=high, low=low),
            "adx_14": self._first_finite_number(merged, "adx_14", "adx14"),
        }
        missing = [key for key in V2_REQUIRED_INDICATORS if not self._is_finite_number(values.get(key))]
        return values, missing

    async def _emit_v2_missing_indicator_skip(
        self,
        *,
        user_id: str,
        symbol: str,
        tf: str,
        bar,
        rule_id: str,
        now_ms: int,
        mode: str,
        missing: list[str],
        indicator_values: dict[str, Any],
    ) -> None:
        labels = [V2_REQUIRED_INDICATOR_LABELS.get(key, key) for key in missing]
        feature_keys = sorted(dict(getattr(bar, "features", None) or {}).keys())
        await self.trade_events_repo.add_event(
            trade_id=f"debug:global:{symbol}:{tf}:{bar.close_time}:{rule_id}:{user_id}",
            event_type="ENTRY_SKIPPED_MISSING_INDICATOR",
            ts=now_ms,
            symbol=symbol,
            user_id=user_id,
            mode=mode,
            payload={
                "reason": "missing_required_indicators",
                "admin_reason": f"Missing indicators: {', '.join(labels)}",
                "missing_indicators": missing,
                "missing_indicators_human": labels,
                "indicator_values": indicator_values,
                "feature_keys": feature_keys,
                "features_ok": bool(getattr(bar, "features_ok", False)),
                "tf_entry": tf,
                "rule_id": rule_id,
                "strategy_version": STRATEGY_VERSION_V2,
                "entry_bar_close_time": getattr(bar, "close_time", None),
                "features_ver": self.features_ver,
            },
        )
        log.warning(
            "[features] FEATURE_PIPELINE_MISSING symbol=%s tf=%s user=%s rule_id=%s features_ver=%s missing=%s features_ok=%s feature_keys=%s",
            symbol,
            tf,
            user_id,
            rule_id,
            self.features_ver,
            missing,
            bool(getattr(bar, "features_ok", False)),
            feature_keys,
        )
        log.warning(
            "[entry-async] ENTRY_SKIPPED_MISSING_INDICATOR symbol=%s tf=%s user=%s rule_id=%s strategy_version=%s missing=%s values=%s",
            symbol,
            tf,
            user_id,
            rule_id,
            STRATEGY_VERSION_V2,
            ",".join(labels),
            indicator_values,
        )

    def _v2_ema_distance_pct(self, values: dict[str, Any], close: float | None) -> float | None:
        explicit = self._first_finite_number(values, "dist_to_ema_50_pct", "ema50_dist_pct")
        if explicit is not None:
            return explicit
        ema50 = self._first_finite_number(values, "ema_50", "ema50")
        if close is None or ema50 in {None, 0.0}:
            return None
        return (close - float(ema50)) / float(ema50)

    def _v2_atr_percent(self, values: dict[str, Any]) -> float | None:
        value = self._first_finite_number(values, "atr_14_pct", "atr14_pct")
        if value is None:
            return None
        return value

    def _v2_close_position(
        self,
        values: dict[str, Any],
        *,
        close: float | None,
        high: float | None,
        low: float | None,
    ) -> float | None:
        explicit = self._first_finite_number(values, "close_position_in_candle")
        if explicit is not None:
            return explicit
        if close is None or high is None or low is None or high == low:
            return None
        return (close - low) / (high - low)

    def _first_finite_number(self, values: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = self._finite_float(values.get(key))
            if value is not None:
                return value
        return None

    @staticmethod
    def _finite_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if math.isfinite(numeric) else None

    @staticmethod
    def _is_finite_number(value: Any) -> bool:
        try:
            return value is not None and math.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_v2_strategy_mode(value: str | None) -> str:
        normalized = str(value or V2_STRATEGY_MODE_EXISTING_PLUS_V18).strip().lower()
        if normalized in {"v18_only", "only_v18"}:
            return V2_STRATEGY_MODE_ONLY_V18
        if normalized in {"existing_plus_v18", "all", "existing+v18"}:
            return V2_STRATEGY_MODE_EXISTING_PLUS_V18
        return V2_STRATEGY_MODE_EXISTING_PLUS_V18

    def _user_strategy_version(self, tg_user: dict[str, Any]) -> str:
        return detect_strategy_version(
            strategy_version=tg_user.get("strategy_version"),
            plan_code=tg_user.get("plan_code") or tg_user.get("subscription_type"),
            features_json=tg_user.get("features_json"),
            email=tg_user.get("email"),
            username=tg_user.get("username"),
            telegram_id=tg_user.get("telegram_id"),
        )

    def _subscription_type(self, tg_user: dict[str, Any]) -> str:
        value = tg_user.get("subscription_type") or tg_user.get("plan_code") or "unknown"
        return normalize_strategy_plan_code(value)

    def _group_users_by_strategy(self, users: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        grouped = {
            STRATEGY_VERSION_V1: [],
            STRATEGY_VERSION_V2: [],
        }
        for user in users:
            grouped[self._user_strategy_version(user)].append(user)
        return grouped

    def _detect_strategy_signals(
        self,
        *,
        window: list,
        users_by_strategy: dict[str, list[dict[str, Any]]],
        signal_cache: dict[tuple[str, str, str, int, str], Any],
        symbol: str,
        tf: str,
        bar,
        now_ms: int,
        metrics: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []

        # V2 is intentionally excluded from legacy RSI/supertrend routing.
        # V2 may open only through LONG_BREAKOUT_V18 strong/medium/weak rules.
        present_existing_versions = {STRATEGY_VERSION_V1} if users_by_strategy.get(STRATEGY_VERSION_V1) else set()
        if present_existing_versions:
            cache_key = (symbol, tf, STRATEGY_VERSION_V1, int(getattr(bar, "close_time", 0) or 0), self.features_ver)
            cached = cache_key in signal_cache or self._get_cached_signal_result(cache_key) is not None
            if cached:
                result = signal_cache.get(cache_key)
                if result is None:
                    result = self._get_cached_signal_result(cache_key)
                    signal_cache[cache_key] = result
            else:
                started_at = time.perf_counter()
                result = self.detector.check_long(window)
                signal_cache[cache_key] = result
                self._set_cached_signal_result(cache_key, result)
                if metrics is not None:
                    metrics["v1_signal_check_ms"] = int(metrics.get("v1_signal_check_ms", 0)) + self._perf_ms_since(started_at)
            if metrics is not None:
                if cached:
                    metrics["signal_cache_hits"] = int(metrics.get("signal_cache_hits", 0)) + 1
                else:
                    metrics["signals_computed"] = int(metrics.get("signals_computed", 0)) + 1
                    metrics["v1_checked"] = int(metrics.get("v1_checked", 0)) + 1
            entries.append(
                {
                    "result": result,
                    "target_versions": present_existing_versions,
                    "strategy_name": "existing",
                }
            )
            self._log_signal_result(
                result=result,
                strategy_versions=present_existing_versions,
                symbol=symbol,
                tf=tf,
                bar=bar,
                now_ms=now_ms,
            )
        elif metrics is not None:
            metrics["v1_skipped"] = int(metrics.get("v1_skipped", 0)) + 1

        if self.enable_v18_for_v2 and users_by_strategy.get(STRATEGY_VERSION_V2):
            cache_key = (symbol, tf, STRATEGY_VERSION_V2, int(getattr(bar, "close_time", 0) or 0), self.features_ver)
            cached = cache_key in signal_cache or self._get_cached_signal_result(cache_key) is not None
            if cached:
                result = signal_cache.get(cache_key)
                if result is None:
                    result = self._get_cached_signal_result(cache_key)
                    signal_cache[cache_key] = result
                if metrics is not None:
                    metrics["signal_cache_hits"] = int(metrics.get("signal_cache_hits", 0)) + 1
            else:
                started_at = time.perf_counter()
                try:
                    result = self.v18_detector.check_long(window)
                except Exception as exc:
                    result = DetectorResult(
                        signal=False,
                        reason="detector_exception",
                        rule_id=LONG_BREAKOUT_V18_RULE_ID,
                        current_close_time=getattr(bar, "close_time", None),
                        bars_available=len(window),
                        meta={"error": f"{type(exc).__name__}: {exc}"},
                    )
                    log.exception(
                        "[signal-error] rule_id=%s strategy_version=%s symbol=%s tf=%s detector failed",
                        LONG_BREAKOUT_V18_RULE_ID,
                        STRATEGY_VERSION_V2,
                        symbol,
                        tf,
                    )
                finally:
                    if metrics is not None:
                        metrics["v2_signal_check_ms"] = int(metrics.get("v2_signal_check_ms", 0)) + self._perf_ms_since(started_at)
                        metrics["signals_computed"] = int(metrics.get("signals_computed", 0)) + 1
                        metrics["v2_checked"] = int(metrics.get("v2_checked", 0)) + 1
                signal_cache[cache_key] = result
                self._set_cached_signal_result(cache_key, result)

            entries.append(
                {
                    "result": result,
                    "target_versions": {STRATEGY_VERSION_V2},
                    "strategy_name": "long_breakout_v18",
                }
            )
            self._log_signal_result(
                result=result,
                strategy_versions={STRATEGY_VERSION_V2},
                symbol=symbol,
                tf=tf,
                bar=bar,
                now_ms=now_ms,
            )
        elif metrics is not None:
            metrics["v2_skipped"] = int(metrics.get("v2_skipped", 0)) + 1

        return entries

    def _get_cached_signal_result(self, cache_key: tuple[str, str, str, int, str]) -> Any | None:
        return self._signal_result_cache.get(cache_key)

    def _set_cached_signal_result(self, cache_key: tuple[str, str, str, int, str], result: Any) -> None:
        if len(self._signal_result_cache) >= self.signal_result_cache_max:
            # FIFO trimming keeps the cache bounded without making signal processing wait on heavier structures.
            for key in list(self._signal_result_cache.keys())[: max(1, self.signal_result_cache_max // 10)]:
                self._signal_result_cache.pop(key, None)
        self._signal_result_cache[cache_key] = result

    def _is_active_signal_entry(self, signal_entry: dict[str, Any]) -> bool:
        result = signal_entry.get("result")
        if not bool(getattr(result, "signal", False)):
            return False

        rule_id = str(getattr(result, "rule_id", "") or "")
        if rule_id != LONG_BREAKOUT_V18_RULE_ID:
            return True

        meta = getattr(result, "meta", None) or {}
        final_signal = self._normalize_signal_level(meta.get("final_signal")) if isinstance(meta, dict) else None
        if final_signal is None:
            log.debug(
                "[signal-routing] rule_id=%s strategy_version=%s skipped because FINAL_SIGNAL=NONE",
                LONG_BREAKOUT_V18_RULE_ID,
                STRATEGY_VERSION_V2,
            )
            return False
        return True

    def _log_signal_result(
        self,
        *,
        result,
        strategy_versions: set[str],
        symbol: str,
        tf: str,
        bar,
        now_ms: int,
    ) -> None:
        versions = ",".join(sorted(strategy_versions))
        meta = getattr(result, "meta", None) or {}
        is_v2_signal = getattr(result, "rule_id", None) == LONG_BREAKOUT_V18_RULE_ID and STRATEGY_VERSION_V2 in strategy_versions
        indicators = self._format_signal_indicators(
            result=result,
            meta=meta,
            bar=bar,
            is_v2_signal=is_v2_signal,
        )
        failed_main = self._format_failed_conditions(self._signal_failed_main(result=result, meta=meta, is_v2_signal=is_v2_signal))
        if is_v2_signal and self.debug_signals:
            log.info(
                "[signal-check] symbol=%s tf=%s rule_id=%s strategy_version=%s strong=%s strong_failed=%s medium=%s medium_failed=%s weak=%s weak_failed=%s weak_score=%s FINAL_SIGNAL=%s indicators=%s",
                symbol,
                tf,
                getattr(result, "rule_id", "-"),
                versions,
                bool(meta.get("strong_result")) if isinstance(meta, dict) else False,
                meta.get("strong_failed_conditions") if isinstance(meta, dict) else None,
                bool(meta.get("medium_result")) if isinstance(meta, dict) else False,
                meta.get("medium_failed_conditions") if isinstance(meta, dict) else None,
                bool(meta.get("weak_result")) if isinstance(meta, dict) else False,
                meta.get("weak_failed_conditions") if isinstance(meta, dict) else None,
                meta.get("signal_score") if isinstance(meta, dict) else None,
                str(meta.get("final_signal") or "none").upper() if isinstance(meta, dict) else "NONE",
                indicators,
            )
        if isinstance(meta, dict) and meta.get("missing_indicators"):
            log.warning(
                "[signal] ENTRY_SKIPPED_MISSING_INDICATOR rule_id=%s strategy_version=%s symbol=%s tf=%s now_ms=%s bar_close=%s missing=%s indicators=%s failed_main=%s",
                getattr(result, "rule_id", "-"),
                versions,
                symbol,
                tf,
                now_ms,
                getattr(bar, "close_time", None),
                ",".join(str(item) for item in meta.get("missing_indicators") or []),
                indicators,
                failed_main,
            )
            return
        if is_v2_signal:
            log.info(
                "[signal] rule_id=%s strategy_version=%s symbol=%s tf=%s now_ms=%s bar_close=%s signal=%s reason=%s final_signal=%s score=%s strong=%s medium=%s weak=%s indicators=%s failed_main=%s",
                getattr(result, "rule_id", "-"),
                versions,
                symbol,
                tf,
                now_ms,
                getattr(bar, "close_time", None),
                bool(getattr(result, "signal", False)),
                getattr(result, "reason", "-"),
                str(meta.get("final_signal") or "none").upper() if isinstance(meta, dict) else "NONE",
                meta.get("signal_score") if isinstance(meta, dict) else None,
                bool(meta.get("strong_result")) if isinstance(meta, dict) else False,
                bool(meta.get("medium_result")) if isinstance(meta, dict) else False,
                bool(meta.get("weak_result")) if isinstance(meta, dict) else False,
                indicators,
                failed_main,
            )
            return
        log.info(
            "[signal] rule_id=%s strategy_version=%s symbol=%s tf=%s now_ms=%s bar_close=%s signal=%s reason=%s signal_level=%s score=%s position_size_multiplier=%s soft_stop_activation_pct=%s indicators=%s failed_main=%s",
            getattr(result, "rule_id", "-"),
            versions,
            symbol,
            tf,
            now_ms,
            getattr(bar, "close_time", None),
            bool(getattr(result, "signal", False)),
            getattr(result, "reason", "-"),
            meta.get("signal_level") if isinstance(meta, dict) else None,
            meta.get("signal_score") if isinstance(meta, dict) else None,
            meta.get("position_size_multiplier") if isinstance(meta, dict) else None,
            meta.get("soft_stop_activation_pct") if isinstance(meta, dict) else None,
            indicators,
            failed_main,
        )

    def _format_signal_indicators(
        self,
        *,
        result,
        meta: dict[str, Any],
        bar,
        is_v2_signal: bool,
    ) -> str:
        if is_v2_signal:
            values = meta.get("indicator_values") if isinstance(meta, dict) else None
            return self._format_v2_indicators(values if isinstance(values, dict) else {})
        return self._format_v1_indicators(result=result, bar=bar)

    def _format_v2_indicators(self, values: dict[str, Any]) -> str:
        return (
            "{"
            f"rsi={self._format_decimal(values.get('rsi_14'), digits=1)}, "
            f"ema_dist={self._format_percent_ratio(values.get('dist_to_ema_50_pct'))}, "
            f"volume={self._format_volume_ratio(values.get('volume_ratio_sma_20'))}, "
            f"atr={self._format_percent_ratio(values.get('atr_14_pct'))}, "
            f"adx={self._format_decimal(values.get('adx_14'), digits=1)}, "
            f"close_pos={self._format_decimal(values.get('close_position_in_candle'), digits=2)}, "
            f"vol_peak={self._format_decimal(values.get('vol_peak_offset_10'), digits=0)}"
            "}"
        )

    def _format_v1_indicators(self, *, result, bar) -> str:
        features = getattr(bar, "features", None) or {}
        close = self._number_or_none(getattr(bar, "c", None))
        ema50 = self._first_feature_number(features, "ema_50", "ema50")
        ema_dist = None
        if close is not None and ema50 not in {None, 0.0}:
            ema_dist = (close - float(ema50)) / float(ema50)
        rsi_now = self._number_or_none(getattr(result, "current_rsi", None))
        if rsi_now is None:
            rsi_now = self._first_feature_number(features, "rsi_14", "rsi14")
        rsi_prev = self._number_or_none(getattr(result, "min_prev_rsi", None))
        supertrend_bullish = self._number_or_none(getattr(result, "supertrend_bullish", None))
        if supertrend_bullish is None:
            supertrend_bullish = self._first_feature_number(features, "supertrend_bullish_10_3_0", "supertrend_bullish")
        supertrend_direction = self._first_feature_value(features, "supertrend_direction", "supertrend_dir")
        supertrend = supertrend_direction
        if supertrend is None and supertrend_bullish is not None:
            supertrend = "bullish" if bool(supertrend_bullish) else "bearish"

        return (
            "{"
            f"rsi_prev={self._format_decimal(rsi_prev, digits=1)}, "
            f"rsi_now={self._format_decimal(rsi_now, digits=1)}, "
            f"rsi={self._format_decimal(rsi_now, digits=1)}, "
            f"ema_dist={self._format_percent_ratio(ema_dist)}, "
            f"volume={self._format_volume_ratio(self._first_feature_number(features, 'volume_ratio_sma_20', 'relative_volume20'))}, "
            f"atr={self._format_percent_ratio(self._first_feature_number(features, 'atr_14_pct', 'atr14_pct'))}, "
            f"adx={self._format_decimal(self._first_feature_number(features, 'adx_14', 'adx14'), digits=1)}, "
            f"supertrend={self._format_raw_value(supertrend)}"
            "}"
        )

    def _signal_failed_main(self, *, result, meta: dict[str, Any], is_v2_signal: bool) -> list[str]:
        if bool(getattr(result, "signal", False)):
            return []
        if is_v2_signal and isinstance(meta, dict):
            if meta.get("missing_indicators"):
                return [f"missing:{','.join(str(item) for item in meta.get('missing_indicators') or [])}"]
            for key in ("weak_failed_conditions", "medium_failed_conditions", "strong_failed_conditions"):
                failed = meta.get(key)
                if isinstance(failed, list) and failed:
                    return [str(item) for item in failed[:5]]
        reason = str(getattr(result, "reason", "") or "").strip()
        return [reason] if reason else []

    @staticmethod
    def _format_failed_conditions(items: list[str]) -> str:
        if not items:
            return "[]"
        return "[" + ", ".join(f'"{str(item)}"' for item in items) + "]"

    def _first_feature_number(self, features: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = self._number_or_none(features.get(key))
            if value is not None:
                return value
        return None

    @staticmethod
    def _first_feature_value(features: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in features and features.get(key) is not None:
                return features.get(key)
        return None

    @staticmethod
    def _number_or_none(value: Any) -> float | None:
        try:
            if value is None:
                return None
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _format_decimal(self, value: Any, *, digits: int) -> str:
        number = self._number_or_none(value)
        if number is None:
            return "N/A"
        return f"{number:.{digits}f}"

    def _format_percent_ratio(self, value: Any) -> str:
        number = self._number_or_none(value)
        if number is None:
            return "N/A"
        return f"{number * 100:.2f}%"

    def _format_volume_ratio(self, value: Any) -> str:
        number = self._number_or_none(value)
        if number is None:
            return "N/A"
        return f"{number:.2f}x/{number * 100:.2f}%"

    def _format_raw_value(self, value: Any) -> str:
        if value is None:
            return "N/A"
        number = self._number_or_none(value)
        if number is not None:
            return self._format_decimal(number, digits=0)
        return str(value)

    def _should_log_detailed_v2_signal(self, *, result, meta: dict[str, Any]) -> bool:
        if self.debug_signals:
            return True
        if bool(getattr(result, "signal", False)):
            return True
        if not isinstance(meta, dict):
            return False
        if self._normalize_signal_level(meta.get("final_signal")) is not None:
            return True
        score = self._int_or_none(meta.get("signal_score"))
        if score is not None and score >= 4:
            return True
        for key in ("strong_failed_conditions", "medium_failed_conditions", "weak_failed_conditions"):
            failed = meta.get(key)
            if isinstance(failed, list) and 0 < len(failed) <= 2:
                return True
        return False

    async def run(
        self,
        symbol: str,
        tf: str,
        now_ms: int,
        *,
        force_signal_scan: bool = False,
    ) -> tuple[bool, int | None, str]:
        cycle_started_at = time.perf_counter()
        metrics = self._new_metrics(symbol=symbol, tf=tf, now_ms=now_ms)

        def finish(processed: bool, last_close: int | None, reason: str) -> tuple[bool, int | None, str]:
            self._finish_metrics(
                metrics,
                started_at=cycle_started_at,
                processed=processed,
                reason=reason,
            )
            return processed, last_close, reason

        symbol_validation = validate_binance_usdt_perp_symbol(symbol)
        if not symbol_validation.valid:
            normalized_symbol = symbol_validation.normalized_symbol or symbol_validation.symbol
            log.warning(
                "[entry-async] INVALID_SYMBOL_SKIPPED symbol=%s tf=%s reason=%s",
                normalized_symbol,
                tf,
                symbol_validation.reason,
            )
            await self.trade_events_repo.add_event(
                trade_id=f"debug:global:{normalized_symbol}:{tf}:{now_ms}:invalid_symbol",
                event_type="INVALID_SYMBOL_SKIPPED",
                ts=now_ms,
                symbol=normalized_symbol,
                user_id="system",
                mode="signal",
                payload={
                    "reason": "invalid_symbol",
                    "invalid_reason": symbol_validation.reason,
                    "symbol": normalized_symbol,
                    "tf_entry": tf,
                    "action": "entry_skipped",
                    "features_ver": self.features_ver,
                },
            )
            return finish(False, None, "invalid_symbol")

        symbol = symbol_validation.normalized_symbol
        cursor_key = self._cursor_key(symbol, tf)
        last_decision = await self._state_get_int(cursor_key, default=0, symbol=symbol)

        feature_tail_started_at = time.perf_counter()
        feature_bars = await self._bars_get_tail(
            symbol=symbol,
            tf=tf,
            n=self.signal_tail_window,
            require_features_ok=False,
        )
        metrics["feature_tail_ms"] = self._perf_ms_since(feature_tail_started_at)

        if not feature_bars:
            metrics["cache_mode"] = "memory_empty"
            return finish(False, None, "no_feature_ready_bars")
        metrics["feature_rows"] = len(feature_bars)
        metrics["cache_mode"] = "memory"

        latest_bar = feature_bars[-1]

        log.debug(
            "[entry-cycle] %s %s: tail_size=%s latest_close=%s last_decision=%s",
            symbol,
            tf,
            len(feature_bars),
            latest_bar.close_time,
            last_decision,
        )

        if last_decision == 0 and not force_signal_scan:
            await self._state_set_int(cursor_key, latest_bar.close_time, symbol=symbol)
            log.debug(
                "[warmup] %s %s: cursor initialized to current last feature bar close=%s (live mode, no replay)",
                symbol,
                tf,
                latest_bar.close_time,
            )
            return finish(False, latest_bar.close_time, "cursor_initialized_live_mode")

        new_bars = [b for b in feature_bars if b.close_time > last_decision]
        new_bars.sort(key=lambda x: x.close_time)

        if force_signal_scan:
            new_bars = [latest_bar]
        elif not new_bars:
            return finish(False, latest_bar.close_time, "no_new_feature_bar")

        # Historical bars are warmup context only. Entry is evaluated exactly
        # once on the latest closed bar to avoid replaying old signals.
        is_warmup = len(new_bars) > 1
        historical_warmup_bars = new_bars[:-1]
        entry_bars = [new_bars[-1]]
        entry_bar_close = entry_bars[-1].close_time
        if is_warmup:
            log.debug(
                "[warmup] %s %s: skipped historical entry checks count=%s first_close=%s last_warmup_close=%s entry_close=%s",
                symbol,
                tf,
                len(historical_warmup_bars),
                historical_warmup_bars[0].close_time if historical_warmup_bars else None,
                historical_warmup_bars[-1].close_time if historical_warmup_bars else None,
                entry_bar_close,
            )

        user_routing_started_at = time.perf_counter()
        enabled_users = await self._list_enabled_trading_users_cached(now_ms)
        metrics["enabled_users_count"] = len(enabled_users)
        metrics["user_routing_ms"] = self._perf_ms_since(user_routing_started_at)
        if not enabled_users:
            await self._state_set_int(cursor_key, entry_bar_close, symbol=symbol)
            log.info(
                "[entry-async] %s %s: no enabled users, cursor advanced to %s",
                symbol,
                tf,
                entry_bar_close,
            )
            return finish(True, entry_bar_close, "no_enabled_users")
        users_by_strategy = self._group_users_by_strategy(enabled_users)
        metrics["v1_users"] = len(users_by_strategy.get(STRATEGY_VERSION_V1, []))
        metrics["v2_users"] = len(users_by_strategy.get(STRATEGY_VERSION_V2, []))
        log.debug(
            "[entry-async] %s %s: active_strategy_versions=%s v1_users=%s v2_users=%s",
            symbol,
            tf,
            [
                version
                for version, users in users_by_strategy.items()
                if users
            ],
            metrics["v1_users"],
            metrics["v2_users"],
        )

        all_open_trades = self.active_trades_cache.get_all_open_trades()
        log.debug(
            "[entry-async] %s %s: loaded open trades from memory cache count=%s",
            symbol,
            tf,
            len(all_open_trades),
        )
        open_trades_by_user: dict[str, list] = defaultdict(list)
        for trade in all_open_trades:
            user_id = getattr(trade, "user_id", None)
            if user_id is not None:
                open_trades_by_user[str(user_id)].append(trade)

        last_processed_close: int | None = None
        signal_cache: dict[tuple[str, str, str, int, str], Any] = {}

        for bar in entry_bars:
            signal_entry_price = float(bar.c)
            window = [b for b in feature_bars if b.close_time <= bar.close_time]
            signal_entries = self._detect_strategy_signals(
                window=window,
                users_by_strategy=users_by_strategy,
                signal_cache=signal_cache,
                symbol=symbol,
                tf=tf,
                bar=bar,
                now_ms=now_ms,
                metrics=metrics,
            )
            active_signal_entries = [
                entry for entry in signal_entries if self._is_active_signal_entry(entry)
            ]
            metrics["active_signals"] = int(metrics.get("active_signals", 0)) + len(active_signal_entries)
            for signal_entry in active_signal_entries:
                result = signal_entry["result"]
                for version in {
                    self._normalize_strategy_version(item)
                    for item in signal_entry.get("target_versions", set())
                }:
                    self._increment_metric_breakdown(
                        metrics,
                        "active_signal_breakdown",
                        strategy_version=version,
                        rule_id=getattr(result, "rule_id", "-"),
                        signal_level=self._signal_level_from_result(version, result),
                        tf=tf,
                        mode="signal",
                    )

            if not active_signal_entries:
                await self._state_set_int(cursor_key, bar.close_time, symbol=symbol)
                last_processed_close = bar.close_time
                continue

            for signal_entry in active_signal_entries:
                result = signal_entry["result"]
                target_versions = {
                    self._normalize_strategy_version(version)
                    for version in signal_entry.get("target_versions", set())
                }
                user_routing_started_at = time.perf_counter()
                target_users = []
                for version in sorted(target_versions):
                    target_users.extend(users_by_strategy.get(version, []))
                metrics["user_routing_ms"] = int(metrics.get("user_routing_ms", 0)) + self._perf_ms_since(user_routing_started_at)
                if not target_users:
                    continue

                live_entry_price = await self.exchange.get_mark_price(symbol)
                if live_entry_price is None:
                    await self.trade_events_repo.add_event(
                        trade_id=f"debug:global:{symbol}:{tf}:{bar.close_time}:{result.rule_id}",
                        event_type="ENTRY_PRICE_UNAVAILABLE",
                        ts=now_ms,
                        symbol=symbol,
                        user_id="system",
                        mode="signal",
                        payload={
                            "scope": "global_signal",
                            "rule_id": result.rule_id,
                            "strategy_versions": sorted(target_versions),
                            "tf_entry": tf,
                            "signal_entry_price": signal_entry_price,
                            "entry_bar_close_time": bar.close_time,
                            "features_ver": self.features_ver,
                        },
                    )
                    log.warning(
                        "[entry-async] %s %s: live price unavailable rule_id=%s signal_price=%.4f entry skipped",
                        symbol,
                        tf,
                        result.rule_id,
                        signal_entry_price,
                    )
                    continue

                price_check = self._check_long_entry_price(
                    signal_entry_price=signal_entry_price,
                    live_entry_price=live_entry_price,
                )

                log.info(
                    "[entry-check] %s %s: rule_id=%s bar_close=%s signal_price=%.4f live_price=%.4f upward_pct=%.3f downward_pct=%.3f allowed=%s reason=%s",
                    symbol,
                    tf,
                    result.rule_id,
                    bar.close_time,
                    signal_entry_price,
                    live_entry_price,
                    price_check["upward_pct"],
                    price_check["downward_pct"],
                    price_check["allowed"],
                    price_check["reason"],
                )

                if not price_check["allowed"]:
                    await self.trade_events_repo.add_event(
                        trade_id=f"debug:global:{symbol}:{tf}:{bar.close_time}:{result.rule_id}",
                        event_type="ENTRY_PRICE_MISMATCH",
                        ts=now_ms,
                        symbol=symbol,
                        user_id="system",
                        mode="signal",
                        payload={
                            "scope": "global_signal",
                            "rule_id": result.rule_id,
                            "strategy_versions": sorted(target_versions),
                            "tf_entry": tf,
                            "signal_entry_price": signal_entry_price,
                            "live_entry_price": live_entry_price,
                            "reason": price_check["reason"],
                            "entry_price_rule_id": price_check["entry_price_rule_id"],
                            "entry_price_rule_version": price_check["entry_price_rule_version"],
                            "upward_pct": price_check["upward_pct"],
                            "downward_pct": price_check["downward_pct"],
                            "max_upward_deviation_pct": price_check["max_upward_deviation_pct"],
                            "max_downward_deviation_pct": price_check["max_downward_deviation_pct"],
                            "entry_bar_close_time": bar.close_time,
                            "features_ver": self.features_ver,
                        },
                    )
                    log.warning(
                        "[entry-skip] %s %s: rule_id=%s price mismatch signal=%.4f live=%.4f reason=%s up=%.3f%% down=%.3f%%",
                        symbol,
                        tf,
                        result.rule_id,
                        signal_entry_price,
                        live_entry_price,
                        price_check["reason"],
                        price_check["upward_pct"],
                        price_check["downward_pct"],
                    )
                    continue

                for tg_user in target_users:
                    strategy_version = self._user_strategy_version(tg_user)
                    entry_checks_started_at = time.perf_counter()
                    blocked_before = self._metric_breakdown_total(metrics, "blocked_breakdown")
                    created = await self._try_open_for_user(
                        tg_user=tg_user,
                        symbol=symbol,
                        tf=tf,
                        now_ms=now_ms,
                        bar=bar,
                        rule_id=result.rule_id,
                        strategy_version=strategy_version,
                        signal_meta=dict(getattr(result, "meta", None) or {}),
                        signal_entry_price=signal_entry_price,
                        live_entry_price=live_entry_price,
                        deviation_pct=float(price_check["effective_deviation_pct"]),
                        entry_price_check=price_check,
                        open_trades_by_user=open_trades_by_user,
                        metrics=metrics,
                    )
                    metrics["entry_checks_ms"] = int(metrics.get("entry_checks_ms", 0)) + self._perf_ms_since(entry_checks_started_at)

                    if created is not None:
                        open_trades_by_user[str(created.user_id)].append(created)
                        metrics["created"] = int(metrics.get("created", 0)) + 1
                        self._increment_metric_breakdown(
                            metrics,
                            "created_breakdown",
                            strategy_version=strategy_version,
                            rule_id=result.rule_id,
                            signal_level=self._signal_level_from_meta(
                                strategy_version,
                                dict(getattr(result, "meta", None) or {}),
                            ),
                            tf=tf,
                            mode=str(getattr(created, "mode", None) or tg_user.get("trading_mode") or "-"),
                        )
                    else:
                        metrics["blocked"] = int(metrics.get("blocked", 0)) + 1
                        if self._metric_breakdown_total(metrics, "blocked_breakdown") == blocked_before:
                            self._increment_blocked_metric(
                                metrics,
                                strategy_version=strategy_version,
                                rule_id=result.rule_id,
                                signal_level=self._signal_level_from_meta(
                                    strategy_version,
                                    dict(getattr(result, "meta", None) or {}),
                                ),
                                tf=tf,
                                mode=str(tg_user.get("trading_mode") or "-"),
                                reason="unknown",
                            )

            await self._state_set_int(cursor_key, bar.close_time, symbol=symbol)
            last_processed_close = bar.close_time

        return finish(True, last_processed_close, "entry_processed")

    async def _try_open_for_user(
        self,
        *,
        tg_user: dict,
        symbol: str,
        tf: str,
        now_ms: int,
        bar,
        rule_id: str,
        strategy_version: str | None = None,
        signal_meta: dict[str, Any] | None = None,
        signal_entry_price: float,
        live_entry_price: float,
        deviation_pct: float,
        open_trades_by_user: dict[str, list],
        entry_price_check: dict | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> Trade | None:
        if metrics is not None:
            metrics["user_checks"] = int(metrics.get("user_checks", 0)) + 1

        symbol_validation = validate_binance_usdt_perp_symbol(symbol)
        if not symbol_validation.valid:
            normalized_symbol = symbol_validation.normalized_symbol or symbol_validation.symbol
            log.warning(
                "[entry-async] INVALID_SYMBOL_SKIPPED symbol=%s tf=%s user=%s reason=%s",
                normalized_symbol,
                tf,
                tg_user.get("trading_user_id") or tg_user.get("telegram_id"),
                symbol_validation.reason,
            )
            await self.trade_events_repo.add_event(
                trade_id=f"debug:global:{normalized_symbol}:{tf}:{getattr(bar, 'close_time', now_ms)}:{rule_id}:invalid_symbol",
                event_type="INVALID_SYMBOL_SKIPPED",
                ts=now_ms,
                symbol=normalized_symbol,
                user_id=str(tg_user.get("trading_user_id") or f"tg:{tg_user.get('telegram_id')}"),
                mode=str(tg_user.get("trading_mode", "sandbox")),
                payload={
                    "reason": "invalid_symbol",
                    "invalid_reason": symbol_validation.reason,
                    "symbol": normalized_symbol,
                    "tf_entry": tf,
                    "rule_id": rule_id,
                    "action": "open_trade_skipped",
                    "features_ver": self.features_ver,
                },
            )
            return None

        symbol = symbol_validation.normalized_symbol
        user_id = str(tg_user.get("trading_user_id") or f"tg:{tg_user.get('telegram_id')}")
        trading_mode = str(tg_user.get("trading_mode", "sandbox"))
        if user_id == "tg:123456789":
            log.warning(
                "[entry-async] fake debug user skipped user=%s symbol=%s tf=%s rule_id=%s",
                user_id,
                symbol,
                tf,
                rule_id,
            )
            return None
        strategy_version = detect_strategy_version(
            strategy_version=strategy_version or tg_user.get("strategy_version"),
            plan_code=tg_user.get("plan_code") or tg_user.get("subscription_type"),
            features_json=tg_user.get("features_json"),
            email=tg_user.get("email"),
            username=tg_user.get("username"),
            telegram_id=tg_user.get("telegram_id"),
        )
        subscription_type = self._subscription_type(tg_user)
        signal_meta = dict(signal_meta or {})
        signal_level = self._signal_level_from_meta(strategy_version, signal_meta)
        signal_score = self._int_or_none(signal_meta.get("signal_score"))
        position_size_multiplier = self._position_size_multiplier(strategy_version, signal_meta)
        log.info(
            "[user-strategy-resolve] user_id=%s telegram_id=%s email=%s username=%s plan=%s subscription_plan_id=%s raw_strategy_version=%s strategy_version=%s rule_id=%s mode=%s",
            user_id,
            tg_user.get("telegram_id"),
            tg_user.get("email"),
            tg_user.get("username"),
            tg_user.get("plan_code") or tg_user.get("subscription_type"),
            tg_user.get("subscription_plan_id") or tg_user.get("plan_id"),
            tg_user.get("strategy_version"),
            strategy_version,
            rule_id,
            trading_mode,
        )

        def log_decision(
            final_decision: str,
            reason: str,
            *,
            open_trades_snapshot: list | None = None,
            risk_allowed: bool | None = None,
            margin_ratio: float | None = None,
            time_sync_ok: bool | None = None,
            binance_ready: bool | None = None,
            signal: bool = True,
        ) -> None:
            self._log_entry_decision(
                user_id=user_id,
                mode=trading_mode,
                symbol=symbol,
                tf=tf,
                rule_id=rule_id,
                strategy_version=strategy_version,
                signal=signal,
                signal_level=signal_level,
                plan=tg_user.get("plan_code") or tg_user.get("subscription_type"),
                allowed_symbols=tg_user.get("allowed_symbols")
                or tg_user.get("enabled_symbols")
                or tg_user.get("symbols"),
                open_trades=open_trades_snapshot or open_trades_by_user.get(user_id, []),
                risk_allowed=risk_allowed,
                margin_ratio=margin_ratio,
                time_sync_ok=time_sync_ok,
                binance_ready=binance_ready,
                final_decision=final_decision,
                reason=reason,
            )
            if final_decision in {"skip", "block"}:
                self._increment_blocked_metric(
                    metrics,
                    strategy_version=strategy_version,
                    rule_id=rule_id,
                    signal_level=signal_level,
                    tf=tf,
                    mode=trading_mode,
                    reason=reason,
                )

        def log_sandbox(trade_created: bool, reason: str, trade_id: str | None = None) -> None:
            if trading_mode != "live":
                self._log_sandbox_entry(
                    user_id=user_id,
                    symbol=symbol,
                    tf=tf,
                    trade_created=trade_created,
                    reason=reason,
                    trade_id=trade_id,
                )

        def log_live_skip(gate: str, reason: str) -> None:
            if trading_mode == "live":
                self._log_live_entry_skipped(
                    user_id=user_id,
                    symbol=symbol,
                    gate=gate,
                    reason=reason,
                )

        if strategy_version == STRATEGY_VERSION_V2:
            strict_reason = self._v2_strict_entry_reject_reason(
                rule_id=rule_id,
                signal_level=signal_level,
                signal_meta=signal_meta,
            )
            if strict_reason is not None:
                await self.trade_events_repo.add_event(
                    trade_id=f"debug:global:{symbol}:{tf}:{bar.close_time}:{rule_id}:{user_id}",
                    event_type="ENTRY_BLOCKED_V2_STRICT_RULE",
                    ts=now_ms,
                    symbol=symbol,
                    user_id=user_id,
                    mode=trading_mode,
                    payload={
                        "reason": strict_reason,
                        "rule_id": rule_id,
                        "strategy_version": STRATEGY_VERSION_V2,
                        "signal_level": signal_level,
                        "final_signal": signal_meta.get("final_signal"),
                        "strong_result": signal_meta.get("strong_result"),
                        "medium_result": signal_meta.get("medium_result"),
                        "weak_result": signal_meta.get("weak_result"),
                        "strong_failed_conditions": signal_meta.get("strong_failed_conditions"),
                        "medium_failed_conditions": signal_meta.get("medium_failed_conditions"),
                        "weak_failed_conditions": signal_meta.get("weak_failed_conditions"),
                        "tf_entry": tf,
                        "features_ver": self.features_ver,
                    },
                )
                log.warning(
                    "[entry-async] V2 strict entry blocked symbol=%s tf=%s user=%s rule_id=%s reason=%s signal_level=%s final_signal=%s strong=%s medium=%s weak=%s",
                    symbol,
                    tf,
                    user_id,
                    rule_id,
                    strict_reason,
                    signal_level,
                    signal_meta.get("final_signal"),
                    signal_meta.get("strong_result"),
                    signal_meta.get("medium_result"),
                    signal_meta.get("weak_result"),
                )
                log_decision("skip", strict_reason, signal=False)
                log_sandbox(False, strict_reason)
                log_live_skip("strategy", strict_reason)
                return None

            indicator_values, missing_indicators = self._v2_required_indicator_values(
                bar=bar,
                signal_meta=signal_meta,
            )
            if missing_indicators:
                await self._emit_v2_missing_indicator_skip(
                    user_id=user_id,
                    symbol=symbol,
                    tf=tf,
                    bar=bar,
                    rule_id=rule_id,
                    now_ms=now_ms,
                    mode=trading_mode,
                    missing=missing_indicators,
                    indicator_values=indicator_values,
                )
                reason = "missing_indicators:" + ",".join(missing_indicators)
                log_decision("skip", reason, signal=False)
                log_sandbox(False, reason)
                log_live_skip("strategy", reason)
                return None

        if self.cooldowns_repo is not None:
            cooldown = await self.cooldowns_repo.get_active_cooldown(
                runtime_user_id=user_id,
                symbol=symbol,
                reason="loss_stop",
            )
            if cooldown is not None:
                cooldown_until = cooldown.get("cooldown_until")

                await self.trade_events_repo.add_event(
                    trade_id=f"debug:global:{symbol}:{tf}:{bar.close_time}:{rule_id}:{user_id}",
                    event_type="ENTRY_BLOCKED_COOLDOWN",
                    ts=now_ms,
                    symbol=symbol,
                    user_id=user_id,
                    mode=trading_mode,
                    payload={
                        "reason": "loss_stop_cooldown_active",
                        "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
                        "tf_entry": tf,
                        "rule_id": rule_id,
                        "features_ver": self.features_ver,
                    },
                )

                log.info(
                    "[entry-async] %s %s user=%s: blocked by cooldown until=%s",
                    symbol,
                    tf,
                    user_id,
                    cooldown_until,
                )
                log_decision("skip", "loss_stop_cooldown_active")
                log_sandbox(False, "loss_stop_cooldown_active")
                log_live_skip("cooldown", "loss_stop_cooldown_active")
                return None

        user_open_trades = open_trades_by_user.get(user_id, [])

        lock_key = self._portfolio_lock_key(user_id)
        symbol_lock_key = self._entry_lock_key(user_id, symbol)
        lock_owner = f"{symbol}:{tf}:{bar.close_time}:{rule_id}:{now_ms}"
        acquired_lock = False
        acquired_symbol_lock = False
        if hasattr(self.bot_state_repo, "acquire_lock"):
            lock_started_at = time.perf_counter()
            acquired_lock = await self._state_acquire_lock(
                key=lock_key,
                owner=lock_owner,
                ttl_ms=self.entry_lock_ttl_ms,
                user_id=user_id,
                symbol=symbol,
            )
            log.info(
                "[entry-lock] user_id=%s symbol=%s lock=portfolio wait_ms=%s acquired=%s",
                user_id,
                symbol,
                self._perf_ms_since(lock_started_at),
                acquired_lock,
            )
            if not acquired_lock:
                log.info(
                    "[entry-async] %s %s user=%s: skipped because portfolio lock is busy",
                    symbol,
                    tf,
                    user_id,
                )
                log_decision("skip", "portfolio_lock_busy", open_trades_snapshot=user_open_trades)
                log_sandbox(False, "portfolio_lock_busy")
                log_live_skip("portfolio_lock", "portfolio_lock_busy")
                return None

            lock_started_at = time.perf_counter()
            acquired_symbol_lock = await self._state_acquire_lock(
                key=symbol_lock_key,
                owner=lock_owner,
                ttl_ms=self.entry_lock_ttl_ms,
                user_id=user_id,
                symbol=symbol,
            )
            log.info(
                "[entry-lock] user_id=%s symbol=%s lock=symbol wait_ms=%s acquired=%s",
                user_id,
                symbol,
                self._perf_ms_since(lock_started_at),
                acquired_symbol_lock,
            )
            if not acquired_symbol_lock:
                log.info(
                    "[entry-async] %s %s user=%s: skipped because symbol lock is busy",
                    symbol,
                    tf,
                    user_id,
                )
                if acquired_lock and hasattr(self.bot_state_repo, "release_lock"):
                    await self._state_release_lock(
                        key=lock_key,
                        owner=lock_owner,
                        user_id=user_id,
                        symbol=symbol,
                    )
                log_decision("skip", "symbol_lock_busy", open_trades_snapshot=user_open_trades)
                log_sandbox(False, "symbol_lock_busy")
                log_live_skip("symbol_lock", "symbol_lock_busy")
                return None

        try:
            fresh_open_trades = self.active_trades_cache.get_user_open_trades(user_id)
            existing_cached_trade = self._find_open_trade_in_list(
                fresh_open_trades,
                user_id=user_id,
                symbol=symbol,
                mode=trading_mode,
            )
            log.info(
                "[duplicate-check] user_id=%s symbol=%s existing_open_trade=%s trade_id=%s source=cache_after_lock",
                user_id,
                symbol,
                existing_cached_trade is not None,
                getattr(existing_cached_trade, "trade_id", None),
            )

            enforce = self._enforce_subscription_limits(
                tg_user=tg_user,
                symbol=symbol,
                tf=tf,
                user_open_trades=fresh_open_trades,
            )
            if not enforce["allowed"]:
                if trading_mode == "live" and str(enforce["reason"]) == "open_trade_exists_for_symbol":
                    existing_trade = self._find_open_trade_in_list(
                        fresh_open_trades,
                        user_id=user_id,
                        symbol=symbol,
                        mode=trading_mode,
                    )
                    await self._emit_live_entry_skipped_active_position(
                        user_id=user_id,
                        symbol=symbol,
                        tf=tf,
                        bar=bar,
                        rule_id=rule_id,
                        now_ms=now_ms,
                        mode=trading_mode,
                        existing_trade=existing_trade,
                        reason="active_position_already_exists",
                    )
                    log.info(
                        "[entry-async] %s %s user=%s: entry skipped active position already exists existing_trade_id=%s",
                        symbol,
                        tf,
                        user_id,
                        getattr(existing_trade, "trade_id", None),
                    )
                    log_decision("skip", "active_position_already_exists", open_trades_snapshot=fresh_open_trades)
                    log_live_skip("active_position", "active_position_already_exists")
                    return None

                await self.trade_events_repo.add_event(
                    trade_id=f"debug:global:{symbol}:{tf}:{bar.close_time}:{rule_id}:{user_id}",
                    event_type=self._entry_block_event_type(str(enforce["reason"])),
                    ts=now_ms,
                    symbol=symbol,
                    user_id=user_id,
                    mode=trading_mode,
                    payload={
                        "reason": enforce["reason"],
                        "plan_code": tg_user.get("plan_code"),
                        "tf_entry": tf,
                        "rule_id": rule_id,
                        "features_ver": self.features_ver,
                        "current_open_trades": enforce.get("current_open_trades"),
                        "current_risk_trades": enforce.get("current_risk_trades"),
                        "max_open_trades_total": enforce.get("max_open_trades_total"),
                        "max_risk_trades": enforce.get("max_risk_trades"),
                    },
                )
                log.info(
                    "[entry-async] %s %s user=%s: blocked after lock reason=%s plan=%s",
                    symbol,
                    tf,
                    user_id,
                    enforce["reason"],
                    tg_user.get("plan_code"),
                )
                reason = str(enforce["reason"])
                log_decision("skip", reason, open_trades_snapshot=fresh_open_trades)
                log_sandbox(False, reason)
                log_live_skip("subscription", reason)
                return None

            policy_decision = self.entry_policy.can_open_trade(
                user_id=user_id,
                symbol=symbol,
                tf=tf,
                rule_id=rule_id,
                open_trades=fresh_open_trades,
            )

            if not policy_decision.allowed:
                if trading_mode == "live" and policy_decision.reason == "open_trade_exists_for_symbol":
                    existing_trade = self._find_open_trade_in_list(
                        fresh_open_trades,
                        user_id=user_id,
                        symbol=symbol,
                        mode=trading_mode,
                    )
                    await self._emit_live_entry_skipped_active_position(
                        user_id=user_id,
                        symbol=symbol,
                        tf=tf,
                        bar=bar,
                        rule_id=rule_id,
                        now_ms=now_ms,
                        mode=trading_mode,
                        existing_trade=existing_trade,
                        reason="active_position_already_exists",
                    )
                    log.info(
                        "[entry-async] %s %s user=%s: entry skipped duplicate live symbol existing_trade_id=%s",
                        symbol,
                        tf,
                        user_id,
                        getattr(existing_trade, "trade_id", None) or policy_decision.conflict_trade_id,
                    )
                    log_decision("skip", "active_position_already_exists", open_trades_snapshot=fresh_open_trades)
                    log_live_skip("active_position", "active_position_already_exists")
                    return None

                await self.trade_events_repo.add_event(
                    trade_id=f"debug:global:{symbol}:{tf}:{bar.close_time}:{rule_id}:{user_id}",
                    event_type="ENTRY_BLOCKED_SUBSCRIPTION",
                    ts=now_ms,
                    symbol=symbol,
                    user_id=user_id,
                    mode=trading_mode,
                    payload={
                        "reason": "open_trade_exists_for_symbol",
                        "plan_code": tg_user.get("plan_code"),
                        "tf_entry": tf,
                        "rule_id": rule_id,
                        "features_ver": self.features_ver,
                        "conflict_trade_id": policy_decision.conflict_trade_id,
                    },
                )
                log.info(
                    "[entry-async] %s %s user=%s: policy blocked rule_id=%s reason=%s conflict_trade_id=%s",
                    symbol,
                    tf,
                    user_id,
                    rule_id,
                    policy_decision.reason,
                    policy_decision.conflict_trade_id,
                )
                reason = str(policy_decision.reason)
                log_decision("skip", reason, open_trades_snapshot=fresh_open_trades)
                log_sandbox(False, reason)
                log_live_skip("entry_policy", reason)
                return None

            risk_snapshot: dict[str, float | int | str | None] = {}

            if trading_mode == "live":
                if self.per_user_live_risk_service is None:
                    await self.trade_events_repo.add_event(
                        trade_id=f"debug:global:{symbol}:{tf}:{bar.close_time}:{rule_id}:{user_id}",
                        event_type="ENTRY_BLOCKED_LIVE_RISK_SERVICE",
                        ts=now_ms,
                        symbol=symbol,
                        user_id=user_id,
                        mode=trading_mode,
                        payload={
                            "reason": "per_user_live_risk_service_unavailable",
                            "tf_entry": tf,
                            "rule_id": rule_id,
                        },
                    )
                    log_decision("skip", "per_user_live_risk_service_unavailable", open_trades_snapshot=fresh_open_trades)
                    log_live_skip("risk_service", "per_user_live_risk_service_unavailable")
                    return None

                risk_result = await self.per_user_live_risk_service.check_new_entry(
                    runtime_user_id=user_id,
                    symbol=symbol,
                    entry_price=live_entry_price,
                    default_stake_mode=str(tg_user.get("default_stake_mode", "percent")),
                    default_stake_value=float(tg_user.get("default_stake_value", 1.0) or 1.0),
                    min_entry_margin_usdt=float(tg_user.get("min_entry_margin_usdt", 0.0) or 0.0),
                    default_leverage=int(tg_user.get("default_leverage", 5) or 5),
                    max_margin_per_trade_mode=str(tg_user.get("max_margin_per_trade_mode", "percent")),
                    max_margin_per_trade_value=float(tg_user.get("max_margin_per_trade_value", 5.0) or 5.0),
                    margin_ratio_warn_pct=float(tg_user.get("margin_ratio_warn_pct", 6.0) or 6.0),
                    margin_ratio_block_pct=float(tg_user.get("margin_ratio_block_pct", 10.0) or 10.0),
                    position_size_multiplier=position_size_multiplier,
                )

                if not risk_result.allowed:
                    await self.trade_events_repo.add_event(
                        trade_id=f"debug:global:{symbol}:{tf}:{bar.close_time}:{rule_id}:{user_id}",
                        event_type="ENTRY_BLOCKED_RISK",
                        ts=now_ms,
                        symbol=symbol,
                        user_id=user_id,
                        mode=trading_mode,
                        payload={
                            "reason": risk_result.reason,
                            "available_balance_usdt": risk_result.available_balance_usdt,
                            "current_margin_ratio_pct": risk_result.current_margin_ratio_pct,
                            "symbol_max_leverage": risk_result.symbol_max_leverage,
                            "requested_leverage": risk_result.requested_leverage,
                            "final_leverage": risk_result.final_leverage,
                            "leverage_adjusted": risk_result.leverage_adjusted,
                            "required_margin_usdt": risk_result.required_margin_usdt,
                            "notional_usdt": risk_result.notional_usdt,
                            "base_stake_margin_usdt": getattr(risk_result, "base_stake_margin_usdt", None),
                            "position_size_multiplier": getattr(risk_result, "position_size_multiplier", position_size_multiplier),
                            "planned_qty": risk_result.planned_qty,
                            "planned_notional_usdt": risk_result.planned_notional_usdt,
                            "adjusted_qty": risk_result.adjusted_qty,
                            "adjusted_notional_usdt": risk_result.adjusted_notional_usdt,
                            "min_notional_usdt": risk_result.min_notional_usdt,
                            "qty_step": risk_result.qty_step,
                            "min_qty": risk_result.min_qty,
                            "qty_bump_applied": risk_result.qty_bump_applied,
                            "account_margin_used_usdt": risk_result.account_margin_used_usdt,
                            "account_margin_limit_usdt": risk_result.account_margin_limit_usdt,
                            "account_margin_usage_pct": risk_result.account_margin_usage_pct,
                            "account_margin_current_used_usdt": risk_result.account_margin_current_used_usdt,
                            "account_margin_after_entry_usdt": risk_result.account_margin_after_entry_usdt,
                            "account_margin_per_trade_limit_usdt": risk_result.account_margin_per_trade_limit_usdt,
                            "tf_entry": tf,
                            "rule_id": rule_id,
                        },
                    )
                    log.info(
                        "[entry-async] %s %s user=%s: blocked by risk reason=%s",
                        symbol,
                        tf,
                        user_id,
                        risk_result.reason,
                    )
                    log_decision(
                        "skip",
                        str(risk_result.reason),
                        open_trades_snapshot=fresh_open_trades,
                        risk_allowed=False,
                        margin_ratio=risk_result.account_margin_usage_pct,
                    )
                    log_live_skip("risk_limit", str(risk_result.reason))
                    return None

                pending_risk_warning_payload = None
                if risk_result.warn_user:
                    pending_risk_warning_payload = {
                        "warning_code": risk_result.warning_code,
                        "available_balance_usdt": risk_result.available_balance_usdt,
                        "current_margin_ratio_pct": risk_result.current_margin_ratio_pct,
                        "account_margin_used_usdt": risk_result.account_margin_used_usdt,
                        "account_margin_limit_usdt": risk_result.account_margin_limit_usdt,
                        "account_margin_usage_pct": risk_result.account_margin_usage_pct,
                        "tf_entry": tf,
                        "rule_id": rule_id,
                    }

                sizing = {
                    "stake_usd": float(risk_result.stake_margin_usdt),
                    "leverage": float(risk_result.leverage),
                    "qty": float(risk_result.qty),
                }
                risk_snapshot = {
                    "available_balance_usdt": risk_result.available_balance_usdt,
                    "current_margin_ratio_pct": risk_result.current_margin_ratio_pct,
                    "symbol_max_leverage": risk_result.symbol_max_leverage,
                    "requested_leverage": risk_result.requested_leverage,
                    "final_leverage": risk_result.final_leverage,
                    "leverage_adjusted": risk_result.leverage_adjusted,
                    "required_margin_usdt": risk_result.required_margin_usdt,
                    "notional_usdt": risk_result.notional_usdt,
                    "base_stake_margin_usdt": getattr(risk_result, "base_stake_margin_usdt", None),
                    "position_size_multiplier": getattr(risk_result, "position_size_multiplier", position_size_multiplier),
                    "planned_qty": risk_result.planned_qty,
                    "planned_notional_usdt": risk_result.planned_notional_usdt,
                    "adjusted_qty": risk_result.adjusted_qty,
                    "adjusted_notional_usdt": risk_result.adjusted_notional_usdt,
                    "min_notional_usdt": risk_result.min_notional_usdt,
                    "qty_step": risk_result.qty_step,
                    "min_qty": risk_result.min_qty,
                    "qty_bump_applied": risk_result.qty_bump_applied,
                    "account_margin_used_usdt": risk_result.account_margin_used_usdt,
                    "account_margin_limit_usdt": risk_result.account_margin_limit_usdt,
                    "account_margin_usage_pct": risk_result.account_margin_usage_pct,
                    "account_margin_current_used_usdt": risk_result.account_margin_current_used_usdt,
                    "account_margin_after_entry_usdt": risk_result.account_margin_after_entry_usdt,
                    "account_margin_per_trade_limit_usdt": risk_result.account_margin_per_trade_limit_usdt,
                }
                if risk_result.leverage_adjusted:
                    await self.trade_events_repo.add_event(
                        trade_id=f"debug:global:{symbol}:{tf}:{bar.close_time}:{rule_id}:{user_id}",
                        event_type="LEVERAGE_ADJUSTED",
                        ts=now_ms,
                        symbol=symbol,
                        user_id=user_id,
                        mode=trading_mode,
                        payload={
                            "symbol": symbol,
                            "requested_leverage": risk_result.requested_leverage,
                            "exchange_max": risk_result.symbol_max_leverage,
                            "final_leverage": risk_result.final_leverage,
                            "tf_entry": tf,
                            "rule_id": rule_id,
                        },
                    )
                    log.info(
                        "[entry-async] %s %s user=%s: leverage adjusted requested=x%s exchange_max=x%s final=x%s",
                        symbol,
                        tf,
                        user_id,
                        risk_result.requested_leverage,
                        risk_result.symbol_max_leverage,
                        risk_result.final_leverage,
                    )
                if risk_result.qty_bump_applied:
                    log.info(
                        "[entry-async] %s %s user=%s: qty bumped to min notional planned_qty=%s planned_notional=%.4f adjusted_qty=%s adjusted_notional=%.4f min_notional=%.4f required_margin=%.4f margin_after=%s margin_limit=%s",
                        symbol,
                        tf,
                        user_id,
                        risk_result.planned_qty,
                        risk_result.planned_notional_usdt,
                        risk_result.adjusted_qty,
                        risk_result.adjusted_notional_usdt,
                        risk_result.min_notional_usdt,
                        risk_result.required_margin_usdt,
                        risk_result.account_margin_after_entry_usdt,
                        risk_result.account_margin_limit_usdt,
                    )

            else:
                pending_risk_warning_payload = None
                history = []
                sizing = self._build_user_sizing_sandbox(
                    tg_user=tg_user,
                    entry_price=live_entry_price,
                    history=history,
                    position_size_multiplier=position_size_multiplier,
                )
                risk_snapshot = {
                    "base_stake_margin_usdt": sizing.get("base_stake_usd"),
                    "position_size_multiplier": position_size_multiplier,
                }

            entry_price = live_entry_price
            exit_profile = get_exit_profile(strategy_version, signal_level=signal_level)
            sl_price = entry_price * (1.0 - (exit_profile.initial_sl_pct / 100.0))
            entry_indicators = self._build_entry_indicators(
                bar=bar,
                symbol=symbol,
                tf=tf,
                rule_id=rule_id,
                strategy_version=strategy_version,
                signal_meta=signal_meta,
                signal_level=signal_level,
                signal_score=signal_score,
                position_size_multiplier=position_size_multiplier,
                exit_profile=exit_profile_to_dict(exit_profile),
                signal_entry_price=signal_entry_price,
                live_entry_price=live_entry_price,
                deviation_pct=deviation_pct,
                entry_price_check=entry_price_check,
            )

            trade = Trade(
                trade_id=f"{user_id}:{symbol}:{tf}:{bar.close_time}:{rule_id}",
                user_id=user_id,
                symbol=symbol,
                side=Side.LONG,
                status=TradeStatus.OPEN,
                opened_at=now_ms,
                entry_price=entry_price,
                qty=sizing["qty"],
                leverage=int(sizing["leverage"]),
                stake_usd=sizing["stake_usd"],
                tf_entry=tf,
                model_id=rule_id,
                entry_bar_close_time=bar.close_time,
                sl_price=sl_price,
                mode=trading_mode,
                execution_engine="binance_futures_live" if trading_mode == "live" else "paper",
                exchange_name="binance" if trading_mode == "live" else None,
                strategy_version=strategy_version,
                subscription_type=subscription_type,
                exit_profile=exit_profile_to_dict(exit_profile),
                signal_level=signal_level,
                signal_score=signal_score,
                position_size_multiplier=position_size_multiplier,
                signal_debug=self._build_signal_debug_payload(entry_indicators),
                tp_hit_count=0,
                remaining_pct=1.0,
                exit_last_check_at=now_ms,
                entry_indicators=entry_indicators,
                qty_requested=sizing["qty"],
                qty_remaining=sizing["qty"],
                realized_pnl_usd=0.0,
                soft_stop_enabled=exit_profile.soft_stop_enabled,
                exchange_safety_sl_price=sl_price,
            )

            conflict_trade = await self._find_open_trade_for_symbol(
                user_id=user_id,
                symbol=symbol,
                mode=trading_mode,
            )
            log.info(
                "[duplicate-check] user_id=%s symbol=%s existing_open_trade=%s trade_id=%s source=final_pre_create",
                user_id,
                symbol,
                conflict_trade is not None,
                getattr(conflict_trade, "trade_id", None),
            )
            if conflict_trade is not None:
                if trading_mode == "live":
                    await self._emit_live_entry_skipped_active_position(
                        user_id=user_id,
                        symbol=symbol,
                        tf=tf,
                        bar=bar,
                        rule_id=rule_id,
                        now_ms=now_ms,
                        mode=trading_mode,
                        existing_trade=conflict_trade,
                        reason="active_position_already_exists",
                    )
                    log.info(
                        "[entry-async] %s %s user=%s: entry skipped final duplicate live symbol existing_trade_id=%s",
                        symbol,
                        tf,
                        user_id,
                        getattr(conflict_trade, "trade_id", None),
                    )
                    log_decision("skip", "active_position_already_exists", open_trades_snapshot=fresh_open_trades)
                    log_live_skip("active_position", "active_position_already_exists")
                    return None

                await self.trade_events_repo.add_event(
                    trade_id=f"debug:global:{symbol}:{tf}:{bar.close_time}:{rule_id}:{user_id}",
                    event_type="ENTRY_BLOCKED_SUBSCRIPTION",
                    ts=now_ms,
                    symbol=symbol,
                    user_id=user_id,
                    mode=trading_mode,
                    payload={
                        "reason": "open_trade_exists_for_symbol",
                        "guard": "final_pre_create",
                        "conflict_trade_id": getattr(conflict_trade, "trade_id", None),
                        "plan_code": tg_user.get("plan_code"),
                        "tf_entry": tf,
                        "rule_id": rule_id,
                        "features_ver": self.features_ver,
                    },
                )
                log.info(
                    "[entry-async] %s %s user=%s: final guard blocked duplicate symbol conflict_trade_id=%s",
                    symbol,
                    tf,
                    user_id,
                    getattr(conflict_trade, "trade_id", None),
                )
                log_decision("skip", "open_trade_exists_for_symbol", open_trades_snapshot=fresh_open_trades)
                log_sandbox(False, "open_trade_exists_for_symbol")
                return None

            try:
                order_started_at = time.perf_counter()
                trade = await self._maybe_await(self.broker.open_position(trade))
                if metrics is not None:
                    metrics["order_trade_creation_ms"] = (
                        int(metrics.get("order_trade_creation_ms", 0)) + self._perf_ms_since(order_started_at)
                    )
            except Exception as exc:
                if metrics is not None:
                    metrics["order_trade_creation_ms"] = (
                        int(metrics.get("order_trade_creation_ms", 0)) + self._perf_ms_since(order_started_at)
                    )
                if trading_mode == "live" and self._is_active_position_skip_exception(exc):
                    await self._emit_live_entry_skipped_active_position(
                        user_id=user_id,
                        symbol=symbol,
                        tf=tf,
                        bar=bar,
                        rule_id=rule_id,
                        now_ms=now_ms,
                        mode=trading_mode,
                        existing_trade=None,
                        reason="exchange_active_position_already_exists",
                        attempted_trade=trade,
                        exchange_details=self._parse_active_position_skip_details(str(exc)),
                    )
                    log.info(
                        "[entry-async] %s %s user=%s: entry skipped active exchange position already exists error=%s",
                        symbol,
                        tf,
                        user_id,
                        self._sanitize_error_text(str(exc)),
                    )
                    log_decision("skip", "exchange_active_position_already_exists", open_trades_snapshot=fresh_open_trades)
                    log_live_skip("active_position", "exchange_active_position_already_exists")
                    return None

                error_payload = self._build_exception_payload(
                    exc,
                    tf=tf,
                    rule_id=rule_id,
                )
                await self.trade_events_repo.add_event(
                    trade_id=trade.trade_id,
                    event_type="LIVE_ORDER_REJECTED" if trading_mode == "live" else "ENTRY_EXECUTION_FAILED",
                    ts=now_ms,
                    symbol=trade.symbol,
                    user_id=trade.user_id,
                    mode=trade.mode,
                    payload=error_payload,
                )
                log.exception(
                    "[entry-async] %s %s user=%s: broker open failed mode=%s",
                    symbol,
                    tf,
                    user_id,
                    trading_mode,
                )
                reason = self._sanitize_error_text(str(error_payload.get("error") or exc))
                log_decision(
                    "skip",
                    reason,
                    open_trades_snapshot=fresh_open_trades,
                    risk_allowed=(True if trading_mode == "live" else None),
                    margin_ratio=self._to_optional_float(risk_snapshot.get("account_margin_usage_pct")),
                )
                if trading_mode == "live":
                    gate, gate_reason = self._live_exception_gate(exc)
                    log_live_skip(gate, gate_reason)
                else:
                    log_sandbox(False, reason, trade.trade_id)
                return None

            persisted = await self._runtime_persistence_write(
                "trades.create_trade",
                lambda: self.trades_repo.create_trade(trade),
                user_id=user_id,
                symbol=symbol,
            )
            if not persisted:
                log.error(
                    "[entry-async] %s %s user=%s: trade persistence failed, cache/event enqueue skipped trade_id=%s",
                    symbol,
                    tf,
                    user_id,
                    trade.trade_id,
                )
                log_decision("skip", "trade_persistence_failed", open_trades_snapshot=fresh_open_trades)
                log_sandbox(False, "trade_persistence_failed", trade.trade_id)
                log_live_skip("persistence", "trade_persistence_failed")
                return None

            self.active_trades_cache.upsert(trade)
            await self._emit_live_protection_alert_if_needed(trade=trade, ts=now_ms)
            signal_debug_payload = self._build_signal_debug_payload(entry_indicators)

            await self.trade_events_repo.add_event(
                trade_id=trade.trade_id,
                event_type="OPENED",
                ts=now_ms,
                symbol=trade.symbol,
                user_id=trade.user_id,
                mode=trade.mode,
                payload={
                    "telegram_id": tg_user.get("telegram_id"),
                    "side": getattr(trade.side, "value", str(trade.side)),
                    "entry_price": trade.entry_price,
                    "signal_entry_price": signal_entry_price,
                    "live_entry_price": live_entry_price,
                    "entry_price_deviation_pct": deviation_pct,
                    "entry_price_rule_id": (entry_price_check or {}).get("entry_price_rule_id"),
                    "entry_price_rule_version": (entry_price_check or {}).get("entry_price_rule_version"),
                    "entry_price_upward_pct": (entry_price_check or {}).get("upward_pct"),
                    "entry_price_downward_pct": (entry_price_check or {}).get("downward_pct"),
                    "entry_price_max_upward_deviation_pct": (entry_price_check or {}).get("max_upward_deviation_pct"),
                    "entry_price_max_downward_deviation_pct": (entry_price_check or {}).get("max_downward_deviation_pct"),
                    "qty": trade.qty,
                    "stake_usd": trade.stake_usd,
                    "sl_price": trade.sl_price,
                    "tf_entry": trade.tf_entry,
                    "model_id": trade.model_id,
                    "rule_id": rule_id,
                    "strategy_version": strategy_version,
                    "subscription_type": subscription_type,
                    "signal_level": getattr(trade, "signal_level", None),
                    "signal_score": getattr(trade, "signal_score", None),
                    "position_size_multiplier": getattr(trade, "position_size_multiplier", 1.0),
                    "exit_profile": getattr(trade, "exit_profile", None),
                    "tp_step_pct": (getattr(trade, "exit_profile", None) or {}).get("tp_step_pct"),
                    "soft_stop_enabled": getattr(trade, "soft_stop_enabled", False),
                    "soft_stop_activation_pct": (getattr(trade, "exit_profile", None) or {}).get("soft_stop_activation_pct"),
                    "soft_stop_start_pct": (getattr(trade, "exit_profile", None) or {}).get("soft_stop_start_pct"),
                    "soft_stop_increment_pct": (getattr(trade, "exit_profile", None) or {}).get("soft_stop_increment_pct"),
                    "soft_stop_hourly_increment_pct": (getattr(trade, "exit_profile", None) or {}).get("soft_stop_hourly_increment_pct"),
                    "soft_stop_increment_interval_seconds": (getattr(trade, "exit_profile", None) or {}).get("soft_stop_increment_interval_seconds"),
                    "exchange_safety_sl_price": getattr(trade, "exchange_safety_sl_price", None),
                    "features_ver": self.features_ver,
                    "entry_bar_close_time": trade.entry_bar_close_time,
                    "rsi14": (bar.features or {}).get("rsi14"),
                    "supertrend_bullish_10_3_0": (bar.features or {}).get("supertrend_bullish_10_3_0"),
                    "entry_indicators": entry_indicators,
                      "signal_debug": signal_debug_payload,
                      **signal_debug_payload,
                      "default_leverage": tg_user.get("default_leverage", 5),
                      "leverage": trade.leverage,
                      "plan_code": tg_user.get("plan_code"),
                    "exchange_name": trade.exchange_name,
                    "exchange_entry_order_id": trade.exchange_entry_order_id,
                    "exchange_entry_status": trade.exchange_entry_status,
                    "risk_warning_pending": pending_risk_warning_payload is not None,
                    "protection_status": getattr(trade, "protection_status", "protected"),
                    "protection_error": getattr(trade, "protection_error", None),
                    "tp_count_original": (getattr(trade, "protection_details", None) or {}).get("tp_count_original"),
                    "tp_count_forced_by_small_margin": bool(
                        (getattr(trade, "protection_details", None) or {}).get("tp_count_forced_by_small_margin")
                    ),
                    "tp_count_force_reason": (getattr(trade, "protection_details", None) or {}).get("tp_count_force_reason"),
                    "entry_margin_usdt": (getattr(trade, "protection_details", None) or {}).get("entry_margin_usdt"),
                    "position_notional": (getattr(trade, "protection_details", None) or {}).get("position_notional"),
                    "tp_count": getattr(trade, "tp_count", 0),
                    "tp_levels": list(getattr(trade, "tp_levels", []) or []),
                    "tp_close_fractions": list(getattr(trade, "tp_close_fractions", []) or []),
                    "tp_plan": list(getattr(trade, "tp_plan", []) or []),
                    "tp_order_ids": list(getattr(trade, "tp_order_ids", []) or []),
                    "tp_algo_ids": list(getattr(trade, "tp_algo_ids", []) or []),
                    **risk_snapshot,
                },
            )

            risk_warning_queued = False
            if pending_risk_warning_payload is not None:
                risk_warning_payload = {
                    **pending_risk_warning_payload,
                    "entry_trade_id": trade.trade_id,
                    "entry_notification_required": True,
                }
                await self.trade_events_repo.add_event(
                    trade_id=trade.trade_id,
                    event_type="ENTRY_WARNING_RISK",
                    ts=now_ms + 1,
                    symbol=trade.symbol,
                    user_id=trade.user_id,
                    mode=trade.mode,
                    payload=risk_warning_payload,
                )
                risk_warning_queued = True

            log.info(
                "[notification-flow] trade_id=%s symbol=%s user=%s open_message_queued=True open_message_sent=False risk_warning_queued=%s risk_warning_sent=False",
                trade.trade_id,
                trade.symbol,
                trade.user_id,
                risk_warning_queued,
            )

            log.info(
                "[entry-async] %s %s: CREATED trade_id=%s user=%s mode=%s strategy_version=%s signal_entry=%.4f live_entry=%.4f stake=%.4f leverage=x%s qty=%s plan=%s",
                symbol,
                tf,
                trade.trade_id,
                user_id,
                trading_mode,
                strategy_version,
                signal_entry_price,
                live_entry_price,
                trade.stake_usd,
                trade.leverage,
                trade.qty,
                tg_user.get("plan_code"),
            )
            log_decision(
                "open",
                "trade_created",
                open_trades_snapshot=[*fresh_open_trades, trade],
                risk_allowed=(True if trading_mode == "live" else None),
                margin_ratio=self._to_optional_float(risk_snapshot.get("account_margin_usage_pct")),
            )
            log_sandbox(True, "trade_created", trade.trade_id)

            return trade
        finally:
            if acquired_symbol_lock and hasattr(self.bot_state_repo, "release_lock"):
                await self._state_release_lock(
                    key=symbol_lock_key,
                    owner=lock_owner,
                    user_id=user_id,
                    symbol=symbol,
                )
            if acquired_lock and hasattr(self.bot_state_repo, "release_lock"):
                await self._state_release_lock(
                    key=lock_key,
                    owner=lock_owner,
                    user_id=user_id,
                    symbol=symbol,
                )

    def _log_entry_decision(
        self,
        *,
        user_id: str,
        mode: str,
        symbol: str,
        tf: str,
        rule_id: str,
        strategy_version: str,
        signal: bool,
        signal_level: str | None,
        plan: Any,
        allowed_symbols: Any,
        open_trades: list | None,
        risk_allowed: bool | None,
        margin_ratio: float | None,
        time_sync_ok: bool | None,
        binance_ready: bool | None,
        final_decision: str,
        reason: str,
    ) -> None:
        open_trades = list(open_trades or [])
        normalized_symbol = str(symbol).upper()
        has_open_trade_for_symbol = any(
            str(getattr(trade, "symbol", "")).upper() == normalized_symbol
            for trade in open_trades
        )
        log.info(
            "[entry-decision] user_id=%s mode=%s symbol=%s tf=%s rule_id=%s strategy_version=%s signal=%s signal_level=%s plan=%s allowed_symbols=%s has_open_trade_for_symbol=%s open_trades_total=%s risk_allowed=%s margin_ratio=%s time_sync_ok=%s binance_ready=%s final_decision=%s reason=%s",
            user_id,
            mode,
            normalized_symbol,
            tf,
            rule_id,
            strategy_version,
            signal,
            signal_level,
            plan,
            self._summarize_allowed_symbols(allowed_symbols),
            has_open_trade_for_symbol,
            len(open_trades),
            risk_allowed,
            margin_ratio,
            time_sync_ok,
            binance_ready,
            final_decision,
            self._sanitize_error_text(str(reason)),
        )

    def _log_sandbox_entry(
        self,
        *,
        user_id: str,
        symbol: str,
        tf: str,
        trade_created: bool,
        reason: str,
        trade_id: str | None = None,
    ) -> None:
        log.info(
            "[sandbox-entry] user_id=%s symbol=%s tf=%s trade_created=%s reason=%s trade_id=%s",
            user_id,
            symbol,
            tf,
            trade_created,
            self._sanitize_error_text(str(reason)),
            trade_id,
        )

    def _log_live_entry_skipped(
        self,
        *,
        user_id: str,
        symbol: str,
        gate: str,
        reason: str,
    ) -> None:
        log.info(
            "[LIVE_ENTRY_SKIPPED] symbol=%s user_id=%s gate=%s reason=%s",
            symbol,
            user_id,
            gate,
            self._sanitize_error_text(str(reason)),
        )

    def _summarize_allowed_symbols(self, allowed_symbols: Any) -> Any:
        if allowed_symbols is None:
            return None
        if isinstance(allowed_symbols, str):
            if allowed_symbols.lower() == "all":
                return "all"
            return allowed_symbols if len(allowed_symbols) <= 160 else f"{allowed_symbols[:157]}..."
        if isinstance(allowed_symbols, (list, tuple, set)):
            values = [str(item) for item in allowed_symbols]
            if len(values) <= 12:
                return values
            return {"count": len(values), "first": values[:12]}
        return allowed_symbols

    def _live_exception_gate(self, exc: Exception) -> tuple[str, str]:
        text = str(exc)
        lowered = text.lower()
        if "-1021" in lowered or "timestamp" in lowered or "time_sync" in lowered:
            return "time_sync", "binance_time_sync_unhealthy"
        if "notional" in lowered:
            return "min_notional", text
        if "qty" in lowered or "quantity" in lowered or "precision" in lowered:
            return "symbol_filters", text
        if "leverage" in lowered:
            return "leverage", text
        if "margin" in lowered:
            return "margin_type", text
        if "position" in lowered and "active" in lowered:
            return "active_position", text
        return "broker_open", text

    async def _emit_live_entry_skipped_active_position(
        self,
        *,
        user_id: str,
        symbol: str,
        tf: str,
        bar,
        rule_id: str,
        now_ms: int,
        mode: str,
        existing_trade: Trade | None,
        reason: str,
        attempted_trade: Trade | None = None,
        exchange_details: dict[str, Any] | None = None,
    ) -> None:
        exchange_details = exchange_details or {}
        open_order_summaries = self._open_order_summaries_from_trade(existing_trade)
        open_order_types = self._open_order_types_from_details(exchange_details)
        if not open_order_types:
            open_order_types = [item["type"] for item in open_order_summaries if item.get("type")]

        existing_trade_status = self._trade_status(existing_trade) if existing_trade is not None else None
        payload = {
            "reason": reason,
            "user_id": user_id,
            "symbol": symbol,
            "existing_trade_id": getattr(existing_trade, "trade_id", None),
            "attempted_trade_id": getattr(attempted_trade, "trade_id", None),
            "existing_trade_status": existing_trade_status,
            "position_amt": self._first_not_none(
                getattr(existing_trade, "exchange_position_amt", None) if existing_trade is not None else None,
                exchange_details.get("position_amt"),
            ),
            "positionSide": self._first_not_none(
                getattr(existing_trade, "exchange_position_side", None) if existing_trade is not None else None,
                exchange_details.get("position_side"),
            ),
            "open_order_count": self._first_not_none(
                len(open_order_summaries) if open_order_summaries else None,
                self._to_optional_int(exchange_details.get("open_order_count")),
                0,
            ),
            "open_order_types": open_order_types,
            "open_orders": open_order_summaries or exchange_details.get("open_orders"),
            "new_signal_tf_entry": tf,
            "new_signal_rule_id": rule_id,
            "features_ver": self.features_ver,
        }
        await self.trade_events_repo.add_event(
            trade_id=f"debug:global:{symbol}:{tf}:{bar.close_time}:{rule_id}:{user_id}",
            event_type="LIVE_ENTRY_SKIPPED_ACTIVE_POSITION",
            ts=now_ms,
            symbol=symbol,
            user_id=user_id,
            mode=mode,
            payload=payload,
        )

    async def _state_get_int(self, key: str, *, default: int, symbol: str | None = None) -> int:
        try:
            value = await self._maybe_await(self.bot_state_repo.get_int(key, default=default))
            log.debug("[cache] bot_state get key=%s symbol=%s value=%s", key, symbol, value)
            return int(value)
        except Exception as exc:
            log.warning(
                "[cache] bot_state get failed key=%s symbol=%s fallback=%s error=%s:%s",
                key,
                symbol,
                default,
                type(exc).__name__,
                exc,
            )
            return int(default)

    async def _state_set_int(self, key: str, value: int, *, symbol: str | None = None) -> None:
        try:
            await self._maybe_await(self.bot_state_repo.set_int(key, int(value)))
            log.debug("[cache] bot_state set key=%s symbol=%s value=%s", key, symbol, value)
        except Exception as exc:
            log.warning(
                "[cache] bot_state set failed key=%s symbol=%s value=%s error=%s:%s",
                key,
                symbol,
                value,
                type(exc).__name__,
                exc,
            )

    async def _state_acquire_lock(
        self,
        *,
        key: str,
        owner: str,
        ttl_ms: int,
        user_id: str,
        symbol: str,
    ) -> bool:
        try:
            acquired = await self._maybe_await(
                self.bot_state_repo.acquire_lock(
                    key=key,
                    owner=owner,
                    ttl_ms=ttl_ms,
                )
            )
            return bool(acquired)
        except Exception as exc:
            log.warning(
                "[cache] bot_state lock failed key=%s user=%s symbol=%s fallback_allow=True error=%s:%s",
                key,
                user_id,
                symbol,
                type(exc).__name__,
                exc,
            )
            return True

    async def _state_release_lock(self, *, key: str, owner: str, user_id: str, symbol: str) -> None:
        try:
            await self._maybe_await(self.bot_state_repo.release_lock(key=key, owner=owner))
        except Exception as exc:
            log.warning(
                "[cache] bot_state release lock failed key=%s user=%s symbol=%s error=%s:%s",
                key,
                user_id,
                symbol,
                type(exc).__name__,
                exc,
            )

    async def _bars_get_tail(
        self,
        *,
        symbol: str,
        tf: str,
        n: int,
        require_features_ok: bool,
    ) -> list:
        try:
            rows = await self._maybe_await(
                self.bars_repo.get_tail(
                    symbol=symbol,
                    tf=tf,
                    n=n,
                    features_ver=self.features_ver,
                    require_features_ok=require_features_ok,
                )
            )
            log.debug("[cache] bars get_tail symbol=%s tf=%s n=%s rows=%s", symbol, tf, n, len(rows or []))
            return list(rows or [])
        except Exception as exc:
            log.warning(
                "[cache] bars get_tail failed symbol=%s tf=%s fallback_empty=True error=%s:%s",
                symbol,
                tf,
                type(exc).__name__,
                exc,
            )
            return []

    async def _runtime_persistence_write(
        self,
        operation: str,
        call_factory,
        *,
        user_id: str | None = None,
        symbol: str | None = None,
        timeout_seconds: float = MONGO_RUNTIME_TIMEOUT_SECONDS,
    ) -> bool:
        started = time.perf_counter()
        try:
            await self._await_runtime_value(call_factory(), timeout_seconds=timeout_seconds)
            duration_ms = int((time.perf_counter() - started) * 1000)
            log.debug(
                "[entry-async][persistence] operation=%s user=%s symbol=%s duration_ms=%s skipped=False",
                operation,
                user_id,
                symbol,
                duration_ms,
            )
            return True
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log.warning(
                "[entry-async][persistence] operation=%s user=%s symbol=%s duration_ms=%s skipped=True error=%s:%s",
                operation,
                user_id,
                symbol,
                duration_ms,
                type(exc).__name__,
                exc,
            )
            return False

    async def _await_runtime_value(self, value, *, timeout_seconds: float):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # Some unit tests run under trio via pytest-anyio. Production runtime is asyncio,
            # so timeout enforcement still applies in the worker process.
            return await self._maybe_await(value)

        return await asyncio.wait_for(self._maybe_await(value), timeout=timeout_seconds)

    async def _maybe_await(self, value):
        if inspect.isawaitable(value):
            return await value
        return value

    async def _find_open_trade_for_symbol(self, *, user_id: str, symbol: str, mode: str):
        return self.active_trades_cache.get_open_trade(
            user_id=user_id,
            symbol=symbol,
            mode=mode,
        )

    def _find_open_trade_in_list(self, trades: list, *, user_id: str, symbol: str, mode: str) -> Trade | None:
        normalized_symbol = str(symbol).upper()
        for trade in trades:
            if str(getattr(trade, "user_id", "")) != str(user_id):
                continue
            if str(getattr(trade, "symbol", "")).upper() != normalized_symbol:
                continue
            if str(getattr(trade, "mode", mode)) != mode:
                continue
            if self._trade_status(trade) != TradeStatus.OPEN.value:
                continue
            return trade
        return None

    def _open_order_summaries_from_trade(self, trade: Trade | None) -> list[dict[str, Any]]:
        if trade is None:
            return []
        orders: list[dict[str, Any]] = []
        for order in list(getattr(trade, "exchange_tp_orders", None) or []):
            if isinstance(order, dict) and self._is_active_exchange_order(order):
                orders.append(self._order_summary(order))
        stop_order = getattr(trade, "exchange_stop_order", None)
        if isinstance(stop_order, dict) and self._is_active_exchange_order(stop_order):
            orders.append(self._order_summary(stop_order))
        return orders

    def _order_summary(self, order: dict[str, Any]) -> dict[str, Any]:
        return {
            "orderId": self._string_or_none(order.get("orderId") or order.get("actualOrderId")),
            "algoId": self._string_or_none(order.get("algoId")),
            "clientOrderId": self._string_or_none(order.get("clientOrderId") or order.get("clientAlgoId")),
            "type": self._string_or_none(order.get("type") or order.get("orderType")),
            "status": self._string_or_none(order.get("status") or order.get("algoStatus")),
        }

    def _is_active_exchange_order(self, order: dict[str, Any]) -> bool:
        status = str(order.get("status") or order.get("algoStatus") or "").upper()
        return status in {"", "NEW", "PARTIALLY_FILLED", "ACCEPTED"}

    def _is_active_position_skip_exception(self, exc: Exception) -> bool:
        return str(exc).startswith("pre_entry_cleanup_blocked_active_position:")

    def _parse_active_position_skip_details(self, text: str) -> dict[str, Any]:
        raw = str(text).split(":", 1)[1] if ":" in str(text) else str(text)
        details: dict[str, Any] = {}
        for part in raw.split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            details[key.strip()] = self._sanitize_error_text(value.strip())
        open_orders = details.get("open_orders")
        if open_orders:
            details["open_order_types"] = self._open_order_types_from_text(str(open_orders))
        return details

    def _open_order_types_from_details(self, details: dict[str, Any]) -> list[str]:
        raw_types = details.get("open_order_types")
        if isinstance(raw_types, list):
            return [str(item) for item in raw_types if item]
        open_orders = details.get("open_orders")
        if open_orders:
            return self._open_order_types_from_text(str(open_orders))
        return []

    def _open_order_types_from_text(self, text: str) -> list[str]:
        types = re.findall(r'"(?:type|orderType)":"([^"]+)"', str(text))
        seen: set[str] = set()
        result: list[str] = []
        for item in types:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    def _trade_status(self, trade: Trade | None) -> str | None:
        if trade is None:
            return None
        status = getattr(trade, "status", None)
        return status.value if hasattr(status, "value") else str(status)

    def _first_not_none(self, *values):
        for value in values:
            if value is not None:
                return value
        return None

    def _to_optional_int(self, value) -> int | None:
        try:
            if value is None or value == "":
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _to_optional_float(self, value) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _string_or_none(self, value) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    async def _emit_live_protection_alert_if_needed(self, *, trade: Trade, ts: int) -> None:
        if str(getattr(trade, "mode", "")) != "live":
            return

        protection_status = str(getattr(trade, "protection_status", "protected") or "protected")
        if protection_status == "protected":
            return

        event_type = "LIVE_POSITION_UNPROTECTED" if protection_status == "unprotected" else "LIVE_PROTECTION_SETUP_FAILED"
        payload = {
            "protection_status": protection_status,
            "protection_error": getattr(trade, "protection_error", None),
            **dict(getattr(trade, "protection_details", None) or {}),
        }
        await self.trade_events_repo.add_event(
            trade_id=trade.trade_id,
            event_type=event_type,
            ts=ts,
            symbol=trade.symbol,
            user_id=trade.user_id,
            mode=trade.mode,
            payload=payload,
        )

    def _build_exception_payload(self, exc: Exception, *, tf: str, rule_id: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "reason": type(exc).__name__,
            "tf_entry": tf,
            "rule_id": rule_id,
            "features_ver": self.features_ver,
        }

        response = getattr(exc, "response", None)
        if response is None:
            payload["error"] = self._sanitize_error_text(str(exc))
            return payload

        status_code = getattr(response, "status_code", None)
        reason_phrase = str(getattr(response, "reason_phrase", "") or "").strip()

        if status_code is not None:
            payload["http_status"] = int(status_code)
        if reason_phrase:
            payload["http_reason"] = reason_phrase

        request = getattr(response, "request", None)
        url = getattr(request, "url", None) if request is not None else None
        if url is not None:
            payload["http_path"] = str(getattr(url, "path", "") or str(url).split("?", 1)[0])

        response_payload = self._response_payload(response)
        exchange_message = None
        if isinstance(response_payload, dict):
            if response_payload.get("code") is not None:
                payload["exchange_error_code"] = response_payload.get("code")
            if response_payload.get("msg") is not None:
                exchange_message = str(response_payload.get("msg"))
                payload["exchange_error_message"] = self._sanitize_error_text(exchange_message)
        elif response_payload:
            payload["exchange_error_text"] = self._sanitize_error_text(str(response_payload))

        safe_summary = exchange_message or payload.get("exchange_error_text") or reason_phrase or str(exc)
        payload["error"] = self._sanitize_error_text(f"HTTP {status_code}: {safe_summary}")
        return payload

    def _response_payload(self, response) -> dict[str, Any] | str | None:
        try:
            payload = response.json()
        except Exception:
            try:
                return self._truncate(str(response.text or ""))
            except Exception:
                return None
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return self._truncate(str(payload))
        return None

    def _sanitize_error_text(self, text: str) -> str:
        sanitized = re.sub(r"([?&]signature=)[^&\s']+", r"\1<redacted>", str(text))
        sanitized = re.sub(r"(signature=)[^&\s']+", r"\1<redacted>", sanitized)
        return self._truncate(sanitized)

    @staticmethod
    def _truncate(text: str, limit: int = 500) -> str:
        text = str(text)
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def _check_long_entry_price(
        self,
        *,
        signal_entry_price: float,
        live_entry_price: float,
    ) -> dict[str, float | str | bool]:
        """
        LONG asymmetric rule:
        - if live > signal by more than 0.5% -> block
        - if live < signal by up to 2.0% -> allow
        - if live < signal by more than 2.0% -> block
        """
        max_upward_deviation_pct = LONG_MAX_UPWARD_DEVIATION_PCT
        max_downward_deviation_pct = LONG_MAX_DOWNWARD_DEVIATION_PCT

        upward_pct = 0.0
        downward_pct = 0.0

        if live_entry_price > signal_entry_price:
            upward_pct = ((live_entry_price - signal_entry_price) / signal_entry_price) * 100.0
            if upward_pct > max_upward_deviation_pct:
                return {
                    "allowed": False,
                    "reason": "live_price_too_high_for_long",
                    "entry_price_rule_id": LONG_ENTRY_PRICE_RULE_ID,
                    "entry_price_rule_version": LONG_ENTRY_PRICE_RULE_VERSION,
                    "upward_pct": upward_pct,
                    "downward_pct": 0.0,
                    "max_upward_deviation_pct": max_upward_deviation_pct,
                    "max_downward_deviation_pct": max_downward_deviation_pct,
                    "effective_deviation_pct": upward_pct,
                }

        elif live_entry_price < signal_entry_price:
            downward_pct = ((signal_entry_price - live_entry_price) / signal_entry_price) * 100.0
            if downward_pct > max_downward_deviation_pct:
                return {
                    "allowed": False,
                    "reason": "live_price_too_low_for_long",
                    "entry_price_rule_id": LONG_ENTRY_PRICE_RULE_ID,
                    "entry_price_rule_version": LONG_ENTRY_PRICE_RULE_VERSION,
                    "upward_pct": 0.0,
                    "downward_pct": downward_pct,
                    "max_upward_deviation_pct": max_upward_deviation_pct,
                    "max_downward_deviation_pct": max_downward_deviation_pct,
                    "effective_deviation_pct": downward_pct,
                }

        return {
            "allowed": True,
            "reason": "ok",
            "entry_price_rule_id": LONG_ENTRY_PRICE_RULE_ID,
            "entry_price_rule_version": LONG_ENTRY_PRICE_RULE_VERSION,
            "upward_pct": upward_pct,
            "downward_pct": downward_pct,
            "max_upward_deviation_pct": max_upward_deviation_pct,
            "max_downward_deviation_pct": max_downward_deviation_pct,
            "effective_deviation_pct": max(upward_pct, downward_pct),
        }

    def _enforce_subscription_limits(
        self,
        *,
        tg_user: dict,
        symbol: str,
        tf: str,
        user_open_trades: list,
    ) -> dict[str, object]:
        allowed_symbols = [str(x).upper() for x in (tg_user.get("allowed_symbols") or [])]
        enabled_symbols = [str(x).upper() for x in (tg_user.get("enabled_symbols") or [])]
        enabled_timeframes = {str(x) for x in (tg_user.get("enabled_timeframes") or [])}

        if enabled_timeframes and tf not in enabled_timeframes:
            return {"allowed": False, "reason": "timeframe_not_allowed"}

        allow_long = bool(tg_user.get("allow_long", True))
        if not allow_long:
            return {"allowed": False, "reason": "long_disabled"}

        decision = self.trade_manager.evaluate_new_trade(
            user_id=str(tg_user.get("trading_user_id") or f"tg:{tg_user.get('telegram_id')}"),
            symbol=symbol,
            open_trades=user_open_trades,
            plan_code=str(tg_user.get("plan_code") or ""),
            plan_features=tg_user.get("features_json"),
            allowed_symbols=allowed_symbols,
            enabled_symbols=enabled_symbols,
            max_open_trades_total=tg_user.get("max_open_trades_total"),
            max_open_trades_per_symbol=tg_user.get("max_open_trades_per_symbol"),
            max_risk_trades=tg_user.get("max_risk_trades"),
        )

        return {
            "allowed": decision.allowed,
            "reason": decision.reason,
            "current_open_trades": decision.current_open_trades,
            "current_risk_trades": decision.current_risk_trades,
            "max_open_trades_total": decision.max_open_trades_total,
            "max_risk_trades": decision.max_risk_trades,
        }

    def _entry_block_event_type(self, reason: str) -> str:
        if reason == "risk_limit_reached":
            return "ENTRY_BLOCKED_RISK"
        return "ENTRY_BLOCKED_SUBSCRIPTION"

    def _build_user_sizing_sandbox(
        self,
        *,
        tg_user: dict,
        entry_price: float,
        history: list,
        position_size_multiplier: float = 1.0,
    ) -> dict[str, float]:
        default_stake_mode = str(tg_user.get("default_stake_mode", "percent"))
        default_stake_value = float(tg_user.get("default_stake_value", 1.0) or 1.0)
        default_leverage = int(tg_user.get("default_leverage", 5) or 5)

        balance = self._estimate_balance(
            history,
            start_balance=self._sandbox_start_balance(tg_user),
        )

        if default_stake_mode == "percent":
            stake_usd = balance * (default_stake_value / 100.0)
        else:
            stake_usd = default_stake_value

        if stake_usd <= 0:
            stake_usd = 1.0
        base_stake_usd = float(stake_usd)
        try:
            multiplier = float(position_size_multiplier or 1.0)
        except (TypeError, ValueError):
            multiplier = 1.0
        if multiplier <= 0:
            multiplier = 1.0
        stake_usd = base_stake_usd * multiplier

        qty = (stake_usd * default_leverage) / entry_price if entry_price > 0 else 0.0

        return {
            "stake_usd": float(stake_usd),
            "base_stake_usd": float(base_stake_usd),
            "position_size_multiplier": float(multiplier),
            "leverage": float(default_leverage),
            "qty": float(qty),
        }

    def _sandbox_start_balance(self, tg_user: dict) -> float:
        try:
            value = float(tg_user.get("sandbox_start_balance_usd") or self.sandbox_start_balance_usd)
        except (TypeError, ValueError):
            value = self.sandbox_start_balance_usd
        return value if value > 0 else self.sandbox_start_balance_usd

    def _estimate_balance(self, history: list, *, start_balance: float | None = None) -> float:
        realized = 0.0
        for trade in history:
            pnl = getattr(trade, "realized_pnl_usd", 0.0)
            realized += float(pnl or 0.0)
        return float(start_balance if start_balance is not None else self.sandbox_start_balance_usd) + realized

    def _build_entry_indicators(
        self,
        *,
        bar,
        symbol: str,
        tf: str,
        rule_id: str,
        strategy_version: str,
        signal_meta: dict[str, Any] | None = None,
        signal_level: str | None = None,
        signal_score: int | None = None,
        position_size_multiplier: float = 1.0,
        exit_profile: dict[str, Any] | None = None,
        signal_entry_price: float,
        live_entry_price: float,
        deviation_pct: float,
        entry_price_check: dict | None = None,
    ) -> dict:
        features = dict(getattr(bar, "features", None) or {})
        entry_price_check = entry_price_check or {}
        signal_meta = dict(signal_meta or {})
        indicator_values = dict(signal_meta.get("indicator_values") or {})
        normalized_features = {**features, **{key: value for key, value in indicator_values.items() if value is not None}}
        return {
            "symbol": symbol,
            "tf_entry": tf,
            "rule_id": rule_id,
            "strategy_version": strategy_version,
            "signal_level": signal_level,
            "signal_score": signal_score,
            "position_size_multiplier": position_size_multiplier,
            "signal_meta": signal_meta,
            "exit_profile": dict(exit_profile or {}),
            "features_ver": self.features_ver,
            "entry_bar_close_time": getattr(bar, "close_time", None),
            "bar": {
                "open_time": getattr(bar, "open_time", None),
                "close_time": getattr(bar, "close_time", None),
                "open": getattr(bar, "o", None),
                "high": getattr(bar, "h", None),
                "low": getattr(bar, "l", None),
                "close": getattr(bar, "c", None),
                "volume": getattr(bar, "v", None),
            },
            "prices": {
                "signal_entry_price": signal_entry_price,
                "live_entry_price": live_entry_price,
                "entry_price_deviation_pct": deviation_pct,
            },
            "entry_price_rule": {
                "id": entry_price_check.get("entry_price_rule_id"),
                "version": entry_price_check.get("entry_price_rule_version"),
                "upward_pct": entry_price_check.get("upward_pct"),
                "downward_pct": entry_price_check.get("downward_pct"),
                "max_upward_deviation_pct": entry_price_check.get("max_upward_deviation_pct"),
                "max_downward_deviation_pct": entry_price_check.get("max_downward_deviation_pct"),
            },
            "features": normalized_features,
        }

    def _build_signal_debug_payload(self, entry_indicators: dict) -> dict[str, Any]:
        features = dict((entry_indicators or {}).get("features") or {})
        bar = dict((entry_indicators or {}).get("bar") or {})
        signal_meta = dict((entry_indicators or {}).get("signal_meta") or {})
        exit_profile = dict((entry_indicators or {}).get("exit_profile") or {})
        close = self._to_optional_float(bar.get("close"))
        high = self._to_optional_float(bar.get("high"))
        low = self._to_optional_float(bar.get("low"))
        ema50 = self._to_optional_float(self._first_not_none(features.get("ema50"), features.get("ema_50")))

        dist_to_ema_50_pct = None
        if close is not None and ema50 not in {None, 0.0}:
            dist_to_ema_50_pct = (close - float(ema50)) / float(ema50)
        if dist_to_ema_50_pct is None:
            dist_to_ema_50_pct = self._to_optional_float(
                self._first_not_none(features.get("dist_to_ema_50_pct"), features.get("ema50_dist_pct"))
            )

        close_position = self._to_optional_float(features.get("close_position_in_candle"))
        if close_position is None and close is not None and high is not None and low is not None and high != low:
            close_position = (close - low) / (high - low)

        return {
            "rule_id": (entry_indicators or {}).get("rule_id"),
            "strategy_version": (entry_indicators or {}).get("strategy_version"),
            "signal_level": (entry_indicators or {}).get("signal_level") or signal_meta.get("signal_level"),
            "signal_score": self._first_not_none((entry_indicators or {}).get("signal_score"), signal_meta.get("signal_score")),
            "position_size_multiplier": self._first_not_none(
                (entry_indicators or {}).get("position_size_multiplier"),
                signal_meta.get("position_size_multiplier"),
            ),
            "soft_stop_activation_pct": self._first_not_none(
                signal_meta.get("soft_stop_activation_pct"),
                exit_profile.get("soft_stop_activation_pct"),
            ),
            "soft_stop_activation_trigger": self._first_not_none(
                signal_meta.get("soft_stop_activation_trigger"),
                exit_profile.get("soft_stop_activation_trigger"),
            ),
            "tp_step_pct": self._first_not_none(
                signal_meta.get("tp_step_pct"),
                exit_profile.get("tp_step_pct"),
            ),
            "soft_stop_start_pct": self._first_not_none(
                signal_meta.get("soft_stop_start_pct"),
                exit_profile.get("soft_stop_start_pct"),
            ),
            "soft_stop_increment_pct": self._first_not_none(
                signal_meta.get("soft_stop_increment_pct"),
                exit_profile.get("soft_stop_increment_pct"),
                exit_profile.get("soft_stop_hourly_increment_pct"),
            ),
            "soft_stop_increment_interval_seconds": self._first_not_none(
                signal_meta.get("soft_stop_increment_interval_seconds"),
                exit_profile.get("soft_stop_increment_interval_seconds"),
            ),
            "rsi_14": self._first_not_none(features.get("rsi_14"), features.get("rsi14")),
            "atr_14_pct": self._first_not_none(features.get("atr_14_pct"), features.get("atr14_pct")),
            "dist_to_ema_50_pct": dist_to_ema_50_pct,
            "ema_50": ema50,
            "close_price": close,
            "volume_ratio_sma_20": self._first_not_none(
                features.get("volume_ratio_sma_20"),
                features.get("relative_volume20"),
            ),
            "adx_14": self._first_not_none(features.get("adx_14"), features.get("adx14")),
            "close_position_in_candle": close_position,
            "vol_peak_offset_10": features.get("vol_peak_offset_10"),
            "trigger_explanation": self._signal_trigger_explanation(
                rule_id=str((entry_indicators or {}).get("rule_id") or ""),
                features=features,
            ),
        }

    def _signal_trigger_explanation(self, *, rule_id: str, features: dict[str, Any]) -> list[str]:
        rule = str(rule_id or "").upper()
        if rule == LONG_BREAKOUT_V18_RULE_ID:
            return [
                "RSI crossed threshold",
                "Volume spike",
                "Trend confirmed",
            ]
        if rule.startswith("RSI_REBOUND"):
            return [
                "RSI rebound detected",
                "Supertrend bullish",
                "Entry price accepted",
            ]
        return ["Signal conditions passed"]
