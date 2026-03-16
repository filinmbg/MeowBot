from meowbot.core.domain.types import Bar
from meowbot.core.services.execution.entry.policy_entry_strategy import PolicyEntryStrategy
from meowbot.infra.memory.policy import FakeThresholdPolicy
from meowbot.core.domain.enums import SignalAction


def make_bar(close: float) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        tf="15m",
        open_time=0,
        close_time=60_000,
        o=close, h=close, l=close, c=close, v=1.0,
        features_ok=True,
        features_ver="v1",
    )


def test_policy_entry_strategy_long():
    policy = FakeThresholdPolicy(threshold=100)
    strategy = PolicyEntryStrategy(policy)

    sig = strategy.decide(make_bar(110))
    assert sig.action == SignalAction.LONG
    assert sig.score > 0


def test_policy_entry_strategy_hold():
    policy = FakeThresholdPolicy(threshold=100)
    strategy = PolicyEntryStrategy(policy)

    sig = strategy.decide(make_bar(90))
    assert sig.action == SignalAction.HOLD