from __future__ import annotations

from meowbot.core.services.execution.exit.exit_profile import get_exit_profile


def test_v1_exit_profile_keeps_half_percent_tp_step_and_no_soft_stop() -> None:
    profile = get_exit_profile("v1")

    assert profile.strategy_version == "v1"
    assert profile.tp_step_pct == 0.5
    assert profile.tp_levels == (0.005, 0.010, 0.015, 0.020)
    assert profile.tp_distribution_mode == "equal"
    assert profile.soft_stop_enabled is False


def test_v2_exit_profile_uses_signal_level_tp_step_and_soft_stop() -> None:
    profile = get_exit_profile("v2", signal_level="medium")

    assert profile.strategy_version == "v2"
    assert profile.tp_step_pct == 0.8
    assert profile.tp_levels == (0.008, 0.016, 0.024, 0.032)
    assert profile.tp_distribution_mode == "equal"
    assert profile.soft_stop_enabled is True
    assert profile.soft_stop_activation_pct == 0.8
    assert profile.soft_stop_activation_trigger == "tp1_hit"
    assert profile.soft_stop_start_pct == 0.1
    assert profile.soft_stop_increment_pct == 0.05
    assert profile.soft_stop_hourly_increment_pct == 0.05
    assert profile.soft_stop_increment_interval_seconds == 900


def test_v2_exit_profile_uses_signal_level_activation_thresholds() -> None:
    assert get_exit_profile("v2", signal_level="weak").soft_stop_activation_pct == 0.6
    assert get_exit_profile("v2", signal_level="medium").soft_stop_activation_pct == 0.8
    assert get_exit_profile("v2", signal_level="strong").soft_stop_activation_pct == 1.0
    assert get_exit_profile("v2", signal_level="weak").tp_step_pct == 0.6
    assert get_exit_profile("v2", signal_level="medium").tp_step_pct == 0.8
    assert get_exit_profile("v2", signal_level="strong").tp_step_pct == 1.0


def test_v2_exit_profile_defaults_missing_level_to_medium() -> None:
    assert get_exit_profile("v2").soft_stop_activation_pct == 0.8


def test_unknown_exit_profile_defaults_to_v1() -> None:
    assert get_exit_profile("bad").strategy_version == "v1"
