from __future__ import annotations

import math

from meowbot.core.domain.types import Bar
from meowbot.core.services.entry.long_breakout_v18_detector import (
    LONG_BREAKOUT_V18_RULE_ID,
    LongBreakoutV18Config,
    LongBreakoutV18Detector,
)


def _make_bar(*, features: dict | None = None, features_ok: bool = True) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        tf="15m",
        open_time=0,
        close_time=59_999,
        o=100.0,
        h=102.0,
        l=99.0,
        c=101.0,
        v=1000.0,
        features=features or {},
        features_ok=features_ok,
        features_ver="v2_core",
    )


def _features(**overrides) -> dict:
    payload = {
        "rsi_14": 82.0,
        "dist_to_ema_50_pct": 0.022,
        "volume_ratio_sma_20": 2.4,
        "atr_14_pct": 0.006,
        "vol_peak_offset_10": -1.0,
        "close_position_in_candle": 0.70,
        "adx_14": 29.0,
    }
    payload.update(overrides)
    return payload


def _check(features: dict):
    detector = LongBreakoutV18Detector(LongBreakoutV18Config())
    return detector.check_long([_make_bar(features=features)])


def test_strong_signal_wins_over_medium_and_weak() -> None:
    result = _check(_features())

    assert result.signal is True
    assert result.rule_id == LONG_BREAKOUT_V18_RULE_ID
    assert result.reason == "signal_long_breakout_strong"
    assert result.meta["signal_level"] == "strong"
    assert result.meta["strong_result"] is True
    assert result.meta["medium_result"] is True
    assert result.meta["weak_result"] is True
    assert result.meta["final_signal"] == "strong"
    assert result.meta["position_size_multiplier"] == 1.5
    assert result.meta["signal_score"] >= 5


def test_medium_signal_wins_over_weak() -> None:
    result = _check(
        _features(
            rsi_14=80.0,
            dist_to_ema_50_pct=0.0175,
            volume_ratio_sma_20=2.05,
            atr_14_pct=0.0074,
            vol_peak_offset_10=-2.0,
            close_position_in_candle=0.79,
            adx_14=26.0,
        )
    )

    assert result.signal is True
    assert result.reason == "signal_long_breakout_medium"
    assert result.meta["signal_level"] == "medium"
    assert result.meta["position_size_multiplier"] == 1.25


def test_weak_signal_requires_base_filters_and_score_at_least_five() -> None:
    result = _check(
        _features(
            rsi_14=80.0,
            dist_to_ema_50_pct=0.038,
            volume_ratio_sma_20=1.6,
            atr_14_pct=0.008,
            vol_peak_offset_10=-2.0,
            close_position_in_candle=0.79,
            adx_14=22.0,
        )
    )

    assert result.signal is True
    assert result.reason == "signal_long_breakout_weak"
    assert result.meta["signal_level"] == "weak"
    assert result.meta["signal_score"] == 5
    assert result.meta["position_size_multiplier"] == 1.0


def test_weak_signal_is_false_when_score_below_five() -> None:
    result = _check(
        _features(
            rsi_14=76.0,
            dist_to_ema_50_pct=0.020,
            volume_ratio_sma_20=1.6,
            atr_14_pct=0.005,
            vol_peak_offset_10=-4.0,
            close_position_in_candle=0.84,
            adx_14=22.0,
        )
    )

    assert result.signal is False
    assert result.reason == "v2_level_conditions_not_met"
    assert result.meta["signal_score"] == 0
    assert result.meta["weak_result"] is False
    assert "signal_score < 5" in result.meta["weak_failed_conditions"]


def test_rsi_72_rejects_all_v2_levels() -> None:
    result = _check(_features(rsi_14=72.0))

    assert result.signal is False
    assert result.reason == "v2_level_conditions_not_met"
    assert result.meta["strong_result"] is False
    assert result.meta["medium_result"] is False
    assert result.meta["weak_result"] is False
    assert "rsi_14 < 75" in result.meta["weak_failed_conditions"]
    assert result.meta["final_signal"] is None


def test_atr_too_low_rejects_all_v2_levels() -> None:
    result = _check(_features(atr_14_pct=0.0001))

    assert result.signal is False
    assert result.reason == "v2_level_conditions_not_met"
    assert result.meta["strong_result"] is False
    assert result.meta["medium_result"] is False
    assert result.meta["weak_result"] is False
    assert "atr_14_pct < 0.004" in result.meta["weak_failed_conditions"]


def test_atr_one_percent_rejects_as_too_high() -> None:
    result = _check(_features(atr_14_pct=0.01))

    assert result.signal is False
    assert result.reason == "v2_level_conditions_not_met"
    assert "atr_14_pct > 0.008" in result.meta["weak_failed_conditions"]


def test_missing_adx_returns_no_signal() -> None:
    features = _features()
    features.pop("adx_14")

    result = _check(features)

    assert result.signal is False
    assert result.reason == "missing_required_indicators"
    assert result.meta["missing_indicators"] == ["adx_14"]
    assert result.meta["event_type"] == "ENTRY_SKIPPED_MISSING_INDICATOR"


def test_adx_below_medium_can_downgrade_to_weak_when_base_filters_pass() -> None:
    result = _check(
        _features(
            rsi_14=80.0,
            dist_to_ema_50_pct=0.0175,
            volume_ratio_sma_20=2.05,
            atr_14_pct=0.0074,
            vol_peak_offset_10=-2.0,
            close_position_in_candle=0.79,
            adx_14=23.0,
        )
    )

    assert result.signal is True
    assert result.reason == "signal_long_breakout_weak"
    assert result.meta["signal_level"] == "weak"


def test_adx_below_weak_threshold_rejects_signal() -> None:
    result = _check(
        _features(
            rsi_14=80.0,
            dist_to_ema_50_pct=0.0175,
            volume_ratio_sma_20=2.05,
            atr_14_pct=0.0074,
            vol_peak_offset_10=-2.0,
            close_position_in_candle=0.79,
            adx_14=21.0,
        )
    )

    assert result.signal is False
    assert result.reason == "v2_level_conditions_not_met"


def test_missing_any_required_field_returns_no_signal() -> None:
    features = _features(volume_ratio_sma_20=None, atr_14_pct=math.nan)

    result = _check(features)

    assert result.signal is False
    assert result.reason == "missing_required_indicators"
    assert set(result.meta["missing_indicators"]) == {"volume_ratio_sma_20", "atr_14_pct"}


def test_not_features_ready_returns_no_signal() -> None:
    detector = LongBreakoutV18Detector(LongBreakoutV18Config())
    result = detector.check_long([_make_bar(features=_features(), features_ok=False)])

    assert result.signal is False
    assert result.reason == "last_bar_features_not_ready"
