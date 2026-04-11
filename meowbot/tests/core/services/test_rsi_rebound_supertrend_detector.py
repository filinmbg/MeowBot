from __future__ import annotations

from meowbot.core.domain.types import Bar
from meowbot.core.services.entry.rsi_rebound_supertrend_detector import (
    RsiReboundSupertrendConfig,
    RsiReboundSupertrendDetector,
)


def _make_bar(
    *,
    idx: int,
    rsi14: float | None,
    supertrend: int | None,
    features_ok: bool = True,
) -> Bar:
    features = {}
    if rsi14 is not None:
        features["rsi14"] = float(rsi14)
    if supertrend is not None:
        features["supertrend_bullish_10_3_0"] = float(supertrend)

    return Bar(
        symbol="BTCUSDT",
        tf="30m",
        open_time=idx * 1000,
        close_time=idx * 1000 + 999,
        o=100.0,
        h=101.0,
        l=99.0,
        c=100.0 + idx,
        v=1000.0,
        features=features,
        features_ok=features_ok,
        features_ver="v2_core",
    )


def test_detector_returns_signal_long() -> None:
    detector = RsiReboundSupertrendDetector(
        RsiReboundSupertrendConfig(
            rsi_period=14,
            lookback=10,
            low_level=45.0,
            reclaim_level=50.0,
            supertrend_key="supertrend_bullish_10_3_0",
            rule_id="RSI_REBOUND_ST_124",
        )
    )

    bars = [_make_bar(idx=i, rsi14=40.0, supertrend=1) for i in range(10)]
    bars.append(_make_bar(idx=10, rsi14=52.0, supertrend=1))

    result = detector.check_long(bars)

    assert result.signal is True
    assert result.reason == "signal_long"
    assert result.rule_id == "RSI_REBOUND_ST_124"
    assert result.current_rsi == 52.0
    assert result.min_prev_rsi == 40.0
    assert result.supertrend_bullish == 1


def test_detector_returns_no_rebound_base() -> None:
    detector = RsiReboundSupertrendDetector(
        RsiReboundSupertrendConfig(
            lookback=10,
            low_level=45.0,
            reclaim_level=50.0,
        )
    )

    bars = [_make_bar(idx=i, rsi14=47.0, supertrend=1) for i in range(10)]
    bars.append(_make_bar(idx=10, rsi14=55.0, supertrend=1))

    result = detector.check_long(bars)

    assert result.signal is False
    assert result.reason == "no_rebound_base"
    assert result.current_rsi == 55.0
    assert result.min_prev_rsi == 47.0
    assert result.supertrend_bullish == 1


def test_detector_returns_supertrend_not_bullish() -> None:
    detector = RsiReboundSupertrendDetector(
        RsiReboundSupertrendConfig(
            lookback=10,
            low_level=45.0,
            reclaim_level=50.0,
        )
    )

    bars = [_make_bar(idx=i, rsi14=40.0, supertrend=1) for i in range(10)]
    bars.append(_make_bar(idx=10, rsi14=55.0, supertrend=0))

    result = detector.check_long(bars)

    assert result.signal is False
    assert result.reason == "supertrend_not_bullish"
    assert result.current_rsi == 55.0
    assert result.min_prev_rsi == 40.0
    assert result.supertrend_bullish == 0


def test_detector_returns_reclaim_not_reached() -> None:
    detector = RsiReboundSupertrendDetector(
        RsiReboundSupertrendConfig(
            lookback=10,
            low_level=45.0,
            reclaim_level=50.0,
        )
    )

    bars = [_make_bar(idx=i, rsi14=40.0, supertrend=1) for i in range(10)]
    bars.append(_make_bar(idx=10, rsi14=48.0, supertrend=1))

    result = detector.check_long(bars)

    assert result.signal is False
    assert result.reason == "reclaim_not_reached"
    assert result.current_rsi == 48.0
    assert result.min_prev_rsi == 40.0
    assert result.supertrend_bullish == 1