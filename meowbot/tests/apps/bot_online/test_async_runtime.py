from __future__ import annotations

import asyncio

import pytest

from meowbot.apps.bot_online.async_runtime import AsyncBotOnlineRuntime, MarketEntryProcessResult


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _last_closed_provider(symbol: str, tf: str) -> int | None:
    return 1


async def _market_entry_handler(symbol: str, tf: str, now_ms: int) -> MarketEntryProcessResult:
    return MarketEntryProcessResult(symbol=symbol, tf=tf, bar_ready=False, last_closed_time_ms=None, reason="test")


async def _exit_handler(now_ms: int) -> None:
    return None


class FakeNotifier:
    enabled = True

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, text: str, *, chat_id: str | None = None) -> None:
        self.messages.append(text)


class FakeAdminAlertService:
    def __init__(self) -> None:
        self.notifier = FakeNotifier()


class FakeState:
    def __init__(self, symbol: str, tf: str) -> None:
        self.symbol = symbol
        self.tf = tf
        self.next_check_time_ms = 1
        self.retry_count = 0


class FakeScheduler:
    def should_run(self, state, now_ms: int) -> bool:
        return True

    def on_not_ready(self, state):
        return state

    def on_bar_ready(self, state, *, new_last_closed_time_ms: int):
        return state


class ShiftingScheduler(FakeScheduler):
    def on_bar_ready(self, state, *, new_last_closed_time_ms: int):
        shifted = FakeState(state.symbol, state.tf)
        shifted.next_check_time_ms = 999
        return shifted


def _runtime(**kwargs) -> AsyncBotOnlineRuntime:
    return AsyncBotOnlineRuntime(
        symbols=kwargs.pop("symbols", []),
        tfs=kwargs.pop("tfs", []),
        scheduler=kwargs.pop("scheduler", object()),
        last_closed_provider=_last_closed_provider,
        market_entry_handler=kwargs.pop("market_entry_handler", _market_entry_handler),
        exit_handler=_exit_handler,
        **kwargs,
    )


def test_runtime_uses_dedicated_signal_fast_lane_semaphores() -> None:
    runtime = _runtime(
        signal_max_concurrency_15m=60,
        signal_max_concurrency_other=20,
        signal_max_concurrency_hard_limit=100,
    )

    assert runtime._market_entry_lane_semaphore("15m") is runtime.market_entry_15m_sem
    assert runtime._market_entry_lane_semaphore("30m") is runtime.market_entry_other_sem
    assert runtime.signal_max_concurrency_15m == 60
    assert runtime.signal_max_concurrency_other == 20
    assert runtime.signal_max_concurrency_hard_limit == 100


@pytest.mark.anyio
async def test_admin_signal_scan_report_contains_scope_and_concurrency() -> None:
    admin_alert_service = FakeAdminAlertService()
    runtime = _runtime(
        admin_alert_service=admin_alert_service,
        signal_max_concurrency_15m=60,
        signal_max_concurrency_other=20,
        signal_max_concurrency_hard_limit=100,
    )

    await runtime._send_admin_signal_scan_report(
        [
            MarketEntryProcessResult(
                symbol="BTCUSDT",
                tf="15m",
                bar_ready=True,
                last_closed_time_ms=1,
                reason="entry_processed",
                duration_ms=120,
                fetch_bars_ms=3,
                feature_rows=200,
                cache_mode="memory",
                v1_checked=1,
                v2_checked=1,
                created=1,
                blocked=2,
            ),
            MarketEntryProcessResult(
                symbol="ETHUSDT",
                tf="30m",
                bar_ready=True,
                last_closed_time_ms=1,
                reason="entry_processed",
                duration_ms=250,
                indicator_calculation_ms=11,
                fetch_bars_ms=5,
                feature_rows=180,
                cache_mode="memory",
                v1_checked=0,
                v2_checked=1,
            ),
        ]
    )

    assert admin_alert_service.notifier.messages
    text = admin_alert_service.notifier.messages[0]
    assert "Signal scan completed" in text
    assert "Active symbols: 0" in text
    assert "Unique symbols checked: 2" in text
    assert "Symbol/TF checks: 2" in text
    assert "15m: active=0 checked=1" in text
    assert "30m: active=0 checked=1" in text
    assert "Concurrency: 15m=60, other=20, hard=100" in text
    assert "Signals: V1=1, V2=2" in text
    assert "Created trades: 1" in text
    assert "Blocked trades: 2" in text
    assert "Slowest: ETHUSDT 30m 250 ms" in text
    assert "fetch_bars_ms=5" in text
    assert "indicator_calc_ms=11" in text
    assert "Market sync total:" in text
    assert "cache_mode=memory" in text
    assert "rows=180" in text


