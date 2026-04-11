from __future__ import annotations

from dataclasses import dataclass


TF_TO_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "2h": 2 * 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


@dataclass(frozen=True)
class SchedulerConfig:
    first_check_delay_ms: int = 15_000
    retry_delay_ms: int = 15_000
    max_retries_after_close: int = 8

    def timeframe_ms(self, tf: str) -> int:
        try:
            return TF_TO_MS[tf]
        except KeyError as exc:
            raise ValueError(f"Unsupported timeframe: {tf}") from exc


@dataclass
class ScheduleState:
    symbol: str
    tf: str

    expected_close_time_ms: int
    next_check_time_ms: int

    retry_count: int = 0
    last_observed_close_time_ms: int | None = None


class BarCloseScheduler:
    def __init__(self, config: SchedulerConfig):
        self.config = config

    def bootstrap(
        self,
        *,
        symbol: str,
        tf: str,
        last_closed_time_ms: int,
    ) -> ScheduleState:
        next_expected_close = last_closed_time_ms + self.config.timeframe_ms(tf)

        return ScheduleState(
            symbol=symbol,
            tf=tf,
            expected_close_time_ms=next_expected_close,
            next_check_time_ms=next_expected_close + self.config.first_check_delay_ms,
            retry_count=0,
            last_observed_close_time_ms=last_closed_time_ms,
        )

    def should_run(self, state: ScheduleState, now_ms: int) -> bool:
        return now_ms >= state.next_check_time_ms

    def on_not_ready(self, state: ScheduleState) -> ScheduleState:
        retry_count = min(
            state.retry_count + 1,
            self.config.max_retries_after_close,
        )

        return ScheduleState(
            symbol=state.symbol,
            tf=state.tf,
            expected_close_time_ms=state.expected_close_time_ms,
            next_check_time_ms=state.next_check_time_ms + self.config.retry_delay_ms,
            retry_count=retry_count,
            last_observed_close_time_ms=state.last_observed_close_time_ms,
        )

    def on_bar_ready(
        self,
        state: ScheduleState,
        *,
        new_last_closed_time_ms: int,
    ) -> ScheduleState:
        next_expected_close = new_last_closed_time_ms + self.config.timeframe_ms(state.tf)

        return ScheduleState(
            symbol=state.symbol,
            tf=state.tf,
            expected_close_time_ms=next_expected_close,
            next_check_time_ms=next_expected_close + self.config.first_check_delay_ms,
            retry_count=0,
            last_observed_close_time_ms=new_last_closed_time_ms,
        )