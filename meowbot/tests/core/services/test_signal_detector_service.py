from meowbot.core.services.signal_detector_service import (
    SignalDetectorService,
    ClosedBar,
)


def test_rsi_rebound_signal():
    service = SignalDetectorService()

    bars = []

    # старі бари з низьким RSI
    for i in range(10):
        bars.append(
            ClosedBar(
                open_time=i,
                close_time=i,
                open=100,
                high=101,
                low=99,
                close=100,
                rsi14=40,
                supertrend_bullish=1,
            )
        )

    # сигнальний бар
    bars.append(
        ClosedBar(
            open_time=11,
            close_time=11,
            open=100,
            high=102,
            low=99,
            close=101,
            rsi14=55,
            supertrend_bullish=1,
        )
    )

    signals = service.detect_signals(
        user_id="test",
        symbol="BTCUSDT",
        timeframe="1h",
        bars=bars,
        is_test_user=True,
    )

    assert len(signals) == 1