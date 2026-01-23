from __future__ import annotations

from typing import Protocol, Optional, List

from ..domain.types import Bar


class ExchangeMarketData(Protocol):
    """Джерело ринкових даних (біржа)."""

    def ping(self) -> bool:
        ...

    def get_last_closed_time(self, symbol: str, tf: str) -> Optional[int]:
        """close_time (ms) останнього ЗАКРИТОГО бару на біржі."""
        ...

    def fetch_klines(self, symbol: str, tf: str, start_ms: int, end_ms: int) -> List[Bar]:
        """Повертає закриті бари в [start_ms, end_ms]."""
        ...
