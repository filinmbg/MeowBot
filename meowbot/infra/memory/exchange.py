from typing import List, Optional
from meowbot.core.domain.types import Bar
from meowbot.core.ports.exchange_market_data import ExchangeMarketData


class FakeExchange(ExchangeMarketData):
    def __init__(self, bars: List[Bar]):
        self._bars = bars
        self.fetch_calls = 0

    def ping(self) -> bool:
        return True

    def get_last_closed_time(self, symbol: str, tf: str) -> Optional[int]:
        bars = [b for b in self._bars if b.symbol == symbol and b.tf == tf]
        if not bars:
            return None
        return max(b.close_time for b in bars)

    def fetch_klines(self, symbol: str, tf: str, start_ms: int, end_ms: int) -> List[Bar]:
        self.fetch_calls += 1
        return [
            b for b in self._bars
            if b.symbol == symbol and b.tf == tf
            and start_ms <= b.close_time <= end_ms
        ]
