from __future__ import annotations

from typing import Protocol, Optional, List

from ..domain.types import Bar


class BarsRepository(Protocol):
    """Сховище барів + фіч (Mongo/інше)."""

    def ping(self) -> bool:
        ...

    def get_last_close_time(self, symbol: str, tf: str, features_ver: str) -> Optional[int]:
        ...

    def get_tail(
        self,
        symbol: str,
        tf: str,
        n: int,
        features_ver: str,
        require_features_ok: bool = True,
    ) -> List[Bar]:
        """Останні n барів (звичайно з features_ok=True)."""
        ...

    def upsert_many(self, bars: List[Bar]) -> None:
        ...
