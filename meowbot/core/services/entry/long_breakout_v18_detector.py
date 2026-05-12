from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from meowbot.core.domain.types import Bar
from meowbot.core.services.entry.rsi_rebound_supertrend_detector import DetectorResult


LONG_BREAKOUT_V18_RULE_ID = "LONG_BREAKOUT_V18"

V2_REQUIRED_INDICATORS = (
    "rsi_14",
    "dist_to_ema_50_pct",
    "volume_ratio_sma_20",
    "atr_14_pct",
    "vol_peak_offset_10",
    "close_position_in_candle",
    "adx_14",
)

V2_LEVEL_MULTIPLIERS = {
    "weak": 1.0,
    "medium": 1.25,
    "strong": 1.5,
}

V2_SOFT_STOP_ACTIVATION_PCT = {
    "weak": 0.6,
    "medium": 0.8,
    "strong": 1.0,
}

# V2 runtime stores *_pct features as decimal ratios: 1% is 0.01.
STRONG_EMA_DISTANCE_MIN = 0.018
STRONG_EMA_DISTANCE_MAX = 0.032
STRONG_ATR_MIN = 0.0045
STRONG_ATR_MAX = 0.0070
MEDIUM_EMA_DISTANCE_MIN = 0.017
MEDIUM_EMA_DISTANCE_MAX = 0.035
MEDIUM_ATR_MIN = 0.0045
MEDIUM_ATR_MAX = 0.0075
WEAK_EMA_DISTANCE_MIN = 0.015
WEAK_EMA_DISTANCE_MAX = 0.040
WEAK_ATR_MIN = 0.004
WEAK_ATR_MAX = 0.008


@dataclass(frozen=True)
class LongBreakoutV18Config:
    rule_id: str = LONG_BREAKOUT_V18_RULE_ID
    breakout_lookback: int = 20


@dataclass(frozen=True)
class SignalLevelCheck:
    passed: bool
    failed_conditions: list[str]
    score: int | None = None


