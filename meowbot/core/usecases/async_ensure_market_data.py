from __future__ import annotations

from functools import partial

import anyio

from meowbot.core.configs.indicator_sets import get_indicator_set
from meowbot.core.domain.types import Bar
from meowbot.core.services.market_data.online_indicators_builder import OnlineIndicatorsBuilder


class AsyncEnsureMarketDataUseCase:
    """
    Старт:
    - якщо даних нема -> fetch 500 bars, build indicators, save all

    Runtime:
    - беремо 300 bars із Mongo
    - тягнемо нові bars із Binance
    - rebuild indicators на вікні
    - upsert назад
    """

    def __init__(
        self,
        *,
        bars_repo,
        exchange,
        features_ver: str,
        startup_history_bars: int = 500,
        runtime_window_bars: int = 300,
    ) -> None:
        self.bars_repo = bars_repo
        self.exchange = exchange
        self.features_ver = features_ver
        self.startup_history_bars = startup_history_bars
        self.runtime_window_bars = runtime_window_bars

        self.indicator_set = get_indicator_set(features_ver)
        self.builder = OnlineIndicatorsBuilder(self.indicator_set)

    async def warmup(self, symbol: str, tf: str) -> int | None:
        existing_last = await self.bars_repo.get_last_close_time(
            symbol=symbol,
            tf=tf,
            features_ver=self.features_ver,
        )

        if existing_last is not None:
            return existing_last

        raw = await self.exchange.fetch_klines(
            symbol=symbol,
            tf=tf,
            start_ms=None,
            end_ms=None,
            limit=self.startup_history_bars,
        )
        if not raw:
            return None

        built = await anyio.to_thread.run_sync(
            partial(
                self.builder.build,
                raw,
                features_ver=self.features_ver,
            )
        )

        await self.bars_repo.upsert_many(built)
        return built[-1].close_time if built else None

    async def sync_if_new_bar(self, symbol: str, tf: str) -> tuple[bool, int | None, str]:
        last_exchange_close = await self.exchange.get_last_closed_time(symbol, tf)
        if last_exchange_close is None:
            return False, None, "no_exchange_last_close"

        tail = await self.bars_repo.get_tail(
            symbol=symbol,
            tf=tf,
            n=self.runtime_window_bars,
            features_ver=self.features_ver,
            require_features_ok=False,
        )

        if not tail:
            warmed = await self.warmup(symbol, tf)
            if warmed is None:
                return False, None, "warmup_failed"
            return True, warmed, "warmup_created"

        last_stored_close = tail[-1].close_time
        if last_exchange_close <= last_stored_close:
            return False, last_stored_close, "no_new_bar"

        raw_new = await self.exchange.fetch_klines(
            symbol=symbol,
            tf=tf,
            start_ms=last_stored_close,
            end_ms=last_exchange_close,
            limit=20,
        )
        if not raw_new:
            return False, last_stored_close, "no_new_klines"

        merged = self._merge_bars(tail, raw_new)
        merged = merged[-self.runtime_window_bars:]

        built = await anyio.to_thread.run_sync(
            partial(
                self.builder.build,
                merged,
                features_ver=self.features_ver,
            )
        )

        await self.bars_repo.upsert_many(built)

        return True, built[-1].close_time, "new_bar_synced"

    def _merge_bars(self, old_bars: list[Bar], new_bars: list[Bar]) -> list[Bar]:
        by_close: dict[int, Bar] = {}
        for bar in old_bars:
            by_close[bar.close_time] = bar
        for bar in new_bars:
            by_close[bar.close_time] = bar

        merged = list(by_close.values())
        merged.sort(key=lambda item: item.close_time)
        return merged