from typing import List, Optional
from meowbot.core.domain.types import Bar
from meowbot.core.ports.bars_repo import BarsRepository


class InMemoryBarsRepo(BarsRepository):
    def __init__(self):
        self._bars: list[Bar] = []

    def ping(self) -> bool:
        return True

    def get_last_close_time(self, symbol: str, tf: str, features_ver: str) -> Optional[int]:
        bars = [
            b for b in self._bars
            if b.symbol == symbol and b.tf == tf and b.features_ver == features_ver
        ]
        if not bars:
            return None
        return max(b.close_time for b in bars)

    def get_tail(
        self,
        symbol: str,
        tf: str,
        n: int,
        features_ver: str,
        require_features_ok: bool = True,
    ) -> List[Bar]:
        bars = [
            b for b in self._bars
            if b.symbol == symbol and b.tf == tf and b.features_ver == features_ver
        ]
        if require_features_ok:
            bars = [b for b in bars if b.features_ok]

        bars.sort(key=lambda b: b.close_time)
        return bars[-n:]

    def upsert_many(self, bars: List[Bar]) -> None:
        for bar in bars:
            self._bars = [
                b for b in self._bars
                if not (b.symbol == bar.symbol and b.tf == bar.tf and b.close_time == bar.close_time)
            ]
            self._bars.append(bar)