class LongBreakoutV18Detector:
    """
    V2 LONG-only entry detector with level-based signal quality.

    Evaluation is fail-closed: any missing/invalid required indicator returns
    signal=False. Strong is checked before medium, then weak, so high quality
    setups are never downgraded by a looser rule.
    """

    def __init__(self, config: LongBreakoutV18Config):
        self.config = config

    def check_long(self, bars: list[Bar]) -> DetectorResult:
        if not bars:
            return self._result(False, "no_bars", bars_available=0)

        bars_sorted = sorted(bars, key=lambda item: item.close_time)
        last = bars_sorted[-1]

        if not bool(getattr(last, "features_ok", False)):
            return self._result(
                False,
                "last_bar_features_not_ready",
                current_close_time=last.close_time,
                bars_available=len(bars_sorted),
            )

        values, missing = self._extract_required_values(last)
        if missing:
            return self._result(
                False,
                "missing_required_indicators",
                current_close_time=last.close_time,
                bars_available=len(bars_sorted),
                meta={
                    "missing_indicators": missing,
                    "indicator_values": values,
                    "event_type": "ENTRY_SKIPPED_MISSING_INDICATOR",
                },
            )

        strong_check = self._check_strong(values)
        medium_check = self._check_medium(values)
        weak_score = self._weak_score(values)
        weak_check = self._check_weak(values, weak_score)
        check_meta = self._checks_meta(
            strong_check=strong_check,
            medium_check=medium_check,
            weak_check=weak_check,
            final_signal=None,
        )

        if strong_check.passed:
            return self._signal_result(
                "strong",
                score=weak_score,
                values=values,
                last=last,
                bars_available=len(bars_sorted),
                check_meta={**check_meta, "final_signal": "strong"},
            )
        if medium_check.passed:
            return self._signal_result(
                "medium",
                score=weak_score,
                values=values,
                last=last,
                bars_available=len(bars_sorted),
                check_meta={**check_meta, "final_signal": "medium"},
            )
        if weak_check.passed:
            return self._signal_result(
                "weak",
                score=weak_score,
                values=values,
                last=last,
                bars_available=len(bars_sorted),
                check_meta={**check_meta, "final_signal": "weak"},
            )

        return self._result(
            False,
            "v2_level_conditions_not_met",
            current_rsi=values.get("rsi_14"),
            current_close_time=last.close_time,
            bars_available=len(bars_sorted),
            meta={
                "signal_level": None,
                "signal_score": weak_score,
                "position_size_multiplier": 0.0,
                "indicator_values": values,
                **check_meta,
            },
        )

    def _signal_result(
        self,
        level: str,
        *,
        score: int,
        values: dict[str, float],
        last: Bar,
        bars_available: int,
        check_meta: dict[str, Any],
    ) -> DetectorResult:
        return self._result(
            True,
            f"signal_long_breakout_{level}",
            current_rsi=values.get("rsi_14"),
            current_close_time=last.close_time,
            bars_available=bars_available,
            meta={
                "signal_level": level,
                "signal_score": int(score),
                "position_size_multiplier": V2_LEVEL_MULTIPLIERS[level],
                "soft_stop_activation_pct": V2_SOFT_STOP_ACTIVATION_PCT[level],
                "soft_stop_start_pct": 0.1,
                "soft_stop_increment_pct": 0.05,
                "soft_stop_increment_interval_seconds": 900,
                "soft_stop_activation_trigger": "tp1_hit",
                "tp_step_pct": V2_SOFT_STOP_ACTIVATION_PCT[level],
                "indicator_values": values,
                **check_meta,
            },
        )

    def _extract_required_values(self, bar: Bar) -> tuple[dict[str, float], list[str]]:
        features = getattr(bar, "features", None) or {}
        close = self._number(getattr(bar, "c", None))
        high = self._number(getattr(bar, "h", None))
        low = self._number(getattr(bar, "l", None))

        values: dict[str, float] = {
            "rsi_14": self._first_number(features, "rsi_14", "rsi14"),
            "dist_to_ema_50_pct": self._ema_distance_pct(features, close),
            "volume_ratio_sma_20": self._first_number(features, "volume_ratio_sma_20", "relative_volume20"),
            "atr_14_pct": self._atr_percent(features),
            "vol_peak_offset_10": self._first_number(features, "vol_peak_offset_10"),
            "close_position_in_candle": self._close_position(features, close=close, high=high, low=low),
            "adx_14": self._first_number(features, "adx_14", "adx14"),
        }
        missing = [key for key in V2_REQUIRED_INDICATORS if not self._is_valid_number(values.get(key))]
        return values, missing

    def _check_strong(self, values: dict[str, float]) -> SignalLevelCheck:
        failed: list[str] = []
        self._require_range(failed, "rsi_14", values["rsi_14"], minimum=80.0, maximum=88.0)
        self._require_abs_range(
            failed,
            "dist_to_ema_50_pct",
            values["dist_to_ema_50_pct"],
            minimum=STRONG_EMA_DISTANCE_MIN,
            maximum=STRONG_EMA_DISTANCE_MAX,
        )
        self._require_min(failed, "volume_ratio_sma_20", values["volume_ratio_sma_20"], minimum=2.3)
        self._require_range(failed, "atr_14_pct", values["atr_14_pct"], minimum=STRONG_ATR_MIN, maximum=STRONG_ATR_MAX)
        self._require_min(failed, "vol_peak_offset_10", values["vol_peak_offset_10"], minimum=-2.0)
        self._require_range(
            failed,
            "close_position_in_candle",
            values["close_position_in_candle"],
            minimum=0.50,
            maximum=0.78,
        )
        self._require_min(failed, "adx_14", values["adx_14"], minimum=28.0)
        return SignalLevelCheck(passed=not failed, failed_conditions=failed)

    def _check_medium(self, values: dict[str, float]) -> SignalLevelCheck:
        failed: list[str] = []
        self._require_range(failed, "rsi_14", values["rsi_14"], minimum=78.0, maximum=88.0)
        self._require_abs_range(
            failed,
            "dist_to_ema_50_pct",
            values["dist_to_ema_50_pct"],
            minimum=MEDIUM_EMA_DISTANCE_MIN,
            maximum=MEDIUM_EMA_DISTANCE_MAX,
        )
        self._require_min(failed, "volume_ratio_sma_20", values["volume_ratio_sma_20"], minimum=2.0)
        self._require_range(failed, "atr_14_pct", values["atr_14_pct"], minimum=MEDIUM_ATR_MIN, maximum=MEDIUM_ATR_MAX)
        self._require_min(failed, "vol_peak_offset_10", values["vol_peak_offset_10"], minimum=-3.0)
        self._require_range(
            failed,
            "close_position_in_candle",
            values["close_position_in_candle"],
            minimum=0.45,
            maximum=0.80,
        )
        self._require_min(failed, "adx_14", values["adx_14"], minimum=25.0)
        return SignalLevelCheck(passed=not failed, failed_conditions=failed)

    def _check_weak(self, values: dict[str, float], score: int) -> SignalLevelCheck:
        failed: list[str] = []
        self._require_range(failed, "rsi_14", values["rsi_14"], minimum=75.0, maximum=90.0)
        self._require_abs_range(
            failed,
            "dist_to_ema_50_pct",
            values["dist_to_ema_50_pct"],
            minimum=WEAK_EMA_DISTANCE_MIN,
            maximum=WEAK_EMA_DISTANCE_MAX,
        )
        self._require_min(failed, "volume_ratio_sma_20", values["volume_ratio_sma_20"], minimum=1.5)
        self._require_range(failed, "atr_14_pct", values["atr_14_pct"], minimum=WEAK_ATR_MIN, maximum=WEAK_ATR_MAX)
        self._require_min(failed, "vol_peak_offset_10", values["vol_peak_offset_10"], minimum=-4.0)
        self._require_max(failed, "close_position_in_candle", values["close_position_in_candle"], maximum=0.85, inclusive=False)
        self._require_min(failed, "adx_14", values["adx_14"], minimum=22.0)
        if score < 5:
            failed.append("signal_score < 5")
        return SignalLevelCheck(passed=not failed, failed_conditions=failed, score=score)

    def _checks_meta(
        self,
        *,
        strong_check: SignalLevelCheck,
        medium_check: SignalLevelCheck,
        weak_check: SignalLevelCheck,
        final_signal: str | None,
    ) -> dict[str, Any]:
        return {
            "strong_result": bool(strong_check.passed),
            "medium_result": bool(medium_check.passed),
            "weak_result": bool(weak_check.passed),
            "strong_failed_conditions": list(strong_check.failed_conditions),
            "medium_failed_conditions": list(medium_check.failed_conditions),
            "weak_failed_conditions": list(weak_check.failed_conditions),
            "strong_passed": bool(strong_check.passed),
            "medium_passed": bool(medium_check.passed),
            "weak_passed": bool(weak_check.passed),
            "final_signal": final_signal,
        }

    @staticmethod
    def _require_min(failed: list[str], key: str, value: float, *, minimum: float) -> None:
        if value < minimum:
            failed.append(f"{key} < {LongBreakoutV18Detector._fmt_condition_number(minimum)}")

    @staticmethod
    def _require_max(
        failed: list[str],
        key: str,
        value: float,
        *,
        maximum: float,
        inclusive: bool = True,
    ) -> None:
        if value > maximum or (not inclusive and value >= maximum):
            operator = ">" if inclusive else ">="
            failed.append(f"{key} {operator} {LongBreakoutV18Detector._fmt_condition_number(maximum)}")

    @classmethod
    def _require_range(
        cls,
        failed: list[str],
        key: str,
        value: float,
        *,
        minimum: float,
        maximum: float,
    ) -> None:
        cls._require_min(failed, key, value, minimum=minimum)
        cls._require_max(failed, key, value, maximum=maximum)

    @classmethod
    def _require_abs_range(
        cls,
        failed: list[str],
        key: str,
        value: float,
        *,
        minimum: float,
        maximum: float,
    ) -> None:
        abs_value = abs(value)
        if abs_value < minimum:
            failed.append(f"abs({key}) < {cls._fmt_condition_number(minimum)}")
        if abs_value > maximum:
            failed.append(f"abs({key}) > {cls._fmt_condition_number(maximum)}")

    @staticmethod
    def _fmt_condition_number(value: float) -> str:
        numeric = float(value)
        precision = 4 if abs(numeric) < 0.1 else 2
        return f"{numeric:.{precision}f}".rstrip("0").rstrip(".")

    def _weak_score(self, values: dict[str, float]) -> int:
        score = 0
        if values["adx_14"] >= 25.0:
            score += 1
        if values["adx_14"] >= 30.0:
            score += 1
        if values["volume_ratio_sma_20"] >= 2.0:
            score += 1
        if values["volume_ratio_sma_20"] >= 2.5:
            score += 1
        if values["rsi_14"] >= 80.0:
            score += 1
        if 80.0 <= values["rsi_14"] <= 88.0:
            score += 1
        if values["vol_peak_offset_10"] >= -2.0:
            score += 1
        if values["close_position_in_candle"] < 0.80:
            score += 1
        if 0.45 <= values["close_position_in_candle"] <= 0.80:
            score += 1
        return score

    def _ema_distance_pct(self, features: dict[str, Any], close: float | None) -> float | None:
        explicit = self._first_number(features, "dist_to_ema_50_pct", "ema50_dist_pct")
        if explicit is not None:
            return explicit
        ema50 = self._first_number(features, "ema_50", "ema50")
        if close is None or ema50 in {None, 0.0}:
            return None
        return (close - float(ema50)) / float(ema50)

    def _atr_percent(self, features: dict[str, Any]) -> float | None:
        value = self._first_number(features, "atr_14_pct", "atr14_pct")
        if value is None:
            return None
        return value

    def _close_position(
        self,
        features: dict[str, Any],
        *,
        close: float | None,
        high: float | None,
        low: float | None,
    ) -> float | None:
        explicit = self._first_number(features, "close_position_in_candle")
        if explicit is not None:
            return explicit
        if close is None or high is None or low is None or high == low:
            return None
        return (close - low) / (high - low)

    def _first_number(self, features: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = self._number(features.get(key))
            if value is not None:
                return value
        return None

    def _number(self, value: Any) -> float | None:
        try:
            if value is None:
                return None
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if self._is_valid_number(numeric) else None

    def _is_valid_number(self, value: Any) -> bool:
        try:
            return value is not None and math.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    def _result(
        self,
        signal: bool,
        reason: str,
        *,
        current_rsi: float | None = None,
        supertrend_bullish: int | None = None,
        current_close_time: int | None = None,
        bars_available: int = 0,
        meta: dict | None = None,
    ) -> DetectorResult:
        return DetectorResult(
            signal=signal,
            reason=reason,
            rule_id=self.config.rule_id,
            current_rsi=current_rsi,
            supertrend_bullish=supertrend_bullish,
            current_close_time=current_close_time,
            bars_available=bars_available,
            lookback_required=1,
            meta=meta,
        )
