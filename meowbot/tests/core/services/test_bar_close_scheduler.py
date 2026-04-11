from __future__ import annotations

from meowbot.core.services.scheduling.bar_close_scheduler import (
    BarCloseScheduler,
    SchedulerConfig,
)


def test_bootstrap_for_15m() -> None:
    scheduler = BarCloseScheduler(
        SchedulerConfig(
            first_check_delay_ms=15_000,
            retry_delay_ms=15_000,
            max_retries_after_close=8,
        )
    )

    last_close = 1_000_000
    state = scheduler.bootstrap(
        symbol="BTCUSDT",
        tf="15m",
        last_closed_time_ms=last_close,
    )

    assert state.expected_close_time_ms == last_close + 15 * 60_000
    assert state.next_check_time_ms == state.expected_close_time_ms + 15_000
    assert state.retry_count == 0


def test_on_not_ready_moves_check_forward() -> None:
    scheduler = BarCloseScheduler(
        SchedulerConfig(
            first_check_delay_ms=15_000,
            retry_delay_ms=15_000,
            max_retries_after_close=8,
        )
    )

    state = scheduler.bootstrap(
        symbol="BTCUSDT",
        tf="15m",
        last_closed_time_ms=1_000_000,
    )

    next_state = scheduler.on_not_ready(state)

    assert next_state.next_check_time_ms == state.next_check_time_ms + 15_000
    assert next_state.retry_count == 1


def test_on_bar_ready_resets_retry_and_sets_next_bar() -> None:
    scheduler = BarCloseScheduler(
        SchedulerConfig(
            first_check_delay_ms=15_000,
            retry_delay_ms=15_000,
            max_retries_after_close=8,
        )
    )

    state = scheduler.bootstrap(
        symbol="BTCUSDT",
        tf="15m",
        last_closed_time_ms=1_000_000,
    )
    state = scheduler.on_not_ready(state)

    new_last_close = 2_000_000
    ready_state = scheduler.on_bar_ready(
        state,
        new_last_closed_time_ms=new_last_close,
    )

    assert ready_state.retry_count == 0
    assert ready_state.last_observed_close_time_ms == new_last_close
    assert ready_state.expected_close_time_ms == new_last_close + 15 * 60_000
    assert ready_state.next_check_time_ms == ready_state.expected_close_time_ms + 15_000