@pytest.mark.anyio
async def test_admin_signal_scan_report_includes_strategy_rule_level_breakdowns() -> None:
    admin_alert_service = FakeAdminAlertService()
    runtime = _runtime(admin_alert_service=admin_alert_service)

    await runtime._send_admin_signal_scan_report(
        [
            MarketEntryProcessResult(
                symbol="BTCUSDT",
                tf="15m",
                bar_ready=True,
                last_closed_time_ms=1,
                reason="entry_processed",
                active_signals=4,
                created=5,
                blocked=23,
                active_signal_breakdown={
                    "v1|RSI_REBOUND_ST_124|-|15m|signal": {
                        "strategy_version": "v1",
                        "rule_id": "RSI_REBOUND_ST_124",
                        "signal_level": None,
                        "tf": "15m",
                        "mode": "signal",
                        "count": 1,
                    },
                    "v2|LONG_BREAKOUT_V18|weak|15m|signal": {
                        "strategy_version": "v2",
                        "rule_id": "LONG_BREAKOUT_V18",
                        "signal_level": "weak",
                        "tf": "15m",
                        "mode": "signal",
                        "count": 1,
                    },
                    "v2|LONG_BREAKOUT_V18|medium|15m|signal": {
                        "strategy_version": "v2",
                        "rule_id": "LONG_BREAKOUT_V18",
                        "signal_level": "medium",
                        "tf": "15m",
                        "mode": "signal",
                        "count": 2,
                    },
                },
                created_breakdown={
                    "v1|RSI_REBOUND_ST_124|-|15m|sandbox": {
                        "strategy_version": "v1",
                        "rule_id": "RSI_REBOUND_ST_124",
                        "signal_level": None,
                        "tf": "15m",
                        "mode": "sandbox",
                        "count": 2,
                    },
                    "v2|LONG_BREAKOUT_V18|weak|15m|sandbox": {
                        "strategy_version": "v2",
                        "rule_id": "LONG_BREAKOUT_V18",
                        "signal_level": "weak",
                        "tf": "15m",
                        "mode": "sandbox",
                        "count": 1,
                    },
                    "v2|LONG_BREAKOUT_V18|medium|15m|live": {
                        "strategy_version": "v2",
                        "rule_id": "LONG_BREAKOUT_V18",
                        "signal_level": "medium",
                        "tf": "15m",
                        "mode": "live",
                        "count": 2,
                    },
                },
                blocked_breakdown={
                    "v1|RSI_REBOUND_ST_124|-|15m|sandbox": {
                        "strategy_version": "v1",
                        "rule_id": "RSI_REBOUND_ST_124",
                        "signal_level": None,
                        "tf": "15m",
                        "mode": "sandbox",
                        "count": 5,
                    },
                    "v2|LONG_BREAKOUT_V18|weak|15m|sandbox": {
                        "strategy_version": "v2",
                        "rule_id": "LONG_BREAKOUT_V18",
                        "signal_level": "weak",
                        "tf": "15m",
                        "mode": "sandbox",
                        "count": 8,
                    },
                    "v2|LONG_BREAKOUT_V18|medium|15m|sandbox": {
                        "strategy_version": "v2",
                        "rule_id": "LONG_BREAKOUT_V18",
                        "signal_level": "medium",
                        "tf": "15m",
                        "mode": "sandbox",
                        "count": 7,
                    },
                    "v2|LONG_BREAKOUT_V18|strong|15m|live": {
                        "strategy_version": "v2",
                        "rule_id": "LONG_BREAKOUT_V18",
                        "signal_level": "strong",
                        "tf": "15m",
                        "mode": "live",
                        "count": 3,
                    },
                },
                blocked_reasons={
                    "open_trade_exists_for_symbol": 12,
                    "risk_limit_reached": 7,
                    "cooldown_active": 4,
                },
            )
        ]
    )

    text = admin_alert_service.notifier.messages[0]
    assert "Active signals: 4" in text
    assert "V1:\n- RSI_REBOUND_ST_124: 1" in text
    assert "V2:\n- LONG_BREAKOUT_V18:" in text
    assert "weak: 1" in text
    assert "medium: 2" in text
    assert "strong: 0" in text
    assert "Created trades: 5" in text
    assert "Blocked trades: 23" in text
    assert "strong: 3" in text
    assert "Top block reasons:" in text
    assert "open_trade_exists_for_symbol: 12" in text


