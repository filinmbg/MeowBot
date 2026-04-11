from __future__ import annotations

from meowbot.core.configs.indicator_sets import get_indicator_set
from meowbot.core.domain.types import Bar
from meowbot.core.services.market_data.online_indicators_builder import OnlineIndicatorsBuilder


def _make_bar(index: int, close_price: float) -> Bar:
    open_price = close_price - 1.0
    high_price = close_price + 2.0
    low_price = close_price - 2.0

    return Bar(
        symbol="BTCUSDT",
        tf="1h",
        open_time=index * 3_600_000,
        close_time=(index + 1) * 3_600_000 - 1,
        o=open_price,
        h=high_price,
        l=low_price,
        c=close_price,
        v=1000.0 + index,
        features=None,
        features_ok=False,
        features_ver="raw",
    )


def test_build_indicators_marks_last_bar_ready() -> None:
    indicator_set = get_indicator_set("v2_core")
    builder = OnlineIndicatorsBuilder(indicator_set)

    bars = [_make_bar(i, 100.0 + i * 0.5) for i in range(260)]
    built = builder.build(bars, features_ver="v2_core")

    assert len(built) == len(bars)
    assert built[-1].features_ok is True
    assert built[-1].features is not None

    expected_keys = {
        "ema20",
        "ema50",
        "ema100",
        "ema200",
        "rsi14",
        "rsi30",
        "rsi100",
        "atr14",
        "atr21",
        "atr14_pct",
        "macd_line",
        "macd_signal",
        "macd_hist",
        "relative_volume20",
        "relative_volume50",
        "relative_volume100",
        "supertrend_bullish_10_3_0",
        "supertrend_bullish_20_4_0",
    }

    for key in expected_keys:
        assert key in built[-1].features


def test_early_bar_is_not_feature_ready() -> None:
    indicator_set = get_indicator_set("v2_core")
    builder = OnlineIndicatorsBuilder(indicator_set)

    bars = [_make_bar(i, 100.0 + i) for i in range(20)]
    built = builder.build(bars, features_ver="v2_core")

    assert built[0].features_ok is False
    assert built[-1].features_ok is False