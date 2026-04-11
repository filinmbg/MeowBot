from meowbot.core.services.paper_trade_runtime_service import (
    PaperTradeRuntimeService,
)
from meowbot.core.services.signal_detector_service import SignalCandidate


def test_open_and_close_trade():
    runtime = PaperTradeRuntimeService()

    signal = SignalCandidate(
        user_id="1",
        symbol="BTCUSDT",
        timeframe="1h",
        rule_id="rule_1",
        rule_name="test",
        strategy_type="rsi",
        side="LONG",
        entry_time=1,
        entry_price=100,
        signal_bar_close_time=1,
        signal_bar_rsi14=55,
        rolling_min_prev_rsi14=40,
    )

    trades = runtime.process_signals(
        user_id="1",
        signals=[signal],
        is_test_user=True,
    )

    assert len(trades) == 1

    runtime.update_price("BTCUSDT", 102)

    trade = trades[0]
    assert trade.status == "closed"