from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from collections.abc import Iterable

from meowbot.core.domain.types import Bar


log = logging.getLogger("meowbot")


class BarsCache:
    """Process-local market bars cache for the online trading runtime."""

    def __init__(self, *, maxlen: int = 500) -> None:
        self.maxlen = int(maxlen)
        self.bars_cache: dict[str, dict[str, deque[Bar]]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=self.maxlen))
        )

    def get_last_close_time(self, *, symbol: str, tf: str, features_ver: str) -> int | None:
        rows = self._matching_rows(symbol=symbol, tf=tf, features_ver=features_ver, require_features_ok=False)
        if not rows:
            return None
        return int(rows[-1].close_time)

    def get_tail(
        self,
        *,
        symbol: str,
        tf: str,
        n: int,
        features_ver: str,
        require_features_ok: bool = True,
    ) -> list[Bar]:
        rows = self._matching_rows(
            symbol=symbol,
            tf=tf,
            features_ver=features_ver,
            require_features_ok=require_features_ok,
        )
        return rows[-max(1, int(n)) :]

    def upsert_many(self, bars: Iterable[Bar]) -> None:
        grouped: dict[tuple[str, str], list[Bar]] = defaultdict(list)
        for bar in bars:
            grouped[(str(bar.symbol).upper(), str(bar.tf))].append(bar)

        for (symbol, tf), incoming in grouped.items():
            current = list(self.bars_cache[symbol][tf])
            by_close = {int(bar.close_time): bar for bar in current}
            for bar in incoming:
                by_close[int(bar.close_time)] = bar
            merged = sorted(by_close.values(), key=lambda item: int(item.close_time))
            self.bars_cache[symbol][tf] = deque(merged[-self.maxlen :], maxlen=self.maxlen)

    def _matching_rows(
        self,
        *,
        symbol: str,
        tf: str,
        features_ver: str,
        require_features_ok: bool,
    ) -> list[Bar]:
        rows = list(self.bars_cache.get(str(symbol).upper(), {}).get(str(tf), ()))
        rows = [bar for bar in rows if str(bar.features_ver) == str(features_ver)]
        if require_features_ok:
            rows = [bar for bar in rows if bool(bar.features_ok)]
        rows.sort(key=lambda item: int(item.close_time))
        return rows


class CacheBackedBarsRepositoryAsync:
    """Bars repository API backed by memory reads and optional Mongo writes."""

    def __init__(
        self,
        *,
        cache: BarsCache,
        persistence_repo=None,
        persist_writes: bool = True,
        max_pending_persist_tasks: int = 20,
    ) -> None:
        self.cache = cache
        self.persistence_repo = persistence_repo
        self.persist_writes = bool(persist_writes)
        self.max_pending_persist_tasks = int(max_pending_persist_tasks)
        self._pending_persist_tasks: set[asyncio.Task] = set()

    async def get_last_close_time(self, symbol: str, tf: str, features_ver: str) -> int | None:
        started = time.perf_counter()
        result = self.cache.get_last_close_time(symbol=symbol, tf=tf, features_ver=features_ver)
        duration_ms = int((time.perf_counter() - started) * 1000)
        log.debug(
            "[cache] bars get_last_close_time symbol=%s tf=%s features_ver=%s duration_ms=%s hit=%s",
            symbol,
            tf,
            features_ver,
            duration_ms,
            result is not None,
        )
        return result

    async def get_tail(
        self,
        *,
        symbol: str,
        tf: str,
        n: int,
        features_ver: str,
        require_features_ok: bool = True,
    ) -> list[Bar]:
        started = time.perf_counter()
        rows = self.cache.get_tail(
            symbol=symbol,
            tf=tf,
            n=n,
            features_ver=features_ver,
            require_features_ok=require_features_ok,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        log.debug(
            "[cache] bars get_tail symbol=%s tf=%s features_ver=%s n=%s rows=%s duration_ms=%s hit=%s",
            symbol,
            tf,
            features_ver,
            n,
            len(rows),
            duration_ms,
            bool(rows),
        )
        return rows

    async def upsert_many(self, bars: list[Bar]) -> None:
        if not bars:
            return
        self.cache.upsert_many(bars)
        await self._schedule_persist(bars)

    async def _schedule_persist(self, bars: list[Bar]) -> None:
        if not self.persist_writes or self.persistence_repo is None:
            return
        if bool(getattr(self.persistence_repo, "supports_nonblocking_enqueue", False)):
            await self.persistence_repo.upsert_many(list(bars))
            return
        if len(self._pending_persist_tasks) >= self.max_pending_persist_tasks:
            log.warning(
                "[cache] bars skipping Mongo persistence pending_tasks=%s batch_size=%s",
                len(self._pending_persist_tasks),
                len(bars),
            )
            return

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # Production runs on asyncio. Some tests use trio, where asyncio
            # background tasks are unavailable, so persist inline there.
            await self._persist_many(list(bars))
            return

        task = asyncio.create_task(self._persist_many(list(bars)))
        self._pending_persist_tasks.add(task)
        task.add_done_callback(self._pending_persist_tasks.discard)

    async def _persist_many(self, bars: list[Bar]) -> None:
        try:
            await self.persistence_repo.upsert_many(bars)
        except Exception as exc:
            log.warning(
                "[cache] bars Mongo persistence failed batch_size=%s error=%s:%s",
                len(bars),
                type(exc).__name__,
                exc,
            )
