from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from meowbot.core.services.scheduling.bar_close_scheduler import (
    BarCloseScheduler,
    ScheduleState,
)
from meowbot.core.services.runtime.symbol_validator import (
    filter_valid_binance_usdt_symbols,
    validate_binance_usdt_perp_symbol,
)


log = logging.getLogger("meowbot")


@dataclass(frozen=True)
class MarketEntryProcessResult:
    symbol: str
    tf: str
    bar_ready: bool
    last_closed_time_ms: int | None
    reason: str
    duration_ms: int = 0
    indicator_calculation_ms: int = 0
    market_sync_ms: int = 0
    last_closed_ms: int = 0
    get_tail_ms: int = 0
    fetch_klines_ms: int = 0
    merge_bars_ms: int = 0
    indicator_build_wait_ms: int = 0
    upsert_bars_ms: int = 0
    fetch_bars_ms: int = 0
    feature_rows: int = 0
    cache_mode: str = "-"
    v1_signal_check_ms: int = 0
    v2_signal_check_ms: int = 0
    user_routing_ms: int = 0
    entry_checks_ms: int = 0
    order_trade_creation_ms: int = 0
    exit_replay_ms: int = 0
    user_checks: int = 0
    created: int = 0
    blocked: int = 0
    signals_computed: int = 0
    active_signals: int = 0
    v1_checked: int = 0
    v2_checked: int = 0
    v1_skipped: int = 0
    v2_skipped: int = 0
    signal_cache_hits: int = 0
    active_signal_breakdown: dict[str, dict] = field(default_factory=dict)
    created_breakdown: dict[str, dict] = field(default_factory=dict)
    blocked_breakdown: dict[str, dict] = field(default_factory=dict)
    blocked_reasons: dict[str, int] = field(default_factory=dict)
    failed: bool = False


@dataclass(frozen=True)
class SignalWorkerPoolSummary:
    tf: str
    submitted: int
    started: int
    completed: int
    failed: int
    cancelled: int
    duration_ms: int


@dataclass(frozen=True)
class TimeframeScanStatus:
    tf: str
    active_symbols: int
    checked: int = 0
    skipped: int = 0
    failed: int = 0
    duration_ms: int = 0
    reason: str = "waiting_scheduler"
    scheduler_state: str = "-"
    last_success_at_ms: int | None = None


