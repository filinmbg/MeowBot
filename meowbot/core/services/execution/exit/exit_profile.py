from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


MIN_EXIT_TP_LEVELS = 1
MAX_EXIT_TP_LEVELS = 4


@dataclass(frozen=True)
class ExitProfile:
    strategy_version: str
    tp_step_pct: float
    max_tp_count: int
    tp_distribution_mode: str
    initial_sl_pct: float
    soft_stop_enabled: bool
    soft_stop_activation_pct: float
    soft_stop_start_pct: float
    soft_stop_increment_pct: float
    soft_stop_hourly_increment_pct: float
    soft_stop_increment_interval_seconds: int
    soft_stop_activation_trigger: str = "price"

    @property
    def tp_levels(self) -> tuple[float, ...]:
        step = max(float(self.tp_step_pct), 0.0) / 100.0
        count = max(MIN_EXIT_TP_LEVELS, min(MAX_EXIT_TP_LEVELS, int(self.max_tp_count)))
        return tuple(step * index for index in range(1, count + 1))


def normalize_strategy_version(strategy_version: str | None) -> str:
    value = str(strategy_version or "v1").strip().lower()
    return value if value in {"v1", "v2"} else "v1"


def normalize_signal_level(signal_level: str | None) -> str:
    value = str(signal_level or "medium").strip().lower()
    return value if value in {"weak", "medium", "strong"} else "medium"


def get_exit_profile(strategy_version: str | None, *, signal_level: str | None = None) -> ExitProfile:
    normalized = normalize_strategy_version(strategy_version)
    if normalized == "v2":
        level = normalize_signal_level(signal_level)
        activation_pct = {
            "weak": 0.6,
            "medium": 0.8,
            "strong": 1.0,
        }[level]
        return ExitProfile(
            strategy_version="v2",
            tp_step_pct=activation_pct,
            max_tp_count=MAX_EXIT_TP_LEVELS,
            tp_distribution_mode="equal",
            initial_sl_pct=2.0,
            soft_stop_enabled=True,
            soft_stop_activation_pct=activation_pct,
            soft_stop_start_pct=0.1,
            soft_stop_increment_pct=0.05,
            soft_stop_hourly_increment_pct=0.05,
            soft_stop_increment_interval_seconds=900,
            soft_stop_activation_trigger="tp1_hit",
        )
    return ExitProfile(
        strategy_version="v1",
        tp_step_pct=0.5,
        max_tp_count=MAX_EXIT_TP_LEVELS,
        tp_distribution_mode="equal",
        initial_sl_pct=2.0,
        soft_stop_enabled=False,
        soft_stop_activation_pct=0.0,
        soft_stop_start_pct=0.0,
        soft_stop_increment_pct=0.0,
        soft_stop_hourly_increment_pct=0.0,
        soft_stop_increment_interval_seconds=3600,
    )


def exit_profile_to_dict(profile: ExitProfile) -> dict[str, Any]:
    payload = asdict(profile)
    payload["tp_levels"] = list(profile.tp_levels)
    return payload
