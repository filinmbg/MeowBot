from __future__ import annotations

from meowbot.core.services.notifications.trade_event_message_builder import (
    TradeEventMessageBuilder,
    format_admin_signal_debug_block,
)


def test_build_opened_message_shows_dynamic_tp_plan() -> None:
    builder = TradeEventMessageBuilder()

    text = builder.build(
        event_type="OPENED",
        symbol="ETHUSDT",
        mode="live",
        lang="en",
        payload={
            "side": "LONG",
            "tf_entry": "15m",
            "entry_price": 100.0,
            "sl_price": 98.0,
            "stake_usd": 10,
            "leverage": 5,
            "qty": 1,
            "tp_count": 2,
            "tp_plan": [
                {"stage": 1, "price": 100.5, "close_fraction": 0.5},
                {"stage": 2, "price": 101.0, "close_fraction": 0.5},
            ],
        },
    )

    assert "<b>TP:</b> 2" in text
    assert "<b>TP1:</b> 100.5000 (50%)" in text
    assert "<b>TP2:</b> 101.0000 (50%)" in text


def test_build_opened_message_shows_small_margin_tp_reason() -> None:
    builder = TradeEventMessageBuilder()

    text = builder.build(
        event_type="OPENED",
        symbol="ETHUSDT",
        mode="live",
        lang="en",
        payload={
            "side": "LONG",
            "tf_entry": "15m",
            "entry_price": 100.0,
            "sl_price": 98.0,
            "stake_usd": 0.8,
            "leverage": 5,
            "qty": 0.04,
            "tp_count": 1,
            "tp_count_forced_by_small_margin": True,
            "entry_margin_usdt": 0.8,
            "tp_plan": [
                {"stage": 1, "price": 101.0, "close_fraction": 1.0},
            ],
        },
    )

    assert "<b>TP:</b> 1" in text
    assert "Reason: small entry margin &lt; 1 USDT (0.80 USDT)" in text
    assert "<b>TP1:</b> 101.0000 (100%)" in text


def test_build_opened_message_shows_leverage_adjustment_warning() -> None:
    builder = TradeEventMessageBuilder()

    text = builder.build(
        event_type="OPENED",
        symbol="BTCUSDT",
        mode="live",
        lang="uk",
        payload={
            "side": "LONG",
            "tf_entry": "15m",
            "entry_price": 100.0,
            "sl_price": 98.0,
            "stake_usd": 1,
            "default_leverage": 20,
            "leverage": 10,
            "requested_leverage": 20,
            "symbol_max_leverage": 10,
            "final_leverage": 10,
            "leverage_adjusted": True,
            "qty": 0.1,
            "tp_count": 1,
            "tp_plan": [{"stage": 1, "price": 101.0, "close_fraction": 1.0}],
        },
    )

    assert "<b>Плече:</b> x10" in text
    assert "Плече скориговано" in text
    assert "Було: x20" in text
    assert "Макс: x10" in text
    assert "Використано: x10" in text


def test_regular_opened_message_does_not_include_admin_strategy_debug_block() -> None:
    builder = TradeEventMessageBuilder()

    text = builder.build(
        event_type="OPENED",
        symbol="ZROUSDT",
        mode="live",
        lang="uk",
        payload={
            "side": "LONG",
            "tf_entry": "15m",
            "entry_price": 1.0,
            "sl_price": 0.98,
            "stake_usd": 10,
            "leverage": 5,
            "qty": 10,
            "rule_id": "LONG_BREAKOUT_V18",
            "strategy_version": "v2",
            "signal_debug": {"rsi_14": 61.2},
        },
    )

    assert "Правило" not in text
    assert "Сигнал" not in text
    assert "LONG_BREAKOUT_V18" not in text


def test_admin_opened_message_includes_v2_strategy_debug_block() -> None:
    builder = TradeEventMessageBuilder()

    text = builder.build(
        event_type="OPENED",
        symbol="ZROUSDT",
        mode="live",
        lang="uk",
        is_admin=True,
        payload={
            "side": "LONG",
            "tf_entry": "15m",
            "entry_price": 1.0,
            "sl_price": 0.98,
            "stake_usd": 10,
            "leverage": 5,
            "qty": 10,
            "rule_id": "LONG_BREAKOUT_V18",
            "strategy_version": "v2",
            "signal_level": "strong",
            "signal_score": 8,
            "position_size_multiplier": 1.5,
            "soft_stop_activation_pct": 1.0,
            "soft_stop_activation_trigger": "tp1_hit",
            "soft_stop_start_pct": 0.1,
            "soft_stop_increment_pct": 0.05,
            "soft_stop_increment_interval_seconds": 900,
            "signal_debug": {
                "rsi_14": 61.24,
                "atr_14_pct": 0.021,
                "dist_to_ema_50_pct": 1.05,
                "close_price": 105.0,
                "ema_50": 100.0,
                "volume_ratio_sma_20": 1.6,
                "adx_14": 24.5,
                "close_position_in_candle": 0.77,
                "vol_peak_offset_10": 2,
            },
        },
    )

    assert "Правило" in text
    assert "Сигнал" in text
    assert "Тригер" in text
    assert "LONG_BREAKOUT_V18" in text
    assert "V2" in text
    assert "strong" in text
    assert "x1.5" in text
    assert "Score" in text
    assert "8" in text
    assert "TP1" in text
    assert "+1%" in text
    assert "+0.1%" in text
    assert "+0.05%" in text
    assert "RSI: 61.2" in text
    assert "ATR: 2.10%" in text
    assert "EMA50 dist: 5.00%" in text
    assert "Volume ratio: 160.00%" in text
    assert "ADX: 24.5" in text
    assert "Close pos: 0.77" in text
    assert "Vol peak: 2" in text
    assert "+ RSI &gt;= 60" in text
    assert "+ Strong volume spike" in text
    assert "+ Trend confirmed" in text
    assert "- EMA distance overextended" in text
    assert "+ ATR volatility OK" in text
    assert "+ Strong candle close" in text
    assert "- ADX weak" in text


