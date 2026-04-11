from __future__ import annotations

from typing import List

from meowbot.core.configs.indicator_sets import get_indicator_set
from meowbot.core.domain.types import Bar
from meowbot.core.ports.bars_repo import BarsRepository
from meowbot.core.ports.exchange_market_data import ExchangeMarketData
from meowbot.core.services.market_data.online_indicators_builder import OnlineIndicatorsBuilder


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

        self.indicator_set = get_indicator_set(features_ver)
        self.builder = OnlineIndicatorsBuilder(self.indicator_set)

    def run(self, symbol: str, tf: str) -> None:
        raw_tail_needed = max(self.required_tail, self.indicator_set.required_history_bars())

        existing_tail = self.bars_repo.get_tail(
            symbol=symbol,
            tf=tf,
            n=raw_tail_needed,
            features_ver=self.features_ver,
            require_features_ok=False,
        )

        last_db = self.bars_repo.get_last_close_time(symbol, tf, self.features_ver)
        last_ex = self.exchange.get_last_closed_time(symbol, tf)

        if last_ex is None:
            return

        if existing_tail:
            start_ms = existing_tail[-1].close_time
        else:
            start_ms = max(0, last_ex - self._approx_tf_window_ms(tf, raw_tail_needed + 10))

        raw_new = self.exchange.fetch_klines(symbol, tf, start_ms, last_ex)

        combined = self._merge_bars(existing_tail, raw_new)
        if not combined:
            return

        tail_for_build = combined[-raw_tail_needed:]
        built = self.builder.build(tail_for_build, features_ver=self.features_ver)
        self.bars_repo.upsert_many(built)

    def _merge_bars(self, left: list[Bar], right: list[Bar]) -> list[Bar]:
        by_close_time: dict[int, Bar] = {}

        for bar in left:
            by_close_time[bar.close_time] = bar

        for bar in right:
            by_close_time[bar.close_time] = bar

        merged = list(by_close_time.values())
        merged.sort(key=lambda item: item.close_time)
        return merged

    def _approx_tf_window_ms(self, tf: str, bars_count: int) -> int:
        tf_ms_map = {
            "1m": 60_000,
            "15m": 15 * 60_000,
            "30m": 30 * 60_000,
            "1h": 60 * 60_000,
            "2h": 2 * 60 * 60_000,
            "4h": 4 * 60 * 60_000,
            "1d": 24 * 60 * 60_000,
        }
        step_ms = tf_ms_map.get(tf, 60_000)
        return step_ms * bars_count