@pytest.mark.anyio
async def test_admin_signal_scan_report_includes_unchecked_timeframes() -> None:
    admin_alert_service = FakeAdminAlertService()
    runtime = _runtime(
        symbols=["BTCUSDT", "ETHUSDT"],
        tfs=["15m", "30m", "1h"],
        admin_alert_service=admin_alert_service,
    )
    runtime._states = {
        ("BTCUSDT", "15m"): FakeState("BTCUSDT", "15m"),
        ("ETHUSDT", "15m"): FakeState("ETHUSDT", "15m"),
        ("BTCUSDT", "30m"): FakeState("BTCUSDT", "30m"),
        ("ETHUSDT", "30m"): FakeState("ETHUSDT", "30m"),
        ("BTCUSDT", "1h"): FakeState("BTCUSDT", "1h"),
        ("ETHUSDT", "1h"): FakeState("ETHUSDT", "1h"),
    }
    await runtime._send_admin_signal_scan_report(
        [
            MarketEntryProcessResult(
                symbol="BTCUSDT",
                tf="15m",
                bar_ready=True,
                last_closed_time_ms=1,
                reason="entry_processed",
            ),
            MarketEntryProcessResult(
                symbol="ETHUSDT",
                tf="15m",
                bar_ready=True,
                last_closed_time_ms=1,
                reason="entry_processed",
            ),
        ]
    )
    text = admin_alert_service.notifier.messages[0]
    assert "15m: active=2 checked=2" in text
    assert "30m: active=2 checked=0 skipped=2" in text
    assert "1h: active=2 checked=0 skipped=2" in text


@pytest.mark.anyio
async def test_market_entry_batch_sends_report_after_all_symbol_tasks_complete() -> None:
    admin_alert_service = FakeAdminAlertService()
    completed: list[str] = []

    async def handler(symbol: str, tf: str, now_ms: int) -> MarketEntryProcessResult:
        completed.append(symbol)
        return MarketEntryProcessResult(
            symbol=symbol,
            tf=tf,
            bar_ready=True,
            last_closed_time_ms=now_ms,
            reason="entry_processed",
            duration_ms=10 if symbol != "TRXUSDT" else 30,
            v1_checked=1,
            v2_checked=1,
        )

    runtime = _runtime(
        symbols=["BTCUSDT", "LINKUSDT", "TRXUSDT"],
        scheduler=FakeScheduler(),
        market_entry_handler=handler,
        admin_alert_service=admin_alert_service,
        admin_signal_scan_report_min_interval_seconds=0,
    )
    states = [
        (("BTCUSDT", "15m"), FakeState("BTCUSDT", "15m")),
        (("LINKUSDT", "15m"), FakeState("LINKUSDT", "15m")),
        (("TRXUSDT", "15m"), FakeState("TRXUSDT", "15m")),
    ]
    for key, _state in states:
        runtime._running_market_tasks.add(key)

    await runtime._run_market_entry_batch(tf="15m", batch=states, now_ms=1000)
    assert runtime._last_admin_signal_scan_report_task is not None
    await runtime._last_admin_signal_scan_report_task

    assert completed == ["BTCUSDT", "LINKUSDT", "TRXUSDT"]
    assert len(admin_alert_service.notifier.messages) == 1
    text = admin_alert_service.notifier.messages[0]
    assert "Active symbols: 3" in text
    assert "Unique symbols checked: 3" in text
    assert "Symbol/TF checks: 3" in text
    assert "15m: active=3 checked=3" in text
    assert "Worker pool: tf=15m submitted=3 started=3 completed=3 failed=0 cancelled=0" in text
    assert "Slowest: TRXUSDT 15m 30 ms" in text


@pytest.mark.anyio
async def test_scan_batch_report_waits_for_all_due_timeframes() -> None:
    admin_alert_service = FakeAdminAlertService()
    started_30m = asyncio.Event()
    release_30m = asyncio.Event()

    async def handler(symbol: str, tf: str, now_ms: int) -> MarketEntryProcessResult:
        if tf == "30m":
            started_30m.set()
            await release_30m.wait()
        return MarketEntryProcessResult(
            symbol=symbol,
            tf=tf,
            bar_ready=True,
            last_closed_time_ms=now_ms,
            reason="entry_processed",
            duration_ms=10 if tf == "15m" else 30,
            v1_checked=1,
        )

    runtime = _runtime(
        symbols=["BTCUSDT"],
        tfs=["15m", "30m", "1h"],
        scheduler=FakeScheduler(),
        market_entry_handler=handler,
        admin_alert_service=admin_alert_service,
        admin_signal_scan_report_min_interval_seconds=0,
    )
    runtime._states = {
        ("BTCUSDT", "15m"): FakeState("BTCUSDT", "15m"),
        ("BTCUSDT", "30m"): FakeState("BTCUSDT", "30m"),
        ("BTCUSDT", "1h"): FakeState("BTCUSDT", "1h"),
    }
    due_groups = [
        ("15m", [(("BTCUSDT", "15m"), runtime._states[("BTCUSDT", "15m")])]),
        ("30m", [(("BTCUSDT", "30m"), runtime._states[("BTCUSDT", "30m")])]),
    ]

    batch_task = asyncio.create_task(
        runtime._run_scan_batch(batch_id="batch-1730", due_groups=due_groups, now_ms=1000)
    )
    await started_30m.wait()
    await asyncio.sleep(0.05)
    assert admin_alert_service.notifier.messages == []

    release_30m.set()
    await batch_task
    assert runtime._last_admin_signal_scan_report_task is not None
    await runtime._last_admin_signal_scan_report_task

    assert len(admin_alert_service.notifier.messages) == 1
    text = admin_alert_service.notifier.messages[0]
    assert "Batch: batch-1730" in text
    assert "Due TF: 15m, 30m" in text
    assert "Completed TF: 15m, 30m" in text
    assert "Pending TF: -" in text
    assert "15m: active=1 checked=1" in text
    assert "30m: active=1 checked=1" in text
    assert "1h: active=1 checked=0 skipped=1" in text


