from dataclasses import replace

from meowbot.core.domain.types import Bar, Signal
from meowbot.core.domain.enums import SignalAction, TradeStatus
from meowbot.core.usecases.run_entry_cycle import RunEntryCycleUseCase
from meowbot.core.services.execution.entry.portfolio_gate import PortfolioGate
from meowbot.infra.memory.bars_repo import InMemoryBarsRepo
from meowbot.infra.memory.trades_repo import InMemoryTradesRepo
from meowbot.infra.memory.broker import InMemoryBroker


class ThresholdLongStrategy:
    def __init__(self, threshold: float):
        self.threshold = threshold

    def decide(self, bar: Bar) -> Signal:
        if bar.c > self.threshold:
            return Signal(action=SignalAction.LONG, score=1.0)
        return Signal(action=SignalAction.HOLD, score=0.0)


def make_bar(i: int, close: float, ok: bool = True) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        tf="15m",
        open_time=i * 60_000,
        close_time=(i + 1) * 60_000,
        o=close, h=close, l=close, c=close, v=1.0,
        features_ok=ok,
        features_ver="v1",
    )


def test_opens_trade_when_signal_long():
    bars = [make_bar(0, 90), make_bar(1, 110)]  # last close=110
    repo_bars = InMemoryBarsRepo()
    repo_bars.upsert_many(bars)

    repo_trades = InMemoryTradesRepo()
    broker = InMemoryBroker()
    strategy = ThresholdLongStrategy(threshold=100)
    gate = PortfolioGate(repo_trades)

    uc = RunEntryCycleUseCase(
        bars_repo=repo_bars,
        trades_repo=repo_trades,
        broker=broker,
        entry_strategy=strategy,
        gate=gate,
        features_ver="v1",
    )

    trade_id = uc.run(symbol="BTCUSDT", tf="15m", now_ms=999_000, sl_price=100.0)
    assert trade_id is not None

    open_trade = repo_trades.get_open_trade_by_symbol("BTCUSDT")
    assert open_trade is not None
    assert open_trade.status == TradeStatus.OPEN
    assert open_trade.entry_price == 110


def test_does_not_open_second_trade_same_symbol():
    bars = [make_bar(0, 110)]
    repo_bars = InMemoryBarsRepo()
    repo_bars.upsert_many(bars)

    repo_trades = InMemoryTradesRepo()
    broker = InMemoryBroker()
    strategy = ThresholdLongStrategy(threshold=100)
    gate = PortfolioGate(repo_trades)

    uc = RunEntryCycleUseCase(
        bars_repo=repo_bars,
        trades_repo=repo_trades,
        broker=broker,
        entry_strategy=strategy,
        gate=gate,
        features_ver="v1",
    )

    tid1 = uc.run(symbol="BTCUSDT", tf="15m", now_ms=1000, sl_price=100.0)
    assert tid1 is not None

    tid2 = uc.run(symbol="BTCUSDT", tf="15m", now_ms=2000, sl_price=100.0)
    assert tid2 is None  # gate блокує

    open_trade = repo_trades.get_open_trade_by_symbol("BTCUSDT")
    assert open_trade is not None
    assert open_trade.trade_id == tid1


def test_no_trade_when_hold():
    bars = [make_bar(0, 90)]
    repo_bars = InMemoryBarsRepo()
    repo_bars.upsert_many(bars)

    repo_trades = InMemoryTradesRepo()
    broker = InMemoryBroker()
    strategy = ThresholdLongStrategy(threshold=100)
    gate = PortfolioGate(repo_trades)

    uc = RunEntryCycleUseCase(
        bars_repo=repo_bars,
        trades_repo=repo_trades,
        broker=broker,
        entry_strategy=strategy,
        gate=gate,
        features_ver="v1",
    )

    tid = uc.run(symbol="BTCUSDT", tf="15m", now_ms=1000, sl_price=100.0)
    assert tid is None
    assert repo_trades.get_open_trade_by_symbol("BTCUSDT") is None

def test_no_duplicate_entry_on_same_bar():
    bars = [make_bar(0, 110)]
    repo_bars = InMemoryBarsRepo()
    repo_bars.upsert_many(bars)

    repo_trades = InMemoryTradesRepo()
    broker = InMemoryBroker()
    strategy = ThresholdLongStrategy(threshold=100)
    gate = PortfolioGate(repo_trades)

    uc = RunEntryCycleUseCase(
        bars_repo=repo_bars,
        trades_repo=repo_trades,
        broker=broker,
        entry_strategy=strategy,
        gate=gate,
        features_ver="v1",
    )

    # 1-й виклик — відкрив
    tid1 = uc.run(symbol="BTCUSDT", tf="15m", now_ms=1000, sl_price=100.0)
    assert tid1 is not None

    # Імітуємо: трейд закритий (щоб gate не блокував по open trade)
    t = repo_trades._trades[tid1]
    t.status = TradeStatus.CLOSED
    repo_trades.update_trade(t)

    # 2-й виклик на тому ж барі — має НЕ відкрити (антидубль)
    tid2 = uc.run(symbol="BTCUSDT", tf="15m", now_ms=2000, sl_price=100.0)
    assert tid2 is None
