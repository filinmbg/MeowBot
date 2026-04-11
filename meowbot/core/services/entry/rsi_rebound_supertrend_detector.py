from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from meowbot.core.domain.types import Bar


@dataclass(frozen=True)
class RsiReboundSupertrendConfig:
    rsi_period: int = 14
    lookback: int = 10
    low_level: float = 45.0
    reclaim_level: float = 50.0
    supertrend_key: str = "supertrend_bullish_10_3_0"
    rule_id: str = "RSI_REBOUND_ST_124"


@dataclass(frozen=True)
class DetectorResult:
    signal: bool
    reason: str
    rule_id: str

    current_rsi: float | None = None
    min_prev_rsi: float | None = None
    supertrend_bullish: int | None = None

    current_close_time: int | None = None
    bars_available: int = 0
    lookback_required: int = 0

    meta: dict[str, Any] | None = None


class RsiReboundSupertrendDetector:
    """
    LONG signal logic:

    1. Поточний бар має бути feature-ready
    2. Має бути достатньо попередніх барів: lookback + 1
    3. Поточний supertrend_bullish == 1
    4. Мінімум RSI за попередні lookback барів <= low_level
    5. Поточний RSI >= reclaim_level
    """

    def __init__(self, config: RsiReboundSupertrendConfig):
        self.config = config

    def check_long(self, bars: list[Bar]) -> DetectorResult:
        if not bars:
            return DetectorResult(
                signal=False,
                reason="no_bars",
                rule_id=self.config.rule_id,
                bars_available=0,
                lookback_required=self.config.lookback + 1,
            )

        bars_sorted = sorted(bars, key=lambda item: item.close_time)
        last = bars_sorted[-1]

        if len(bars_sorted) < self.config.lookback + 1:
            return DetectorResult(
                signal=False,
                reason="not_enough_bars",
                rule_id=self.config.rule_id,
                current_close_time=last.close_time,
                bars_available=len(bars_sorted),
                lookback_required=self.config.lookback + 1,
            )

        if not last.features_ok:
            return DetectorResult(
                signal=False,
                reason="last_bar_features_not_ready",
                rule_id=self.config.rule_id,
                current_close_time=last.close_time,
                bars_available=len(bars_sorted),
                lookback_required=self.config.lookback + 1,
            )

        features = last.features or {}
        rsi_key = f"rsi{self.config.rsi_period}"
        st_key = self.config.supertrend_key

        if rsi_key not in features:
            return DetectorResult(
                signal=False,
                reason="missing_current_rsi",
                rule_id=self.config.rule_id,
                current_close_time=last.close_time,
                bars_available=len(bars_sorted),
                lookback_required=self.config.lookback + 1,
                meta={"missing_feature": rsi_key},
            )

        if st_key not in features:
            return DetectorResult(
                signal=False,
                reason="missing_current_supertrend",
                rule_id=self.config.rule_id,
                current_close_time=last.close_time,
                bars_available=len(bars_sorted),
                lookback_required=self.config.lookback + 1,
                meta={"missing_feature": st_key},
            )

        current_rsi = float(features[rsi_key])
        current_supertrend = int(features[st_key])

        previous_bars = bars_sorted[-(self.config.lookback + 1):-1]
        min_prev_rsi = float("inf")

        for bar in previous_bars:
            if not bar.features_ok:
                return DetectorResult(
                    signal=False,
                    reason="previous_bar_features_not_ready",
                    rule_id=self.config.rule_id,
                    current_rsi=current_rsi,
                    current_close_time=last.close_time,
                    bars_available=len(bars_sorted),
                    lookback_required=self.config.lookback + 1,
                    meta={"prev_close_time": bar.close_time},
                )

            prev_features = bar.features or {}
            prev_rsi = prev_features.get(rsi_key)
            if prev_rsi is None:
                return DetectorResult(
                    signal=False,
                    reason="missing_previous_rsi",
                    rule_id=self.config.rule_id,
                    current_rsi=current_rsi,
                    current_close_time=last.close_time,
                    bars_available=len(bars_sorted),
                    lookback_required=self.config.lookback + 1,
                    meta={"prev_close_time": bar.close_time, "missing_feature": rsi_key},
                )

            min_prev_rsi = min(min_prev_rsi, float(prev_rsi))

        if current_supertrend != 1:
            return DetectorResult(
                signal=False,
                reason="supertrend_not_bullish",
                rule_id=self.config.rule_id,
                current_rsi=current_rsi,
                min_prev_rsi=min_prev_rsi,
                supertrend_bullish=current_supertrend,
                current_close_time=last.close_time,
                bars_available=len(bars_sorted),
                lookback_required=self.config.lookback + 1,
            )

        if min_prev_rsi > self.config.low_level:
            return DetectorResult(
                signal=False,
                reason="no_rebound_base",
                rule_id=self.config.rule_id,
                current_rsi=current_rsi,
                min_prev_rsi=min_prev_rsi,
                supertrend_bullish=current_supertrend,
                current_close_time=last.close_time,
                bars_available=len(bars_sorted),
                lookback_required=self.config.lookback + 1,
                meta={
                    "low_level": self.config.low_level,
                    "reclaim_level": self.config.reclaim_level,
                },
            )

        if current_rsi < self.config.reclaim_level:
            return DetectorResult(
                signal=False,
                reason="reclaim_not_reached",
                rule_id=self.config.rule_id,
                current_rsi=current_rsi,
                min_prev_rsi=min_prev_rsi,
                supertrend_bullish=current_supertrend,
                current_close_time=last.close_time,
                bars_available=len(bars_sorted),
                lookback_required=self.config.lookback + 1,
                meta={
                    "low_level": self.config.low_level,
                    "reclaim_level": self.config.reclaim_level,
                },
            )

        return DetectorResult(
            signal=True,
            reason="signal_long",
            rule_id=self.config.rule_id,
            current_rsi=current_rsi,
            min_prev_rsi=min_prev_rsi,
            supertrend_bullish=current_supertrend,
            current_close_time=last.close_time,
            bars_available=len(bars_sorted),
            lookback_required=self.config.lookback + 1,
            meta={
                "low_level": self.config.low_level,
                "reclaim_level": self.config.reclaim_level,
                "lookback": self.config.lookback,
                "rsi_key": rsi_key,
                "supertrend_key": st_key,
            },
        )