@pytest.mark.anyio
async def test_scan_batch_report_timeout_marks_pending_timeframe_once() -> None:
    admin_alert_service = FakeAdminAlertService()
    release_30m = asyncio.Event()

    async def handler(symbol: str, tf: str, now_ms: int) -> MarketEntryProcessResult:
        if tf == "30m":
            await release_30m.wait()
        return MarketEntryProcessResult(
            symbol=symbol,
            tf=tf,
            bar_ready=True,
            last_closed_time_ms=now_ms,
            reason="entry_processed",
            duration_ms=10,
            v1_checked=1,
        )

    runtime = _runtime(
        symbols=["BTCUSDT"],
        tfs=["15m", "30m"],
        scheduler=FakeScheduler(),
        market_entry_handler=handler,
        admin_alert_service=admin_alert_service,
        admin_signal_scan_report_min_interval_seconds=0,
    )
    runtime.admin_scan_batch_timeout_seconds = 0.05
    runtime._states = {
        ("BTCUSDT", "15m"): FakeState("BTCUSDT", "15m"),
        ("BTCUSDT", "30m"): FakeState("BTCUSDT", "30m"),
    }
    due_groups = [
        ("15m", [(("BTCUSDT", "15m"), runtime._states[("BTCUSDT", "15m")])]),
        ("30m", [(("BTCUSDT", "30m"), runtime._states[("BTCUSDT", "30m")])]),
    ]

    await runtime._run_scan_batch(batch_id="batch-timeout", due_groups=due_groups, now_ms=1000)
    assert runtime._last_admin_signal_scan_report_task is not None
    await runtime._last_admin_signal_scan_report_task

    assert len(admin_alert_service.notifier.messages) == 1
    text = admin_alert_service.notifier.messages[0]
    assert "Batch: batch-timeout" in text
    assert "Completed TF: 15m" in text
    assert "Pending TF: 30m" in text
    assert "30m: active=1 checked=0 skipped=1 failed=0" in text
    assert "reason=timeout_waiting_for_tf" in text

    release_30m.set()
    await asyncio.sleep(0.05)
    assert len(admin_alert_service.notifier.messages) == 1


@pytest.mark.anyio
async def test_initial_signal_scan_forces_all_states_without_shifting_scheduler_timing() -> None:
    calls: list[tuple[str, str, bool]] = []

    async def handler(symbol: str, tf: str, now_ms: int, *, force_signal_scan: bool = False) -> MarketEntryProcessResult:
        calls.append((symbol, tf, force_signal_scan))
        return MarketEntryProcessResult(
            symbol=symbol,
            tf=tf,
            bar_ready=True,
            last_closed_time_ms=now_ms,
            reason="entry_processed",
            duration_ms=5,
            v1_checked=1,
        )

    runtime = _runtime(
        symbols=["BTCUSDT", "ETHUSDT"],
        tfs=["15m", "30m"],
        scheduler=ShiftingScheduler(),
        market_entry_handler=handler,
        admin_signal_scan_report_min_interval_seconds=0,
    )
    runtime._states = {
        ("BTCUSDT", "15m"): FakeState("BTCUSDT", "15m"),
        ("ETHUSDT", "15m"): FakeState("ETHUSDT", "15m"),
        ("BTCUSDT", "30m"): FakeState("BTCUSDT", "30m"),
        ("ETHUSDT", "30m"): FakeState("ETHUSDT", "30m"),
    }
    original_state_ids = {key: id(value) for key, value in runtime._states.items()}

    rows = await runtime.run_initial_signal_scan()

    assert len(rows) == 4
    assert all(force for _symbol, _tf, force in calls)
    assert {tf for _symbol, tf, _force in calls} == {"15m", "30m"}
    assert {key: id(value) for key, value in runtime._states.items()} == original_state_ids