def test_admin_signal_debug_block_handles_missing_fields() -> None:
    text = format_admin_signal_debug_block(
        {
            "rule_id": "RSI_REBOUND_ST_124",
            "strategy_version": "v1",
            "entry_indicators": {"features": {"rsi14": 44.4}},
        },
        is_admin=True,
    )

    assert "RSI_REBOUND_ST_124" in text
    assert "V1" in text
    assert "RSI: 44.4" in text
    assert "ADX: N/A" in text
    assert "- RSI rebound detected" in text


def test_admin_signal_strength_marks_strong_v2_signal() -> None:
    text = format_admin_signal_debug_block(
        {
            "rule_id": "LONG_BREAKOUT_V18",
            "strategy_version": "v2",
            "signal_level": "strong",
            "signal_score": 9,
            "position_size_multiplier": 1.5,
            "signal_debug": {
                "rsi_14": 62.0,
                "volume_ratio_sma_20": 1.6,
                "close_price": 103.0,
                "ema_50": 100.0,
                "atr_14_pct": 0.011,
                "close_position_in_candle": 0.82,
                "adx_14": 30.0,
            },
        },
        is_admin=True,
    )

    assert "strong" in text
    assert "Score" in text
    assert "9" in text
    assert "+ Strong volume spike" in text


def test_admin_signal_strength_marks_weak_v2_signal() -> None:
    text = format_admin_signal_debug_block(
        {
            "rule_id": "LONG_BREAKOUT_V18",
            "strategy_version": "v2",
            "signal_level": "weak",
            "signal_score": 0,
            "position_size_multiplier": 1.0,
            "signal_debug": {
                "rsi_14": 45.0,
                "volume_ratio_sma_20": 1.0,
                "close_price": 95.0,
                "ema_50": 100.0,
                "atr_14_pct": 0.002,
                "close_position_in_candle": 0.4,
                "adx_14": 10.0,
            },
        },
        is_admin=True,
    )

    assert "weak" in text
    assert "Score" in text
    assert "0" in text
    assert "- Weak candle close" in text


def test_build_opened_message_uses_equal_split_fallback_fractions() -> None:
    builder = TradeEventMessageBuilder()

    text = builder.build(
        event_type="OPENED",
        symbol="ETHUSDT",
        mode="live",
        lang="en",
        payload={
            "side": "LONG",
            "tf_entry": "15m",
            "entry_price": 100.0,
            "sl_price": 98.0,
            "stake_usd": 10,
            "leverage": 5,
            "qty": 1,
            "tp_count": 3,
            "tp_levels": [0.005, 0.010, 0.015],
        },
    )

    assert "<b>TP:</b> 3" in text
    assert "<b>TP1:</b> 100.5000 (33.33%)" in text
    assert "<b>TP2:</b> 101.0000 (33.33%)" in text
    assert "<b>TP3:</b> 101.5000 (33.33%)" in text


def test_build_opened_message_shows_v2_soft_stop_rule() -> None:
    builder = TradeEventMessageBuilder()

    text = builder.build(
        event_type="OPENED",
        symbol="ETHUSDT",
        mode="live",
        lang="en",
        payload={
            "side": "LONG",
            "tf_entry": "15m",
            "entry_price": 100.0,
            "sl_price": 98.0,
            "stake_usd": 10,
            "leverage": 5,
            "qty": 1,
            "tp_step_pct": 0.6,
            "soft_stop_enabled": True,
            "soft_stop_activation_pct": 0.6,
            "soft_stop_activation_trigger": "tp1_hit",
            "soft_stop_start_pct": 0.1,
            "soft_stop_increment_pct": 0.05,
            "soft_stop_increment_interval_seconds": 900,
            "tp_count": 1,
            "tp_levels": [0.006],
        },
    )

    assert "<b>TP step:</b> 0.6%" in text
    assert "Activation: after TP1 (+0.6%); initial soft stop +0.1%, then +0.05% every 15m" in text
    assert "Exchange SL" in text


