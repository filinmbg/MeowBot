from __future__ import annotations

from typing import Protocol, Optional
from meowbot.core.domain.types import Bar, Signal


class EntryStrategy(Protocol):
    """
    Вхідна стратегія: бере останній бар (або window потім) і видає Signal.
    Для тесту працюємо по 1 бару.
    """
    def decide(self, bar: Bar) -> Signal:
        ...