@dataclass(frozen=True)
class ScanBatchReportContext:
    batch_id: str
    due_timeframes: tuple[str, ...]
    completed_timeframes: tuple[str, ...]
    pending_timeframes: tuple[str, ...] = ()
    duration_ms: int = 0


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
        signal_max_concurrency_15m: int = 60,
        signal_max_concurrency_other: int = 20,
        signal_max_concurrency_hard_limit: int = 100,
        admin_alert_service=None,
        admin_signal_scan_report_min_interval_seconds: float = 300.0,
    ) -> None:
        self.symbols, invalid_symbols = filter_valid_binance_usdt_symbols(symbols)
        if invalid_symbols:
            log.warning(
                "[runtime] INVALID_SYMBOL_SKIPPED scope=init count=%s examples=%s",
                len(invalid_symbols),
                [f"{item.normalized_symbol or item.symbol}:{item.reason}" for item in invalid_symbols[:10]],
            )
        self.tfs = tfs
        self.scheduler = scheduler
        self.last_closed_provider = last_closed_provider
        self.market_entry_handler = market_entry_handler
        self._market_entry_accepts_force_signal_scan = self._callable_accepts_kwarg(
            market_entry_handler,
            "force_signal_scan",
        )
        self.exit_handler = exit_handler
        self.symbols_provider = symbols_provider

        self.scheduler_poll_ms = scheduler_poll_ms
        self.exit_poll_ms = exit_poll_ms

        self.signal_max_concurrency_15m = max(1, int(signal_max_concurrency_15m))
        self.signal_max_concurrency_other = max(1, int(signal_max_concurrency_other))
        self.signal_max_concurrency_hard_limit = max(1, int(signal_max_concurrency_hard_limit))
        self.market_entry_sem = asyncio.Semaphore(max(1, int(market_entry_max_concurrency)))
        self.market_entry_15m_sem = asyncio.Semaphore(self.signal_max_concurrency_15m)
        self.market_entry_other_sem = asyncio.Semaphore(self.signal_max_concurrency_other)
        self.market_entry_hard_sem = asyncio.Semaphore(self.signal_max_concurrency_hard_limit)
        self.exit_sem = asyncio.Semaphore(exit_max_concurrency)
        self.admin_alert_service = admin_alert_service
        self.admin_signal_scan_report_min_interval_seconds = max(
            0.0,
            float(admin_signal_scan_report_min_interval_seconds),
        )
        self.admin_signal_report_mode = str(os.getenv("ADMIN_SIGNAL_REPORT_MODE", "batch")).strip().lower()
        if self.admin_signal_report_mode not in {"batch", "per_tf"}:
            self.admin_signal_report_mode = "batch"
        self.admin_scan_batch_timeout_seconds = max(
            0.01,
            float(os.getenv("ADMIN_SCAN_BATCH_TIMEOUT_SECONDS", "120")),
        )
        self._last_admin_signal_scan_report_at = 0.0
        self._last_admin_signal_scan_report_task: asyncio.Task | None = None

        self._states: dict[tuple[str, str], ScheduleState] = {}
        self._running_market_tasks: set[tuple[str, str]] = set()
        self._bootstrap_lock = asyncio.Lock()
        self._cycle_metrics_window: list[MarketEntryProcessResult] = []
        self._last_cycle_metrics_log_at = time.monotonic()
        self._cycle_metrics_log_interval_seconds = 15.0
        self._tf_scan_status: dict[str, TimeframeScanStatus] = {}
        self._tf_status_log_interval_seconds = float(os.getenv("TF_STATUS_LOG_INTERVAL_SECONDS", "60"))
        self._last_tf_status_log_at: dict[str, float] = {}
        self._last_tf_stall_alert_at: dict[str, float] = {}
        log.info(
            "[signal-concurrency] fast_lane_15m=%s other=%s hard_limit=%s admin_report_mode=%s batch_timeout_seconds=%s",
            self.signal_max_concurrency_15m,
            self.signal_max_concurrency_other,
            self.signal_max_concurrency_hard_limit,
            self.admin_signal_report_mode,
            self.admin_scan_batch_timeout_seconds,
        )

    async def bootstrap(self) -> None:
        log.info("[runtime] bootstrap start")

        for symbol in self.symbols:
            for tf in self.tfs:
                await self._bootstrap_symbol_tf(symbol, tf)

        log.info("[runtime] bootstrap done states=%s", len(self._states))

    async def _bootstrap_symbol_tf(self, symbol: str, tf: str) -> None:
        validation = validate_binance_usdt_perp_symbol(symbol)
        if not validation.valid:
            log.warning(
                "[runtime] INVALID_SYMBOL_SKIPPED scope=bootstrap symbol=%s reason=%s tf=%s",
                validation.normalized_symbol or validation.symbol,
                validation.reason,
                tf,
            )
            return

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
            active_symbols, invalid_symbols = filter_valid_binance_usdt_symbols(self.symbols_provider())
            if invalid_symbols:
                log.warning(
                    "[runtime] INVALID_SYMBOL_SKIPPED scope=symbols_provider count=%s examples=%s",
                    len(invalid_symbols),
                    [f"{item.normalized_symbol or item.symbol}:{item.reason}" for item in invalid_symbols[:10]],
                )
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
        await self.run_initial_signal_scan()

        await asyncio.gather(
            self._scheduler_loop(),
            self._exit_loop(),
        )

    async def run_initial_signal_scan(self) -> list[MarketEntryProcessResult]:
        if not self._states:
            log.info(
                "[startup-initial-scan] active_symbols=%s timeframes=%s started=False reason=no_states",
                len(self.symbols),
                self.tfs,
            )
            return []

        started_at = time.perf_counter()
        log.info(
            "[startup-initial-scan] active_symbols=%s timeframes=%s started=True",
            len(self.symbols),
            self.tfs,
        )
        rows: list[MarketEntryProcessResult] = []
        all_states = sorted(
            list(self._states.items()),
            key=lambda item: (0 if item[1].tf == "15m" else 1, item[1].tf, item[1].symbol),
        )
        rows.extend(
            await self._run_scan_batch(
                batch_id=f"startup:{int(time.time() * 1000)}",
                due_groups=self._group_due_states_by_timeframe(all_states),
                now_ms=int(time.time() * 1000),
                force_signal_scan=True,
                update_scheduler=False,
            )
        )

        log.info(
            "[startup-initial-scan-done] duration_ms=%s signals=%s created=%s blocked=%s checks=%s",
            int((time.perf_counter() - started_at) * 1000),
            sum(int(row.v1_checked) + int(row.v2_checked) for row in rows),
            sum(int(row.created) for row in rows),
            sum(int(row.blocked) for row in rows),
            len(rows),
        )
        return rows

    async def _scheduler_loop(self) -> None:
        log.info("[runtime] scheduler loop started")

        while True:
            now_ms = int(time.time() * 1000)

            await self._sync_dynamic_symbols()

            due_states: list[tuple[tuple[str, str], ScheduleState]] = []
            for key, state in list(self._states.items()):
                if not self.scheduler.should_run(state, now_ms):
                    continue

                if key in self._running_market_tasks:
                    continue

                due_states.append((key, state))

            # 15m is the fast lane: schedule it first, and let its dedicated semaphore keep it moving.
            due_states.sort(key=lambda item: (0 if item[1].tf == "15m" else 1, item[1].next_check_time_ms, item[1].symbol))

            due_tfs = {state.tf for _key, state in due_states}
            self._record_not_due_timeframes(now_ms=now_ms, due_tfs=due_tfs)

            due_groups = self._group_due_states_by_timeframe(due_states)
            if due_groups:
                asyncio.create_task(
                    self._run_scan_batch(
                        batch_id=str(now_ms),
                        due_groups=due_groups,
                        now_ms=now_ms,
                    )
                )

            await asyncio.sleep(self.scheduler_poll_ms / 1000.0)

    @staticmethod
    def _group_due_states_by_timeframe(
        due_states: list[tuple[tuple[str, str], ScheduleState]],
    ) -> list[tuple[str, list[tuple[tuple[str, str], ScheduleState]]]]:
        grouped: dict[str, list[tuple[tuple[str, str], ScheduleState]]] = {}
        for item in due_states:
            grouped.setdefault(item[1].tf, []).append(item)
        return sorted(grouped.items(), key=lambda item: (0 if item[0] == "15m" else 1, item[0]))

    def _active_symbols_for_tf(self, tf: str) -> int:
        return sum(1 for state in self._states.values() if state.tf == tf)

    def _scheduler_state_for_tf(self, tf: str, now_ms: int) -> str:
        states = [state for state in self._states.values() if state.tf == tf]
        if not states:
            return "no_states"
        running = sum(1 for state in states if (state.symbol, state.tf) in self._running_market_tasks)
        next_check = min((state.next_check_time_ms for state in states), default=None)
        if hasattr(self.scheduler, "should_run"):
            due = sum(1 for state in states if self.scheduler.should_run(state, now_ms))
        else:
            due = 0
        return f"due={due},running={running},next_check={next_check}"

    def _skip_reason_for_tf(self, tf: str, now_ms: int) -> str:
        states = [state for state in self._states.values() if state.tf == tf]
        if not states:
            return "no_active_symbols"
        if any((state.symbol, state.tf) in self._running_market_tasks for state in states):
            return "fast_lane_priority"
        if not hasattr(self.scheduler, "should_run"):
            return "waiting_scheduler"
        if not any(self.scheduler.should_run(state, now_ms) for state in states):
            return "scheduler_not_due"
        return "waiting_scheduler"

    def _record_not_due_timeframes(self, *, now_ms: int, due_tfs: set[str]) -> None:
        for tf in self.tfs:
            if tf in due_tfs:
                continue
            active_symbols = self._active_symbols_for_tf(tf)
            reason = self._skip_reason_for_tf(tf, now_ms)
            status = TimeframeScanStatus(
                tf=tf,
                active_symbols=active_symbols,
                checked=0,
                skipped=active_symbols,
                failed=0,
                duration_ms=0,
                reason=reason,
                scheduler_state=self._scheduler_state_for_tf(tf, now_ms),
                last_success_at_ms=self._tf_scan_status.get(
                    tf,
                    TimeframeScanStatus(tf=tf, active_symbols=active_symbols),
                ).last_success_at_ms,
            )
            self._update_tf_scan_status(status)
            self._maybe_log_tf_skip_status(status)
            self._maybe_alert_stalled_timeframe(status, now_ms=now_ms)

    def _update_tf_scan_status(self, status: TimeframeScanStatus) -> None:
        self._tf_scan_status[status.tf] = status

    def _maybe_log_tf_skip_status(self, status: TimeframeScanStatus) -> None:
        now = time.monotonic()
        last_logged = self._last_tf_status_log_at.get(status.tf, 0.0)
        if now - last_logged < self._tf_status_log_interval_seconds:
            return
        self._last_tf_status_log_at[status.tf] = now
        log.info(
            "[tf-scan-finished] tf=%s checked=0 skipped=%s failed=0 duration_ms=0 reason=%s scheduler_state=%s",
            status.tf,
            status.skipped,
            status.reason,
            status.scheduler_state,
        )

    def _maybe_alert_stalled_timeframe(self, status: TimeframeScanStatus, *, now_ms: int) -> None:
        if self.admin_alert_service is None or status.active_symbols <= 0:
            return
        if status.checked > 0:
            return
        interval_ms = self._timeframe_interval_ms(status.tf)
        if interval_ms <= 0:
            return
        last_success = status.last_success_at_ms
        if last_success is None:
            return
        if now_ms - int(last_success) <= interval_ms * 2:
            return
        now = time.monotonic()
        last_alert = self._last_tf_stall_alert_at.get(status.tf, 0.0)
        if now - last_alert < 600:
            return
        self._last_tf_stall_alert_at[status.tf] = now
        log.warning(
            "[tf-scan-stalled] tf=%s active=%s checked=0 last_success_at=%s reason=%s scheduler_state=%s",
            status.tf,
            status.active_symbols,
            last_success,
            status.reason,
            status.scheduler_state,
        )
        notifier = getattr(self.admin_alert_service, "notifier", None)
        if getattr(notifier, "enabled", False):
            asyncio.create_task(self._send_tf_stall_alert(status, last_success_at_ms=int(last_success)))

    async def _send_tf_stall_alert(self, status: TimeframeScanStatus, *, last_success_at_ms: int) -> None:
        notifier = getattr(self.admin_alert_service, "notifier", None)
        if not getattr(notifier, "enabled", False):
            return
        try:
            await notifier.send_message(
                "\n".join(
                    [
                        "⚠️ <b>Timeframe scan stalled</b>",
                        f"TF: {status.tf}",
                        f"Last successful scan: {last_success_at_ms}",
                        f"Reason: {status.reason}",
                        f"Scheduler: {status.scheduler_state}",
                    ]
                )
            )
        except Exception as exc:
            log.warning(
                "[tf-scan-stalled] admin alert failed tf=%s error=%s:%s",
                status.tf,
                type(exc).__name__,
                exc,
            )

    def _timeframe_interval_ms(self, tf: str) -> int:
        try:
            return int(self.scheduler.config.timeframe_ms(tf))
        except Exception:
            return 0

    async def _run_scan_batch(
        self,
        *,
        batch_id: str,
        due_groups: list[tuple[str, list[tuple[tuple[str, str], ScheduleState]]]],
        now_ms: int,
        force_signal_scan: bool = False,
        update_scheduler: bool = True,
    ) -> list[MarketEntryProcessResult]:
        started_at = time.perf_counter()
        due_timeframes = tuple(tf for tf, _batch in due_groups)
        log.info(
            "[scan-batch-start] batch_id=%s due_timeframes=%s all_timeframes=%s active_symbols=%s",
            batch_id,
            list(due_timeframes),
            self.tfs,
            len(self.symbols),
        )

        for _tf, batch in due_groups:
            for key, _state in batch:
                self._running_market_tasks.add(key)

        tasks: dict[str, asyncio.Task[list[MarketEntryProcessResult]]] = {
            tf: asyncio.create_task(
                self._run_market_entry_batch(
                    tf=tf,
                    batch=batch,
                    now_ms=now_ms,
                    force_signal_scan=force_signal_scan,
                    update_scheduler=update_scheduler,
                    batch_id=batch_id,
                    send_admin_report=self.admin_signal_report_mode == "per_tf",
                )
            )
            for tf, batch in due_groups
        }

        done, pending = await asyncio.wait(
            set(tasks.values()),
            timeout=self.admin_scan_batch_timeout_seconds,
        )
        rows: list[MarketEntryProcessResult] = []
        completed_timeframes: list[str] = []
        failed_timeframes: list[str] = []
        for tf, task in tasks.items():
            if task not in done:
                continue
            try:
                tf_rows = task.result()
            except Exception as exc:
                failed_timeframes.append(tf)
                log.exception(
                    "[scan-batch-tf-completed] batch_id=%s tf=%s failed=True error=%s:%s",
                    batch_id,
                    tf,
                    type(exc).__name__,
                    exc,
                )
                continue
            rows.extend(tf_rows)
            completed_timeframes.append(tf)
            status = self._tf_scan_status.get(tf)
            log.info(
                "[scan-batch-tf-completed] batch_id=%s tf=%s duration_ms=%s checked=%s skipped=%s failed=%s",
                batch_id,
                tf,
                status.duration_ms if status else 0,
                status.checked if status else len(tf_rows),
                status.skipped if status else 0,
                status.failed if status else 0,
            )

        pending_timeframes = [tf for tf, task in tasks.items() if task in pending]
        for tf in pending_timeframes:
            active_symbols = self._active_symbols_for_tf(tf)
            tasks[tf].add_done_callback(
                lambda task, completed_tf=tf: self._handle_late_scan_batch_task(batch_id, completed_tf, task)
            )
            self._update_tf_scan_status(
                TimeframeScanStatus(
                    tf=tf,
                    active_symbols=active_symbols,
                    checked=0,
                    skipped=active_symbols,
                    failed=0,
                    duration_ms=int((time.perf_counter() - started_at) * 1000),
                    reason="timeout_waiting_for_tf",
                    scheduler_state=self._scheduler_state_for_tf(tf, now_ms),
                    last_success_at_ms=self._tf_scan_status.get(
                        tf,
                        TimeframeScanStatus(tf=tf, active_symbols=active_symbols),
                    ).last_success_at_ms,
                )
            )

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        log.info(
            "[scan-batch-report-ready] batch_id=%s due_timeframes=%s completed_timeframes=%s pending_timeframes=%s failed_timeframes=%s duration_ms=%s",
            batch_id,
            list(due_timeframes),
            completed_timeframes,
            pending_timeframes,
            failed_timeframes,
            duration_ms,
        )

        if self.admin_signal_report_mode == "batch":
            self._maybe_send_admin_signal_scan_report(
                rows,
                batch_context=ScanBatchReportContext(
                    batch_id=batch_id,
                    due_timeframes=tuple(due_timeframes),
                    completed_timeframes=tuple(completed_timeframes),
                    pending_timeframes=tuple(pending_timeframes),
                    duration_ms=duration_ms,
                ),
            )
        return rows

    def _handle_late_scan_batch_task(
        self,
        batch_id: str,
        tf: str,
        task: asyncio.Task[list[MarketEntryProcessResult]],
    ) -> None:
        try:
            rows = task.result()
            status = self._tf_scan_status.get(tf)
            log.info(
                "[scan-batch-tf-completed-late] batch_id=%s tf=%s checked=%s skipped=%s failed=%s duration_ms=%s report_already_sent=True",
                batch_id,
                tf,
                status.checked if status else len(rows),
                status.skipped if status else 0,
                status.failed if status else 0,
                status.duration_ms if status else 0,
            )
        except Exception as exc:
            log.warning(
                "[scan-batch-tf-completed-late] batch_id=%s tf=%s failed=True error=%s:%s report_already_sent=True",
                batch_id,
                tf,
                type(exc).__name__,
                exc,
            )

    async def _run_market_entry_batch(
        self,
        *,
        tf: str,
        batch: list[tuple[tuple[str, str], ScheduleState]],
        now_ms: int,
        force_signal_scan: bool = False,
        update_scheduler: bool = True,
        batch_id: str | None = None,
        send_admin_report: bool = True,
    ) -> list[MarketEntryProcessResult]:
        started_at = time.perf_counter()
        active_symbols = self._active_symbols_for_tf(tf)
        log.info(
            "[tf-scan-start] batch_id=%s tf=%s active_symbols=%s scheduled_at=%s force=%s",
            batch_id,
            tf,
            active_symbols,
            now_ms,
            force_signal_scan,
        )
        tasks = [
            asyncio.create_task(
                self._run_market_entry_task(
                    state,
                    now_ms,
                    force_signal_scan=force_signal_scan,
                    update_scheduler=update_scheduler,
                )
            )
            for _key, state in batch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        rows: list[MarketEntryProcessResult] = []
        failed = 0
        cancelled = 0
        for (_key, state), result in zip(batch, results, strict=False):
            if isinstance(result, asyncio.CancelledError):
                cancelled += 1
                rows.append(
                    MarketEntryProcessResult(
                        symbol=state.symbol,
                        tf=state.tf,
                        bar_ready=False,
                        last_closed_time_ms=None,
                        reason="market_entry_cancelled",
                        failed=True,
                    )
                )
            elif isinstance(result, Exception):
                failed += 1
                rows.append(
                    MarketEntryProcessResult(
                        symbol=state.symbol,
                        tf=state.tf,
                        bar_ready=False,
                        last_closed_time_ms=None,
                        reason=f"market_entry_exception:{type(result).__name__}",
                        failed=True,
                    )
                )
            else:
                rows.append(result)
                if result.failed:
                    failed += 1

        summary = SignalWorkerPoolSummary(
            tf=tf,
            submitted=len(batch),
            started=len(tasks),
            completed=sum(1 for row in rows if not row.failed),
            failed=failed,
            cancelled=cancelled,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
        )
        log.info(
            "[signal-worker-pool] batch_id=%s tf=%s submitted=%s started=%s completed=%s failed=%s cancelled=%s duration_ms=%s",
            batch_id,
            summary.tf,
            summary.submitted,
            summary.started,
            summary.completed,
            summary.failed,
            summary.cancelled,
            summary.duration_ms,
        )
        skipped = sum(1 for row in rows if not row.bar_ready and not row.failed)
        reason = "completed" if rows else "no_active_symbols"
        self._update_tf_scan_status(
            TimeframeScanStatus(
                tf=tf,
                active_symbols=active_symbols,
                checked=len(rows),
                skipped=skipped,
                failed=failed,
                duration_ms=summary.duration_ms,
                reason=reason,
                scheduler_state=self._scheduler_state_for_tf(tf, now_ms),
                last_success_at_ms=now_ms if rows and failed < len(rows) else self._tf_scan_status.get(tf, TimeframeScanStatus(tf=tf, active_symbols=active_symbols)).last_success_at_ms,
            )
        )
        log.info(
            "[tf-scan-finished] batch_id=%s tf=%s checked=%s skipped=%s failed=%s duration_ms=%s reason=%s scheduler_state=%s",
            batch_id,
            tf,
            len(rows),
            skipped,
            failed,
            summary.duration_ms,
            reason,
            self._scheduler_state_for_tf(tf, now_ms),
        )
        self._record_cycle_metrics(rows)
        if send_admin_report:
            self._maybe_send_admin_signal_scan_report(
                rows,
                worker_pool=summary,
                batch_context=ScanBatchReportContext(
                    batch_id=batch_id or f"{tf}:{now_ms}",
                    due_timeframes=(tf,),
                    completed_timeframes=(tf,),
                    pending_timeframes=(),
                    duration_ms=summary.duration_ms,
                ),
            )
        return rows

    async def _run_market_entry_task(
        self,
        state: ScheduleState,
        now_ms: int,
        *,
        force_signal_scan: bool = False,
        update_scheduler: bool = True,
    ) -> MarketEntryProcessResult:
        key = (state.symbol, state.tf)
        task_started_at = time.perf_counter()

        try:
            lane_sem = self._market_entry_lane_semaphore(state.tf)
            async with self.market_entry_hard_sem:
                async with lane_sem:
                    result = await self.market_entry_handler(
                        state.symbol,
                        state.tf,
                        now_ms,
                        **({"force_signal_scan": force_signal_scan} if self._market_entry_accepts_force_signal_scan else {}),
                    )

            if result.bar_ready and result.last_closed_time_ms is not None:
                if update_scheduler:
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
                log.info(
                    "[symbol-scan-metrics] duration_ms=%s symbol=%s tf=%s market_sync_ms=%s last_closed_ms=%s get_tail_ms=%s fetch_klines_ms=%s indicator_calculation_ms=%s indicator_build_wait_ms=%s upsert_bars_ms=%s fetch_bars_ms=%s rows=%s cache_mode=%s v1_signal_check_ms=%s v2_signal_check_ms=%s user_routing_ms=%s entry_checks_ms=%s order_trade_creation_ms=%s exit_replay_ms=%s signals=%s active_signals=%s user_checks=%s created=%s blocked=%s reason=%s",
                    result.duration_ms,
                    result.symbol,
                    result.tf,
                    result.market_sync_ms,
                    result.last_closed_ms,
                    result.get_tail_ms,
                    result.fetch_klines_ms,
                    result.indicator_calculation_ms,
                    result.indicator_build_wait_ms,
                    result.upsert_bars_ms,
                    result.fetch_bars_ms,
                    result.feature_rows,
                    result.cache_mode,
                    result.v1_signal_check_ms,
                    result.v2_signal_check_ms,
                    result.user_routing_ms,
                    result.entry_checks_ms,
                    result.order_trade_creation_ms,
                    result.exit_replay_ms,
                    result.signals_computed,
                    result.active_signals,
                    result.user_checks,
                    result.created,
                    result.blocked,
                    result.reason,
                )
            else:
                if update_scheduler:
                    self._states[key] = self.scheduler.on_not_ready(state)
                log.debug(
                    "[runtime] %s %s not ready reason=%s retry_count=%s next_check=%s",
                    state.symbol,
                    state.tf,
                    result.reason,
                    self._states[key].retry_count,
                    self._states[key].next_check_time_ms,
                )
            return result
        except Exception:
            log.exception("[runtime] market/entry task failed for %s %s", state.symbol, state.tf)
            if update_scheduler:
                self._states[key] = self.scheduler.on_not_ready(state)
            return MarketEntryProcessResult(
                symbol=state.symbol,
                tf=state.tf,
                bar_ready=False,
                last_closed_time_ms=None,
                reason="market_entry_exception",
                duration_ms=int((time.perf_counter() - task_started_at) * 1000),
                failed=True,
            )
        finally:
            self._running_market_tasks.discard(key)

    @staticmethod
    def _callable_accepts_kwarg(func, name: str) -> bool:
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return False
        for parameter in signature.parameters.values():
            if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                return True
        return name in signature.parameters

    def _market_entry_lane_semaphore(self, tf: str) -> asyncio.Semaphore:
        return self.market_entry_15m_sem if str(tf) == "15m" else self.market_entry_other_sem

    def _record_cycle_metrics(self, rows: list[MarketEntryProcessResult]) -> None:
        self._cycle_metrics_window.extend(rows)
        now = time.monotonic()
        should_log = (
            now - self._last_cycle_metrics_log_at >= self._cycle_metrics_log_interval_seconds
            or len(self._cycle_metrics_window) >= max(1, len(self._states))
        )
        if not should_log:
            return

        rows = self._cycle_metrics_window
        self._cycle_metrics_window = []
        self._last_cycle_metrics_log_at = now
        totals = self._aggregate_scan_rows(rows)
        log.info(
            "[cycle-metrics] duration_ms=%s max_task_duration_ms=%s active_symbols=%s unique_symbols_checked=%s symbol_tf_checks=%s skipped_checks=%s failed_checks=%s per_tf=%s market_sync_ms=%s last_closed_ms=%s get_tail_ms=%s fetch_klines_ms=%s indicator_calculation_ms=%s indicator_build_wait_ms=%s upsert_bars_ms=%s fetch_bars_ms=%s v1_signal_check_ms=%s v2_signal_check_ms=%s user_routing_ms=%s entry_checks_ms=%s order_trade_creation_ms=%s exit_replay_ms=%s signals=%s active_signals=%s user_checks=%s created=%s blocked=%s slowest=%s",
            totals["duration_ms"],
            totals["max_task_duration_ms"],
            totals["active_symbols"],
            totals["unique_symbols_checked"],
            totals["symbol_tf_checks"],
            totals["skipped_checks"],
            totals["failed_checks"],
            totals["per_tf_counts"],
            totals["market_sync_ms"],
            totals["last_closed_ms"],
            totals["get_tail_ms"],
            totals["fetch_klines_ms"],
            totals["indicator_calculation_ms"],
            totals["indicator_build_wait_ms"],
            totals["upsert_bars_ms"],
            totals["fetch_bars_ms"],
            totals["v1_signal_check_ms"],
            totals["v2_signal_check_ms"],
            totals["user_routing_ms"],
            totals["entry_checks_ms"],
            totals["order_trade_creation_ms"],
            totals["exit_replay_ms"],
            totals["signals_computed"],
            totals["active_signals"],
            totals["user_checks"],
            totals["created"],
            totals["blocked"],
            self._format_slowest_row(totals["slowest"]),
        )

    def _aggregate_scan_rows(self, rows: list[MarketEntryProcessResult]) -> dict[str, object]:
        symbols = {row.symbol for row in rows}
        skipped_rows = [row for row in rows if not row.bar_ready and not row.failed]
        failed_rows = [row for row in rows if row.failed]
        slowest = self._slowest_row(rows)
        return {
            "duration_ms": sum(int(row.duration_ms) for row in rows),
            "max_task_duration_ms": int(slowest.duration_ms) if slowest is not None else 0,
            "active_symbols": len(self.symbols),
            "unique_symbols_checked": len(symbols),
            "symbol_tf_checks": len(rows),
            "skipped_checks": len(skipped_rows),
            "skipped_symbols": len({row.symbol for row in skipped_rows}),
            "failed_checks": len(failed_rows),
            "failed_symbols": len({row.symbol for row in failed_rows}),
            "per_tf_counts": self._per_timeframe_counts(rows),
            "market_sync_ms": sum(int(row.market_sync_ms) for row in rows),
            "last_closed_ms": sum(int(row.last_closed_ms) for row in rows),
            "get_tail_ms": sum(int(row.get_tail_ms) for row in rows),
            "fetch_klines_ms": sum(int(row.fetch_klines_ms) for row in rows),
            "indicator_calculation_ms": sum(int(row.indicator_calculation_ms) for row in rows),
            "indicator_build_wait_ms": sum(int(row.indicator_build_wait_ms) for row in rows),
            "upsert_bars_ms": sum(int(row.upsert_bars_ms) for row in rows),
            "fetch_bars_ms": sum(int(row.fetch_bars_ms) for row in rows),
            "v1_signal_check_ms": sum(int(row.v1_signal_check_ms) for row in rows),
            "v2_signal_check_ms": sum(int(row.v2_signal_check_ms) for row in rows),
            "user_routing_ms": sum(int(row.user_routing_ms) for row in rows),
            "entry_checks_ms": sum(int(row.entry_checks_ms) for row in rows),
            "order_trade_creation_ms": sum(int(row.order_trade_creation_ms) for row in rows),
            "exit_replay_ms": sum(int(row.exit_replay_ms) for row in rows),
            "signals_computed": sum(int(row.signals_computed) for row in rows),
            "active_signals": sum(int(row.active_signals) for row in rows),
            "user_checks": sum(int(row.user_checks) for row in rows),
            "created": sum(int(row.created) for row in rows),
            "blocked": sum(int(row.blocked) for row in rows),
            "v1_checked": sum(int(row.v1_checked) for row in rows),
            "v2_checked": sum(int(row.v2_checked) for row in rows),
            "avg_indicator_ms": self._avg(row.indicator_calculation_ms for row in rows),
            "max_indicator_ms": max((int(row.indicator_calculation_ms) for row in rows), default=0),
            "avg_user_routing_ms": self._avg(row.user_routing_ms for row in rows),
            "max_user_routing_ms": max((int(row.user_routing_ms) for row in rows), default=0),
            "active_signal_breakdown": self._merge_breakdown(rows, "active_signal_breakdown"),
            "created_breakdown": self._merge_breakdown(rows, "created_breakdown"),
            "blocked_breakdown": self._merge_breakdown(rows, "blocked_breakdown"),
            "blocked_reasons": self._merge_reasons(rows, "blocked_reasons"),
            "slowest": slowest,
        }

    @staticmethod
    def _merge_breakdown(rows: list[MarketEntryProcessResult], attr_name: str) -> dict[str, dict]:
        merged: dict[str, dict] = {}
        for row in rows:
            source = getattr(row, attr_name, None) or {}
            if not isinstance(source, dict):
                continue
            for key, item in source.items():
                if not isinstance(item, dict):
                    continue
                count = int(item.get("count", 0) or 0)
                if count <= 0:
                    continue
                merged_item = merged.setdefault(
                    str(key),
                    {
                        "strategy_version": str(item.get("strategy_version") or "v1").lower(),
                        "rule_id": str(item.get("rule_id") or "-"),
                        "signal_level": item.get("signal_level"),
                        "tf": str(item.get("tf") or "-"),
                        "mode": str(item.get("mode") or "-").lower(),
                        "count": 0,
                    },
                )
                merged_item["count"] = int(merged_item.get("count", 0) or 0) + count
        return dict(sorted(merged.items()))

    @staticmethod
    def _merge_reasons(rows: list[MarketEntryProcessResult], attr_name: str) -> dict[str, int]:
        merged: dict[str, int] = {}
        for row in rows:
            source = getattr(row, attr_name, None) or {}
            if not isinstance(source, dict):
                continue
            for reason, count in source.items():
                normalized = str(reason or "unknown").strip() or "unknown"
                merged[normalized] = int(merged.get(normalized, 0) or 0) + int(count or 0)
        return dict(sorted(merged.items(), key=lambda item: (-item[1], item[0])))

    @staticmethod
    def _avg(values) -> int:
        rows = [int(value or 0) for value in values]
        if not rows:
            return 0
        return int(sum(rows) / len(rows))

    @staticmethod
    def _per_timeframe_counts(rows: list[MarketEntryProcessResult]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.tf] = counts.get(row.tf, 0) + 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _slowest_row(rows: list[MarketEntryProcessResult]) -> MarketEntryProcessResult | None:
        return max(rows, key=lambda row: int(row.duration_ms), default=None)

    @staticmethod
    def _format_slowest_row(row: MarketEntryProcessResult | None) -> str:
        if row is None:
            return "-"
        return (
            f"{row.symbol}:{row.tf}:{int(row.duration_ms)}ms"
            f":market_sync_ms={int(row.market_sync_ms)}"
            f":last_closed_ms={int(row.last_closed_ms)}"
            f":get_tail_ms={int(row.get_tail_ms)}"
            f":fetch_klines_ms={int(row.fetch_klines_ms)}"
            f":fetch_bars_ms={int(row.fetch_bars_ms)}"
            f":indicator_calc_ms={int(row.indicator_calculation_ms)}"
            f":indicator_build_wait_ms={int(row.indicator_build_wait_ms)}"
            f":upsert_bars_ms={int(row.upsert_bars_ms)}"
            f":cache_mode={row.cache_mode}"
            f":rows={int(row.feature_rows)}"
            f":v1_signal_ms={int(row.v1_signal_check_ms)}"
            f":v2_signal_ms={int(row.v2_signal_check_ms)}"
            f":user_routing_ms={int(row.user_routing_ms)}"
            f":entry_checks_ms={int(row.entry_checks_ms)}"
            f":order_creation_ms={int(row.order_trade_creation_ms)}"
            f":reason={row.reason}"
        )

    def _maybe_send_admin_signal_scan_report(
        self,
        rows: list[MarketEntryProcessResult],
        *,
        worker_pool: SignalWorkerPoolSummary | None = None,
        batch_context: ScanBatchReportContext | None = None,
    ) -> None:
        if self.admin_alert_service is None:
            return
        notifier = getattr(self.admin_alert_service, "notifier", None)
        if not getattr(notifier, "enabled", False):
            return
        now = time.monotonic()
        if (
            self._last_admin_signal_scan_report_at
            and now - self._last_admin_signal_scan_report_at < self.admin_signal_scan_report_min_interval_seconds
        ):
            return
        self._last_admin_signal_scan_report_at = now
        self._last_admin_signal_scan_report_task = asyncio.create_task(
            self._send_admin_signal_scan_report(
                rows,
                worker_pool=worker_pool,
                batch_context=batch_context,
            )
        )

    def _format_timeframe_status_lines(self, rows: list[MarketEntryProcessResult]) -> list[str]:
        current_by_tf: dict[str, list[MarketEntryProcessResult]] = {}
        for row in rows:
            current_by_tf.setdefault(row.tf, []).append(row)

        lines: list[str] = []
        tfs = list(dict.fromkeys([*self.tfs, *sorted(current_by_tf)]))
        for tf in tfs:
            tf_rows = current_by_tf.get(tf)
            if tf_rows:
                checked = len(tf_rows)
                skipped = sum(1 for row in tf_rows if not row.bar_ready and not row.failed)
                failed = sum(1 for row in tf_rows if row.failed)
                duration_ms = max((int(row.duration_ms) for row in tf_rows), default=0)
                active = self._active_symbols_for_tf(tf)
                reason = "completed"
            else:
                status = self._tf_scan_status.get(
                    tf,
                    TimeframeScanStatus(
                        tf=tf,
                        active_symbols=self._active_symbols_for_tf(tf),
                        checked=0,
                        skipped=self._active_symbols_for_tf(tf),
                        failed=0,
                        duration_ms=0,
                        reason="waiting_scheduler",
                        scheduler_state=self._scheduler_state_for_tf(tf, int(time.time() * 1000)),
                    ),
                )
                active = status.active_symbols
                checked = status.checked
                skipped = status.skipped
                failed = status.failed
                duration_ms = status.duration_ms
                reason = status.reason

            lines.append(
                f"{tf}: active={active} checked={checked} skipped={skipped} failed={failed} duration={duration_ms}ms reason={reason}"
            )
        return lines

    @staticmethod
    def _strategy_breakdown_summary(breakdown: dict[str, dict]) -> dict[str, dict[str, dict]]:
        summary: dict[str, dict[str, dict]] = {}
        for item in breakdown.values():
            if not isinstance(item, dict):
                continue
            version = str(item.get("strategy_version") or "v1").lower()
            rule_id = str(item.get("rule_id") or "-")
            count = int(item.get("count", 0) or 0)
            if count <= 0:
                continue
            rule_summary = summary.setdefault(version, {}).setdefault(
                rule_id,
                {"total": 0, "levels": {}},
            )
            rule_summary["total"] = int(rule_summary.get("total", 0) or 0) + count
            level = str(item.get("signal_level") or "").lower()
            if version == "v2" and level in {"weak", "medium", "strong"}:
                levels = rule_summary.setdefault("levels", {})
                levels[level] = int(levels.get(level, 0) or 0) + count
        return summary

    def _format_strategy_breakdown(
        self,
        *,
        title: str,
        total: int,
        breakdown: dict[str, dict],
    ) -> list[str]:
        lines = [f"{title}: {int(total)}"]
        summary = self._strategy_breakdown_summary(breakdown)
        if not summary:
            lines.append("- no breakdown")
            return lines

        for version in ("v1", "v2"):
            rules = summary.get(version)
            if not rules:
                continue
            lines.append(f"{version.upper()}:")
            for rule_id, item in sorted(rules.items()):
                rule_total = int(item.get("total", 0) or 0)
                if version == "v2":
                    levels = item.get("levels") if isinstance(item.get("levels"), dict) else {}
                    lines.append(f"- {rule_id}:")
                    level_total = 0
                    for level in ("weak", "medium", "strong"):
                        level_count = int(levels.get(level, 0) or 0)
                        level_total += level_count
                        lines.append(f"  {level}: {level_count}")
                    unclassified = rule_total - level_total
                    if unclassified > 0:
                        lines.append(f"  unclassified: {unclassified}")
                else:
                    lines.append(f"- {rule_id}: {rule_total}")
        return lines

    @staticmethod
    def _v2_strong_signal_count(breakdown: dict[str, dict]) -> int:
        count = 0
        for item in breakdown.values():
            if not isinstance(item, dict):
                continue
            if str(item.get("strategy_version") or "").lower() == "v2" and str(item.get("signal_level") or "").lower() == "strong":
                count += int(item.get("count", 0) or 0)
        return count

    @staticmethod
    def _format_top_block_reasons(reasons: dict[str, int], *, limit: int = 5) -> list[str]:
        lines = ["Top block reasons:"]
        if not reasons:
            lines.append("- none")
            return lines
        for reason, count in sorted(reasons.items(), key=lambda item: (-int(item[1] or 0), item[0]))[:limit]:
            lines.append(f"- {reason}: {int(count or 0)}")
        return lines

    async def _send_admin_signal_scan_report(
        self,
        rows: list[MarketEntryProcessResult],
        *,
        worker_pool: SignalWorkerPoolSummary | None = None,
        batch_context: ScanBatchReportContext | None = None,
    ) -> None:
        notifier = getattr(self.admin_alert_service, "notifier", None)
        if not getattr(notifier, "enabled", False):
            return
        totals = self._aggregate_scan_rows(rows)
        due_timeframes = tuple(batch_context.due_timeframes if batch_context else ())
        completed_timeframes = tuple(batch_context.completed_timeframes if batch_context else ())
        pending_timeframes = tuple(batch_context.pending_timeframes if batch_context else ())
        timeframes = sorted({row.tf for row in rows} | set(due_timeframes) | set(pending_timeframes))
        per_tf_lines = self._format_timeframe_status_lines(rows)
        slowest = totals["slowest"]
        strong_signal_count = self._v2_strong_signal_count(totals["active_signal_breakdown"])
        text = "\n".join(
            [
                "📊 <b>Signal scan completed</b>",
                f"Batch: {batch_context.batch_id if batch_context else '-'}",
                f"⏱ Duration: {batch_context.duration_ms if batch_context else worker_pool.duration_ms if worker_pool else totals['duration_ms']} ms",
                f"🕒 Timeframes: {', '.join(timeframes) if timeframes else '-'}",
                f"Due TF: {', '.join(due_timeframes) if due_timeframes else '-'}",
                f"Completed TF: {', '.join(completed_timeframes) if completed_timeframes else '-'}",
                f"Pending TF: {', '.join(pending_timeframes) if pending_timeframes else '-'}",
                f"🪙 Active symbols: {totals['active_symbols']}",
                f"🪙 Unique symbols checked: {totals['unique_symbols_checked']}",
                f"🧮 Symbol/TF checks: {totals['symbol_tf_checks']}",
                f"↩️ Skipped symbols: {totals['skipped_symbols']} ({totals['skipped_checks']} checks)",
                f"❌ Failed symbols: {totals['failed_symbols']} ({totals['failed_checks']} checks)",
                "Per TF:",
                *(per_tf_lines or ["-"]),
                (
                    "Worker pool: "
                    f"tf={worker_pool.tf} submitted={worker_pool.submitted} started={worker_pool.started} "
                    f"completed={worker_pool.completed} failed={worker_pool.failed} cancelled={worker_pool.cancelled}"
                    if worker_pool is not None
                    else "Worker pool: -"
                ),
                (
                    "⚙️ Concurrency: "
                    f"15m={self.signal_max_concurrency_15m}, "
                    f"other={self.signal_max_concurrency_other}, "
                    f"hard={self.signal_max_concurrency_hard_limit}"
                ),
                f"📈 Signals: V1={totals['v1_checked']}, V2={totals['v2_checked']}",
                *(["🔥 Strong signals detected: " + str(strong_signal_count)] if strong_signal_count > 0 else []),
                *self._format_strategy_breakdown(
                    title="📈 Active signals",
                    total=int(totals["active_signals"]),
                    breakdown=totals["active_signal_breakdown"],
                ),
                *self._format_strategy_breakdown(
                    title="✅ Created trades",
                    total=int(totals["created"]),
                    breakdown=totals["created_breakdown"],
                ),
                *self._format_strategy_breakdown(
                    title="⛔ Blocked trades",
                    total=int(totals["blocked"]),
                    breakdown=totals["blocked_breakdown"],
                ),
                *self._format_top_block_reasons(totals["blocked_reasons"]),
                f"avg/max indicator: {totals['avg_indicator_ms']}/{totals['max_indicator_ms']} ms",
                f"Market sync total: {totals['market_sync_ms']} ms",
                f"avg/max user routing: {totals['avg_user_routing_ms']}/{totals['max_user_routing_ms']} ms",
                (
                    "🐢 Slowest: "
                    f"{slowest.symbol} {slowest.tf} {int(slowest.duration_ms)} ms\n"
                    f"market_sync_ms={int(slowest.market_sync_ms)}\n"
                    f"last_closed_ms={int(slowest.last_closed_ms)}\n"
                    f"get_tail_ms={int(slowest.get_tail_ms)}\n"
                    f"fetch_klines_ms={int(slowest.fetch_klines_ms)}\n"
                    f"fetch_bars_ms={int(slowest.fetch_bars_ms)}\n"
                    f"indicator_calc_ms={int(slowest.indicator_calculation_ms)}\n"
                    f"indicator_build_wait_ms={int(slowest.indicator_build_wait_ms)}\n"
                    f"upsert_bars_ms={int(slowest.upsert_bars_ms)}\n"
                    f"cache_mode={slowest.cache_mode}\n"
                    f"rows={int(slowest.feature_rows)}\n"
                    f"v1_signal_ms={int(slowest.v1_signal_check_ms)}\n"
                    f"v2_signal_ms={int(slowest.v2_signal_check_ms)}\n"
                    f"user_routing_ms={int(slowest.user_routing_ms)}\n"
                    f"entry_checks_ms={int(slowest.entry_checks_ms)}\n"
                    f"order_creation_ms={int(slowest.order_trade_creation_ms)}\n"
                    f"reason={slowest.reason}"
                    if slowest is not None
                    else "🐢 Slowest: -"
                ),
            ]
        )
        try:
            await notifier.send_message(text)
            log.info(
                "[admin-signal-scan-report] sent batch_id=%s duration_ms=%s active_symbols=%s unique_symbols_checked=%s symbol_tf_checks=%s timeframes_included=%s due_timeframes=%s completed_timeframes=%s pending_timeframes=%s created=%s blocked=%s",
                batch_context.batch_id if batch_context else None,
                batch_context.duration_ms if batch_context else worker_pool.duration_ms if worker_pool else totals["duration_ms"],
                totals["active_symbols"],
                totals["unique_symbols_checked"],
                totals["symbol_tf_checks"],
                timeframes,
                list(due_timeframes),
                list(completed_timeframes),
                list(pending_timeframes),
                totals["created"],
                totals["blocked"],
            )
        except Exception as exc:
            log.warning(
                "[admin-signal-scan-report] delivery failed error=%s:%s",
                type(exc).__name__,
                exc,
            )

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
