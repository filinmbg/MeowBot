from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from meowbot.core.services.scheduling.bar_close_scheduler import (
    BarCloseScheduler,
    ScheduleState,
)


log = logging.getLogger("meowbot")


@dataclass(frozen=True)
class MarketEntryProcessResult:
    symbol: str
    tf: str
    bar_ready: bool
    last_closed_time_ms: int | None
    reason: str


LastClosedProvider = Callable[[str, str], Awaitable[int | None]]
MarketEntryHandler = Callable[[str, str, int], Awaitable[MarketEntryProcessResult]]
ExitHandler = Callable[[int], Awaitable[None]]
SymbolsProvider = Callable[[], list[str]]


class AsyncBotOnlineRuntime:
    """
    Runtime:
    - окремий exit loop
    - окремий scheduler для symbol+tf
    - bounded concurrency через semaphore
    - підтримка динамічного додавання нових symbols під час роботи
    """

    def __init__(
        self,
        *,
        symbols: list[str],
        tfs: list[str],
        scheduler: BarCloseScheduler,
        last_closed_provider: LastClosedProvider,
        market_entry_handler: MarketEntryHandler,
        exit_handler: ExitHandler,
        scheduler_poll_ms: int = 1_000,
        exit_poll_ms: int = 5_000,
        market_entry_max_concurrency: int = 8,
        exit_max_concurrency: int = 1,
        symbols_provider: SymbolsProvider | None = None,
    ) -> None:
        self.symbols = symbols
        self.tfs = tfs
        self.scheduler = scheduler
        self.last_closed_provider = last_closed_provider
        self.market_entry_handler = market_entry_handler
        self.exit_handler = exit_handler
        self.symbols_provider = symbols_provider

        self.scheduler_poll_ms = scheduler_poll_ms
        self.exit_poll_ms = exit_poll_ms

        self.market_entry_sem = asyncio.Semaphore(market_entry_max_concurrency)
        self.exit_sem = asyncio.Semaphore(exit_max_concurrency)

        self._states: dict[tuple[str, str], ScheduleState] = {}
        self._running_market_tasks: set[tuple[str, str]] = set()
        self._bootstrap_lock = asyncio.Lock()

    async def bootstrap(self) -> None:
        log.info("[runtime] bootstrap start")

        for symbol in self.symbols:
            for tf in self.tfs:
                await self._bootstrap_symbol_tf(symbol, tf)

        log.info("[runtime] bootstrap done states=%s", len(self._states))

    async def _bootstrap_symbol_tf(self, symbol: str, tf: str) -> None:
        key = (symbol, tf)
        if key in self._states:
            return

        try:
            last_closed_time_ms = await self.last_closed_provider(symbol, tf)
            if last_closed_time_ms is None:
                log.warning("[runtime] bootstrap skip %s %s: no last closed bar", symbol, tf)
                return

            state = self.scheduler.bootstrap(
                symbol=symbol,
                tf=tf,
                last_closed_time_ms=last_closed_time_ms,
            )
            self._states[key] = state

            log.info(
                "[runtime] bootstrap %s %s last_closed=%s expected_next_close=%s next_check=%s",
                symbol,
                tf,
                last_closed_time_ms,
                state.expected_close_time_ms,
                state.next_check_time_ms,
            )
        except Exception:
            log.exception("[runtime] bootstrap failed for %s %s", symbol, tf)

    async def _sync_dynamic_symbols(self) -> None:
        if self.symbols_provider is None:
            return

        async with self._bootstrap_lock:
            active_symbols = self.symbols_provider()
            new_symbols = [s for s in active_symbols if s not in self.symbols]

            if not new_symbols:
                return

            self.symbols.extend(new_symbols)
            log.info("[runtime] new symbols detected: %s", ", ".join(new_symbols))

            for symbol in new_symbols:
                for tf in self.tfs:
                    await self._bootstrap_symbol_tf(symbol, tf)

            log.info("[runtime] states after symbol expansion=%s", len(self._states))

    async def run(self) -> None:
        await self.bootstrap()

        await asyncio.gather(
            self._scheduler_loop(),
            self._exit_loop(),
        )

    async def _scheduler_loop(self) -> None:
        log.info("[runtime] scheduler loop started")

        while True:
            now_ms = int(time.time() * 1000)

            await self._sync_dynamic_symbols()

            for key, state in list(self._states.items()):
                if not self.scheduler.should_run(state, now_ms):
                    continue

                if key in self._running_market_tasks:
                    continue

                self._running_market_tasks.add(key)
                asyncio.create_task(self._run_market_entry_task(state, now_ms))

            await asyncio.sleep(self.scheduler_poll_ms / 1000.0)

    async def _run_market_entry_task(self, state: ScheduleState, now_ms: int) -> None:
        key = (state.symbol, state.tf)

        try:
            async with self.market_entry_sem:
                result = await self.market_entry_handler(
                    state.symbol,
                    state.tf,
                    now_ms,
                )

            if result.bar_ready and result.last_closed_time_ms is not None:
                self._states[key] = self.scheduler.on_bar_ready(
                    state,
                    new_last_closed_time_ms=result.last_closed_time_ms,
                )
                log.info(
                    "[runtime] %s %s ready last_closed=%s reason=%s next_check=%s",
                    state.symbol,
                    state.tf,
                    result.last_closed_time_ms,
                    result.reason,
                    self._states[key].next_check_time_ms,
                )
            else:
                self._states[key] = self.scheduler.on_not_ready(state)
                log.debug(
                    "[runtime] %s %s not ready reason=%s retry_count=%s next_check=%s",
                    state.symbol,
                    state.tf,
                    result.reason,
                    self._states[key].retry_count,
                    self._states[key].next_check_time_ms,
                )
        except Exception:
            log.exception("[runtime] market/entry task failed for %s %s", state.symbol, state.tf)
            self._states[key] = self.scheduler.on_not_ready(state)
        finally:
            self._running_market_tasks.discard(key)

    async def _exit_loop(self) -> None:
        log.info("[runtime] exit loop started")

        while True:
            now_ms = int(time.time() * 1000)

            try:
                async with self.exit_sem:
                    await self.exit_handler(now_ms)
            except Exception:
                log.exception("[runtime] exit loop failed")

            await asyncio.sleep(self.exit_poll_ms / 1000.0)