def test_build_soft_stop_activation_and_close_messages() -> None:
    builder = TradeEventMessageBuilder()

    activated = builder.build(
        event_type="SOFT_STOP_ACTIVATED",
        symbol="ETHUSDT",
        mode="live",
        lang="en",
        payload={
            "soft_stop_current_pct": 0.1,
            "soft_stop_trigger_price": 100.1,
            "current_profit_pct": 0.81,
        },
    )
    closed = builder.build(
        event_type="CLOSED",
        symbol="ETHUSDT",
        mode="live",
        lang="en",
        payload={"reason": "SOFT_TRAILING_STOP", "realized_pnl_usd": 0.31},
    )

    assert "V2 soft stop activated" in activated
    assert "<b>Soft stop:</b> 0.1%" in activated
    assert "Trade closed by V2 soft stop" in closed


def test_build_tp_hit_message_for_partial_dynamic_tp() -> None:
    builder = TradeEventMessageBuilder()

    text = builder.build(
        event_type="TP_HIT",
        symbol="ETHUSDT",
        mode="live",
        lang="en",
        payload={
            "tp_index": 1,
            "tp_count": 2,
            "tp_close_fraction": 0.5,
            "remaining_pct": 0.5,
            "realized_pnl_usd": 0.25,
            "trade_closed": False,
        },
    )

    assert "✅ <b>TP1 / 2 filled</b>" in text
    assert "<b>Closed:</b> 50%" in text
    assert "<b>Remaining:</b> 50%" in text
    assert "Trade remains open" in text


def test_build_tp_hit_message_does_not_infer_close_from_zero_local_remaining() -> None:
    builder = TradeEventMessageBuilder()

    text = builder.build(
        event_type="TP_HIT",
        symbol="COMPUSDT",
        mode="live",
        lang="en",
        payload={
            "tp_index": 3,
            "tp_count": 4,
            "tp_close_fraction": 0.25,
            "remaining_pct": 0.0,
            "exchange_remaining_pct": 0.25,
            "exchange_position_amt_after_close": 0.024,
            "qty": 0.24,
            "realized_pnl_usd": 0.08,
            "trade_closed": False,
        },
    )

    assert "TP3 / 4 filled" in text
    assert "<b>Closed:</b> 25%" in text
    assert "<b>Remaining:</b> 25%" in text
    assert "Trade remains open" in text
    assert "Trade fully closed" not in text


def test_build_tp_close_message_for_single_tp_full_close() -> None:
    builder = TradeEventMessageBuilder()

    text = builder.build(
        event_type="CLOSED",
        symbol="ETHUSDT",
        mode="live",
        lang="en",
        payload={
            "reason": "TP1_HIT",
            "tp_index": 1,
            "tp_count": 1,
            "tp_close_fraction": 1.0,
            "remaining_pct": 0.0,
            "realized_pnl_usd": 0.42,
            "trade_closed": True,
        },
    )

    assert "✅ <b>TP1 / 1 filled</b>" in text
    assert "<b>Closed:</b> 100%" in text
    assert "<b>Remaining:</b> 0%" in text
    assert "Trade fully closed" in text


def test_build_opened_message_uk_contains_localized_labels_and_emoji() -> None:
    builder = TradeEventMessageBuilder()

    text = builder.build(
        event_type="OPENED",
        symbol="INJUSDT",
        mode="live",
        lang="uk",
        payload={
            "side": "LONG",
            "tf_entry": "1h",
            "entry_price": 2.9580,
            "sl_price": 2.8988,
            "stake_usd": 10.23,
            "default_leverage": 5,
            "qty": 17.28448157,
        },
    )

    assert "🟢 <b>LONG відкрито</b>" in text
    assert "<b>Вхід:</b> 2.9580" in text
    assert "<b>Ставка:</b> 10.23 USDT" in text
    assert "<b>Плече:</b> x5" in text


def test_build_closed_message_ru_contains_localized_labels_and_emoji() -> None:
    builder = TradeEventMessageBuilder()

    text = builder.build(
        event_type="CLOSED",
        symbol="UNIUSDT",
        mode="live",
        lang="ru",
        payload={"realized_pnl_usd": 0.18},
    )

    assert "✅ <b>Сделка закрыта</b>" in text
    assert "<b>Реализовано:</b> 🟢 +0.18 USDT" in text


def test_build_closed_message_uk_marks_negative_pnl_with_red_minus() -> None:
    builder = TradeEventMessageBuilder()

    text = builder.build(
        event_type="CLOSED",
        symbol="ENJUSDT",
        mode="sandbox",
        lang="uk",
        payload={"realized_pnl_usd": -0.40},
    )

    assert "✅ <b>Угоду закрито</b>" in text
    assert "<b>Реалізовано:</b> 🔴 −0.40 USDT" in text


def test_build_falls_back_to_english_for_unknown_language() -> None:
    builder = TradeEventMessageBuilder()

    text = builder.build(
        event_type="STOP",
        symbol="BTCUSDT",
        mode="live",
        lang="de",
        payload={"realized_pnl_usd": -1.25},
    )

    assert "🛑 <b>Trade closed by Stop Loss</b>" in text
    assert "<b>Result:</b> 🔴 −1.25 USDT" in text
