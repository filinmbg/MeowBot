from __future__ import annotations

from meowbot.core.domain.types import Bar, Signal
from meowbot.core.domain.enums import SignalAction
from meowbot.core.ports.policy import Policy


class FakeThresholdPolicy(Policy):
    """
    Фейкова "ML" політика:
    якщо close > threshold -> LONG, інакше HOLD.
    """
    def __init__(self, threshold: float):
        self.threshold = float(threshold)

    def predict(self, bar: Bar) -> Signal:
        if bar.c > self.threshold:
            return Signal(action=SignalAction.LONG, score=1.0, meta={"src": "fake"})
        return Signal(action=SignalAction.HOLD, score=0.0, meta={"src": "fake"})