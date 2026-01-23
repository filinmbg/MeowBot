from typing import List
from meowbot.core.domain.types import Bar
from meowbot.core.ports.bars_repo import BarsRepository
from meowbot.core.ports.exchange_market_data import ExchangeMarketData
from dataclasses import replace



class EnsureMarketDataUseCase:
    def __init__(
        self,
        bars_repo: BarsRepository,
        exchange: ExchangeMarketData,
        features_ver: str,
        required_tail: int,
    ):
        self.bars_repo = bars_repo
        self.exchange = exchange
        self.features_ver = features_ver
        self.required_tail = required_tail

    def run(self, symbol: str, tf: str) -> None:
        tail = self.bars_repo.get_tail(
            symbol=symbol,
            tf=tf,
            n=self.required_tail,
            features_ver=self.features_ver,
            require_features_ok=True,
        )

        if len(tail) >= self.required_tail:
            return

        last_db = self.bars_repo.get_last_close_time(symbol, tf, self.features_ver)
        last_ex = self.exchange.get_last_closed_time(symbol, tf)

        if last_ex is None:
            return

        start = last_db or 0
        raw = self.exchange.fetch_klines(symbol, tf, start, last_ex)

        # заглушка індикаторів
        ready: List[Bar] = []
        for b in raw:
            ready.append(
                replace(
                    b,
                    features_ok=True,
                    features_ver=self.features_ver,
                )
            )

        self.bars_repo.upsert_many(ready)
