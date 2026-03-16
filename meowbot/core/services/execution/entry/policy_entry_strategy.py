from __future__ import annotations

from meowbot.core.domain.types import Bar, Signal
from meowbot.core.ports.entry_strategy import EntryStrategy
from meowbot.core.ports.policy import Policy


class PolicyEntryStrategy(EntryStrategy):
    def __init__(self, policy: Policy):
        self.policy = policy

    def decide(self, bar: Bar) -> Signal:
        return self.policy.predict(bar)