from __future__ import annotations

from typing import Protocol
from meowbot.core.domain.types import Bar, Signal


class Policy(Protocol):
    """
    Політика (ML або будь-який інший мозок): на вхід бар/вікно, на вихід Signal.
    На цьому кроці — тільки один Bar.
    """
    def predict(self, bar: Bar) -> Signal:
        ...