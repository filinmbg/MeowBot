from __future__ import annotations

from html import escape


ADMIN_SIGNAL_FIELDS = (
    ("RSI", ("rsi_14", "rsi14"), "number_1"),
    ("ATR", ("atr_14_pct", "atr14_pct"), "percent_2_ratio"),
    ("EMA50 dist", ("dist_to_ema_50_pct",), "ema_distance_pct"),
    ("Volume ratio", ("volume_ratio_sma_20", "relative_volume20"), "percent_2_ratio"),
    ("ADX", ("adx_14", "adx14"), "number_1"),
    ("Close pos", ("close_position_in_candle",), "number_2"),
    ("Vol peak", ("vol_peak_offset_10",), "number_0"),
)


def format_admin_signal_debug_block(trade, *, is_admin: bool = False) -> str:
    if not is_admin:
        return ""

    payload = _as_mapping(trade)
    entry_indicators = _as_mapping(_value_from(payload, ("entry_indicators",)))
    signal_debug = _as_mapping(_value_from(payload, ("signal_debug",)))
    rule_id = _first_value(payload, entry_indicators, signal_debug, aliases=("rule_id", "model_id"))
    strategy_version = _first_value(payload, entry_indicators, signal_debug, aliases=("strategy_version",))
    signal_level = _first_value(payload, entry_indicators, signal_debug, aliases=("signal_level",))
    signal_score = _first_value(payload, entry_indicators, signal_debug, aliases=("signal_score",))
    multiplier = _first_value(payload, entry_indicators, signal_debug, aliases=("position_size_multiplier",))
    soft_stop_activation_pct = _first_value(
        payload,
        entry_indicators,
        signal_debug,
        aliases=("soft_stop_activation_pct",),
    )
    soft_stop_start_pct = _first_value(
        payload,
        entry_indicators,
        signal_debug,
        aliases=("soft_stop_start_pct",),
    )
    soft_stop_increment_pct = _first_value(
        payload,
        entry_indicators,
        signal_debug,
        aliases=("soft_stop_increment_pct", "soft_stop_hourly_increment_pct"),
    )
    soft_stop_interval_seconds = _first_value(
        payload,
        entry_indicators,
        signal_debug,
        aliases=("soft_stop_increment_interval_seconds",),
    )
    soft_stop_activation_trigger = _first_value(
        payload,
        entry_indicators,
        signal_debug,
        _as_mapping(_first_value(payload, entry_indicators, signal_debug, aliases=("exit_profile",))),
        aliases=("soft_stop_activation_trigger",),
    )
    if soft_stop_activation_trigger is None and str(strategy_version or "").lower() == "v2":
        soft_stop_activation_trigger = "tp1_hit"

    signal_strength = _score_signal_strength(signal_debug, entry_indicators, payload, rule_id=rule_id)
    lines = [
        "",
        "",
        f"\u2699\ufe0f <b>\u041f\u0440\u0430\u0432\u0438\u043b\u043e:</b> {escape(str(rule_id or 'N/A'))}",
        f"\U0001F4CA <b>\u0412\u0435\u0440\u0441\u0456\u044f:</b> {escape(str(strategy_version or 'v1').upper())}",
    ]
    if signal_level is not None or signal_strength is not None:
        level_text = str(signal_level or signal_strength.get("label") or "N/A")
        lines.append(f"\U0001F4C8 <b>\u0420\u0456\u0432\u0435\u043d\u044c \u0441\u0438\u0433\u043d\u0430\u043b\u0443:</b> {escape(level_text)}")
    if multiplier is not None:
        lines.append(f"\U0001F3AF <b>\u041c\u043d\u043e\u0436\u043d\u0438\u043a \u043f\u043e\u0437\u0438\u0446\u0456\u0457:</b> x{_format_multiplier(multiplier)}")
    if signal_score is not None:
        lines.append(f"\U0001F9EE <b>Score:</b> {escape(str(signal_score))}")
    if str(strategy_version or "").lower() == "v2":
        lines.extend(
            [
                "",
                "\U0001F4CC <b>Soft stop:</b>",
                (
                    f"\u0410\u043a\u0442\u0438\u0432\u0430\u0446\u0456\u044f: \u043f\u0456\u0441\u043b\u044f TP1 (+{_format_percent_number(soft_stop_activation_pct)})"
                    if str(soft_stop_activation_trigger or "").lower() == "tp1_hit" and soft_stop_activation_pct is not None
                    else f"\u0410\u043a\u0442\u0438\u0432\u0430\u0446\u0456\u044f: +{_format_percent_number(soft_stop_activation_pct)}"
                    if soft_stop_activation_pct is not None
                    else "\u0410\u043a\u0442\u0438\u0432\u0430\u0446\u0456\u044f: N/A"
                ),
                (
                    f"\u041f\u043e\u0447\u0430\u0442\u043a\u043e\u0432\u0438\u0439 soft stop: +{_format_percent_number(soft_stop_start_pct)}"
                    if soft_stop_start_pct is not None
                    else "\u041f\u043e\u0447\u0430\u0442\u043a\u043e\u0432\u0438\u0439 soft stop: N/A"
                ),
                (
                    f"\u041f\u0456\u0441\u043b\u044f \u0430\u043a\u0442\u0438\u0432\u0430\u0446\u0456\u0457: +{_format_percent_number(soft_stop_increment_pct)} \u043a\u043e\u0436\u043d\u0456 {_format_interval(soft_stop_interval_seconds)}"
                    if soft_stop_increment_pct is not None
                    else "\u041f\u0456\u0441\u043b\u044f \u0430\u043a\u0442\u0438\u0432\u0430\u0446\u0456\u0457: N/A"
                ),
            ]
        )
    lines.extend(["", "\U0001F4C8 <b>\u0421\u0438\u0433\u043d\u0430\u043b:</b>"])
    for label, aliases, formatter in ADMIN_SIGNAL_FIELDS:
        value = _ema_distance_pct(signal_debug, entry_indicators, payload) if formatter == "ema_distance_pct" else None
        if value is None:
            value = _first_value(signal_debug, payload, entry_indicators, aliases=aliases)
        if value is None:
            value = _first_value(_features_from(payload, entry_indicators), aliases=aliases)
        formatted = _format_debug_field(value, formatter)
        lines.append(f"{label}: {formatted}")
    trigger_lines = _format_trigger_explanation(
        payload,
        entry_indicators,
        signal_debug,
        rule_id=rule_id,
        signal_strength=signal_strength,
    )
    if trigger_lines:
        lines.extend(["", "\U0001F4CC <b>Тригер:</b>", *trigger_lines])
    for index, line in enumerate(lines):
        if line.startswith("\u2699\ufe0f <b>"):
            lines[index] = f"\u2699\ufe0f <b>\u041f\u0440\u0430\u0432\u0438\u043b\u043e:</b> {escape(str(rule_id or 'N/A'))}"
        elif line.startswith("\U0001F4CA <b>"):
            lines[index] = f"\U0001F4CA <b>\u0412\u0435\u0440\u0441\u0456\u044f:</b> {escape(str(strategy_version or 'v1').upper())}"
        elif line.startswith("\U0001F4CC <b>") and "Soft stop" not in line:
            lines[index] = "\U0001F4CC <b>\u0422\u0440\u0438\u0433\u0435\u0440:</b>"
    return "\n".join(lines)


def format_admin_close_debug_block(trade, *, is_admin: bool = False) -> str:
    if not is_admin:
        return ""

    payload = _as_mapping(trade)
    entry_indicators = _as_mapping(_value_from(payload, ("entry_indicators",)))
    rule_id = _first_value(payload, entry_indicators, aliases=("rule_id", "model_id"))
    strategy_version = _first_value(payload, entry_indicators, aliases=("strategy_version",))
    close_reason = _first_value(payload, aliases=("reason", "close_reason", "close_reason_code", "exit_reason"))
    pnl_source = _first_value(payload, aliases=("pnl_source",))
    return (
        f"\n\n\u2699\ufe0f <b>\u041f\u0440\u0430\u0432\u0438\u043b\u043e:</b> {escape(str(rule_id or 'N/A'))}"
        f"\n\U0001F4CA <b>\u0412\u0435\u0440\u0441\u0456\u044f:</b> {escape(str(strategy_version or 'v1').upper())}"
        f"\n\U0001F4CC <b>\u0417\u0430\u043a\u0440\u0438\u0442\u0442\u044f:</b> {escape(str(close_reason or 'N/A'))}"
        f"\nPnL source: {escape(str(pnl_source or 'N/A'))}"
    )


def _as_mapping(value) -> dict:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return {
        key: getattr(value, key)
        for key in dir(value)
        if not key.startswith("_") and not callable(getattr(value, key, None))
    }


def _features_from(*sources: dict) -> dict:
    for source in sources:
        features = _value_from(source, ("features",))
        if isinstance(features, dict):
            return features
    return {}


def _first_value(*sources: dict, aliases: tuple[str, ...]):
    for source in sources:
        value = _value_from(source, aliases)
        if value is not None:
            return value
    return None


def _value_from(source: dict, aliases: tuple[str, ...]):
    for alias in aliases:
        if alias in source and source.get(alias) is not None:
            return source.get(alias)
    return None


def _format_debug_value(value) -> str:
    if value is None:
        return "N/A"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return escape(str(value))
    return escape(f"{numeric:.4f}".rstrip("0").rstrip("."))


def _format_debug_percent(value) -> str:
    if value is None:
        return "N/A"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return escape(str(value))
    if abs(numeric) <= 1.0:
        numeric *= 100.0
    return escape(f"{numeric:.2f}".rstrip("0").rstrip(".") + "%")


def _format_debug_field(value, formatter: str) -> str:
    if value is None:
        return "N/A"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return escape(str(value))

    if formatter == "number_0":
        return escape(f"{numeric:.0f}")
    if formatter == "number_1":
        return escape(f"{numeric:.1f}")
    if formatter == "number_2":
        return escape(f"{numeric:.2f}")
    if formatter == "percent_2_plain":
        return escape(f"{numeric:.2f}%")
    if formatter in {"percent_2_auto", "ema_distance_pct"}:
        if abs(numeric) <= 1.0:
            numeric *= 100.0
        return escape(f"{numeric:.2f}%")
    if formatter == "percent_2_ratio":
        return escape(f"{numeric * 100.0:.2f}%")
    return _format_debug_value(value)


def _format_multiplier(value) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return escape(str(value or "N/A"))
    return escape(f"{numeric:.2f}".rstrip("0").rstrip("."))


def _format_percent_number(value) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "N/A"
    return escape(f"{numeric:.2f}".rstrip("0").rstrip(".") + "%")


def _format_interval(value) -> str:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return "N/A"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return escape(f"{hours} \u0433\u043e\u0434" if hours == 1 else f"{hours} \u0433\u043e\u0434")
    if seconds % 60 == 0:
        return escape(f"{seconds // 60} \u0445\u0432")
    return escape(f"{seconds} \u0441")


def _ema_distance_pct(*sources: dict) -> float | None:
    features = _features_from(*sources)
    bar = _as_mapping(_first_value(*sources, aliases=("bar",)))
    prices = _as_mapping(_first_value(*sources, aliases=("prices",)))
    price = _first_value(
        *sources,
        features,
        bar,
        prices,
        aliases=("close_price", "entry_price", "price", "close", "signal_entry_price", "live_entry_price"),
    )
    ema50 = _first_value(*sources, features, aliases=("ema_50", "ema50"))
    try:
        price_value = float(price)
        ema50_value = float(ema50)
    except (TypeError, ValueError):
        return None
    if ema50_value == 0.0:
        return None
    return (price_value - ema50_value) / ema50_value


def _score_signal_strength(*sources: dict, rule_id) -> dict | None:
    rule = str(rule_id or "").upper()
    if rule != "LONG_BREAKOUT_V18":
        return None

    features = _features_from(*sources)
    rsi = _number_from_sources(*sources, features, aliases=("rsi_14", "rsi14"))
    volume_ratio = _number_from_sources(*sources, features, aliases=("volume_ratio_sma_20", "relative_volume20"))
    ema_distance = _ema_distance_pct(*sources)
    atr_pct = _percent_value(_first_value(*sources, features, aliases=("atr_14_pct", "atr14_pct")))
    close_position = _number_from_sources(*sources, features, aliases=("close_position_in_candle",))
    adx = _number_from_sources(*sources, features, aliases=("adx_14", "adx14"))

    score = 0
    explanations: list[str] = []

    if rsi is None:
        explanations.append("- RSI N/A")
    elif rsi >= 60.0:
        score += 2
        explanations.append("+ RSI >= 60")
    elif rsi >= 50.0:
        score += 1
        explanations.append("+ RSI > 50")
    else:
        explanations.append("- RSI below 50")

    if volume_ratio is None:
        explanations.append("- Volume ratio N/A")
    elif volume_ratio >= 1.5:
        score += 2
        explanations.append("+ Strong volume spike")
    elif volume_ratio >= 1.2:
        score += 1
        explanations.append("+ Volume spike")
    else:
        explanations.append("- Volume below spike threshold")

    if ema_distance is None:
        explanations.append("- EMA50 distance N/A")
    elif ema_distance > 0.0:
        score += 1
        explanations.append("+ Trend confirmed")
    else:
        explanations.append("- Price below EMA50")

    if ema_distance is not None and 0.0 < ema_distance < 0.05:
        score += 1
        explanations.append("+ EMA distance healthy")
    elif ema_distance is not None:
        explanations.append("- EMA distance overextended")

    if atr_pct is None:
        explanations.append("- ATR N/A")
    elif atr_pct >= 0.8:
        score += 1
        explanations.append("+ ATR volatility OK")
    else:
        explanations.append("- ATR too low")

    if close_position is None:
        explanations.append("- Candle close N/A")
    elif close_position >= 0.7:
        score += 1
        explanations.append("+ Strong candle close")
    else:
        explanations.append("- Weak candle close")

    if adx is None:
        explanations.append("- ADX N/A")
    elif adx >= 25.0:
        score += 1
        explanations.append("+ ADX trend strength OK")
    else:
        explanations.append("- ADX weak")

    if score >= 7:
        emoji = "\U0001F7E2"
        label = "\u0441\u0438\u043b\u044c\u043d\u0438\u0439"
    elif score >= 4:
        emoji = "\U0001F7E1"
        label = "\u0441\u0435\u0440\u0435\u0434\u043d\u0456\u0439"
    else:
        emoji = "\U0001F534"
        label = "\u0441\u043b\u0430\u0431\u043a\u0438\u0439"

    return {
        "score": score,
        "max_score": 9,
        "emoji": emoji,
        "label": label,
        "explanations": explanations,
    }


def _number_from_sources(*sources: dict, aliases: tuple[str, ...]) -> float | None:
    value = _first_value(*sources, aliases=aliases)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent_value(value) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if abs(numeric) <= 1.0:
        numeric *= 100.0
    return numeric


def _format_trigger_explanation(*sources: dict, rule_id, signal_strength: dict | None = None) -> list[str]:
    raw = _first_value(
        *sources,
        aliases=(
            "trigger_explanation",
            "trigger_reasons",
            "signal_triggers",
            "trigger",
        ),
    )
    if raw is None and signal_strength is not None:
        raw = signal_strength.get("explanations")
    if raw is None:
        raw = _default_trigger_explanation(rule_id)
    if isinstance(raw, str):
        items = [part.strip() for part in raw.replace(";", "\n").splitlines() if part.strip()]
    elif isinstance(raw, (list, tuple)):
        items = [str(item).strip() for item in raw if str(item).strip()]
    else:
        items = []
    return [_format_trigger_line(item) for item in items[:8]]


def _format_trigger_line(item: str) -> str:
    text = str(item).strip()
    if text.startswith(("+", "-")):
        return escape(text)
    return f"- {escape(text)}"


def _default_trigger_explanation(rule_id) -> list[str]:
    rule = str(rule_id or "").upper()
    if rule == "LONG_BREAKOUT_V18":
        return [
            "RSI crossed threshold",
            "Volume spike",
            "Trend confirmed",
        ]
    if rule.startswith("RSI_REBOUND"):
        return [
            "RSI rebound detected",
            "Supertrend bullish",
            "Entry price accepted",
        ]
    return ["Signal conditions passed"]


class TradeEventMessageBuilder:
    def build(
        self,
        *,
        event_type: str,
        symbol: str,
        mode: str,
        payload: dict | None = None,
        lang: str = "uk",
        user_id: str | None = None,
        is_admin: bool = False,
    ) -> str:
        payload = payload or {}
        labels = self._labels(lang)
        safe_symbol = escape(str(symbol or "-"))
        admin_live = bool(is_admin) and str(mode or "").lower() == "live"

        if event_type == "OPENED":
            side = self._side(payload)
            tf = escape(str(payload.get("tf_entry") or "-"))
            entry_price = self._fmt(payload.get("entry_price"))
            sl_price = self._fmt(payload.get("sl_price"))
            stake_usd = self._fmt(payload.get("stake_usd"), 2)
            leverage = escape(str(payload.get("leverage") or payload.get("final_leverage") or payload.get("default_leverage") or "-"))
            qty = self._fmt(payload.get("qty"), 8)
            tp_lines = self._tp_plan_lines(labels, payload, side)
            protection_warning_line = self._protection_warning_line(labels, payload)
            account_margin_line = self._account_margin_line(labels, payload)
            exit_profile_lines = self._exit_profile_lines(labels, payload)
            recovery_note_line = self._entry_recovery_note_line(labels, payload)
            return (
                f"{self._opened_title(labels, side)}\n\n"
                f"<b>{safe_symbol} \u00b7 {tf}</b>\n"
                f"<b>{labels['entry_price']}:</b> {entry_price}\n"
                f"<b>{labels['sl_price']}:</b> {sl_price}\n\n"
                f"{tp_lines}\n\n"
                f"<b>{labels['stake']}:</b> {stake_usd} USDT\n"
                f"<b>{labels['leverage']}:</b> x{leverage}\n"
                f"<b>{labels['qty']}:</b> {qty}"
                f"{protection_warning_line}"
                f"{self._leverage_adjustment_line(payload)}"
                f"{account_margin_line}"
                f"{exit_profile_lines}"
                f"{recovery_note_line}"
                f"{format_admin_signal_debug_block(payload, is_admin=admin_live)}"
            )

        if event_type == "TP_HIT":
            return self._build_tp_fill_message(labels=labels, symbol=safe_symbol, payload=payload)

        if event_type == "SOFT_STOP_ACTIVATED":
            return self._build_soft_stop_message(labels=labels, symbol=safe_symbol, payload=payload, kind="activated")

        if event_type == "SOFT_STOP_RAISED":
            return self._build_soft_stop_message(labels=labels, symbol=safe_symbol, payload=payload, kind="raised")

        if event_type == "STOP":
            realized = self._fmt_pnl_marker(payload.get("realized_pnl_usd"))
            return (
                f"\U0001F6D1 <b>{labels['stop_title']}</b>\n\n"
                f"<b>{safe_symbol}</b>\n"
                f"<b>{labels['result']}:</b> {realized} USDT"
                f"{self._account_margin_line(labels, payload)}"
                f"{format_admin_close_debug_block(payload, is_admin=admin_live)}"
            )

        if event_type == "CLOSED_PNL_PENDING":
            return (
                f"\u23f3 <b>{labels['pnl_pending_title']}</b>\n\n"
                f"<b>{safe_symbol}</b>\n"
                f"{labels['pnl_pending_body']}"
            )

        if event_type == "INVALID_API":
            reason = escape(str(payload.get("reason") or "invalid_api"))
            return (
                f"\U0001F511 <b>{labels['invalid_api_title']}</b>\n\n"
                f"<b>{safe_symbol}</b>\n"
                f"<b>{labels['reason']}:</b> <code>{reason}</code>"
            )

        if event_type == "CLOSED":
            if self._is_tp_close_payload(payload):
                return self._build_tp_fill_message(labels=labels, symbol=safe_symbol, payload=payload)
            realized = self._fmt_pnl_marker(payload.get("realized_pnl_usd"))
            title = labels["soft_stop_closed_title"] if self._is_soft_stop_close(payload) else labels["closed_title"]
            return (
                f"\u2705 <b>{title}</b>\n\n"
                f"<b>{safe_symbol}</b>\n"
                f"<b>{labels['realized']}:</b> {realized} USDT"
                f"{self._account_margin_line(labels, payload)}"
                f"{format_admin_close_debug_block(payload, is_admin=admin_live)}"
            )

        return (
            f"\u2139\ufe0f <b>{labels['system_title']}</b>\n\n"
            f"<b>{labels['event']}:</b> {escape(str(event_type))}\n"
            f"<b>{labels['symbol']}:</b> {safe_symbol}\n"
            f"<b>{labels['mode']}:</b> {escape(str(mode))}\n"
            f"<b>{labels['user']}:</b> {escape(str(user_id or '-'))}"
        )

    def build_admin_debug(
        self,
        *,
        event_type: str,
        symbol: str,
        user_id: str,
        mode: str,
        payload: dict | None = None,
    ) -> str:
        payload = payload or {}
        return (
            f"\U0001F6E0 <b>Debug event</b>\n\n"
            f"<b>Event:</b> {escape(str(event_type))}\n"
            f"<b>Symbol:</b> {escape(str(symbol))}\n"
            f"<b>User:</b> {escape(str(user_id))}\n"
            f"<b>Mode:</b> {escape(str(mode))}\n"
            f"<b>Payload:</b> <code>{self._compact_payload(payload)}</code>"
        )

    def _normalize_lang(self, lang: str | None) -> str:
        value = str(lang or "").strip().lower()
        if value.startswith("uk"):
            return "uk"
        if value.startswith("ru"):
            return "ru"
        return "en"

    def _opened_title(self, labels: dict[str, str], side: str) -> str:
        emoji = "\U0001F534" if side == "SHORT" else "\U0001F7E2"
        return f"{emoji} <b>{side} {labels['opened_suffix']}</b>"

    def _side(self, payload: dict) -> str:
        raw = str(
            payload.get("side")
            or payload.get("trade_side")
            or payload.get("position_side")
            or "LONG"
        ).upper()
        return raw if raw in {"LONG", "SHORT"} else "LONG"

    def _calc_tp_price(self, entry_price, pct: float, side: str) -> str:
        if entry_price is None:
            return "-"
        try:
            value = float(entry_price) * (1.0 + pct if side != "SHORT" else 1.0 - pct)
            return self._fmt(value)
        except Exception:
            return "-"

    def _tp_plan_lines(self, labels: dict[str, str], payload: dict, side: str) -> str:
        plan = self._tp_plan_from_payload(payload, side)
        if not plan:
            return f"<b>{labels.get('tp_levels_label', 'TP')}:</b> -"

        lines = [f"<b>{labels.get('tp_levels_label', 'TP')}:</b> {len(plan)}"]
        small_margin_reason = self._small_margin_tp_reason_line(labels, payload)
        if small_margin_reason:
            lines.append(small_margin_reason)
        for index, item in enumerate(plan, start=1):
            stage = int(item.get("stage") or index)
            price = self._fmt(item.get("price") or item.get("triggerPrice"))
            pct = self._fmt_percent(self._tp_close_fraction_from_item(item) * 100.0)
            lines.append(f"<b>TP{stage}:</b> {price} ({pct})")
        return "\n".join(lines)

    def _tp_plan_from_payload(self, payload: dict, side: str) -> list[dict]:
        raw_plan = payload.get("tp_plan")
        if isinstance(raw_plan, list) and raw_plan:
            return [dict(item) for item in raw_plan if isinstance(item, dict)]

        raw_orders = payload.get("exchange_tp_orders")
        if isinstance(raw_orders, list) and raw_orders:
            return [dict(item) for item in raw_orders if isinstance(item, dict)]

        entry_price = payload.get("entry_price")
        levels = payload.get("tp_levels")
        fractions = payload.get("tp_close_fractions")
        if not isinstance(levels, list) or not levels:
            levels = [0.005, 0.010, 0.015, 0.020]

        tp_count = self._safe_int(payload.get("tp_count"), default=len(levels))
        tp_count = max(1, min(4, tp_count))
        levels = levels[:tp_count]
        normalized_fractions = self._normalize_close_fractions(fractions, tp_count)

        plan: list[dict] = []
        for index, level in enumerate(levels, start=1):
            plan.append(
                {
                    "stage": index,
                    "level": level,
                    "close_fraction": normalized_fractions[index - 1],
                    "price": self._calc_tp_price(entry_price, float(level), side),
                }
            )
        return plan

    def _normalize_close_fractions(self, fractions, tp_count: int) -> list[float]:
        if isinstance(fractions, list) and len(fractions) >= tp_count:
            result = []
            for item in fractions[:tp_count]:
                try:
                    result.append(max(float(item), 0.0))
                except (TypeError, ValueError):
                    result.append(0.0)
            if any(value > 0 for value in result):
                return result
        return list(self._default_close_fractions(tp_count))

    @staticmethod
    def _default_close_fractions(tp_count: int) -> tuple[float, ...]:
        tp_count = max(1, min(4, int(tp_count)))
        if tp_count == 1:
            return (1.0,)
        equal_fraction = 1.0 / float(tp_count)
        return tuple(equal_fraction for _ in range(tp_count))

    def _build_tp_fill_message(self, *, labels: dict[str, str], symbol: str, payload: dict) -> str:
        tp_index = self._safe_int(payload.get("tp_index"), default=self._tp_stage_from_reason(payload.get("reason")))
        tp_count = self._safe_int(payload.get("tp_count"), default=max(tp_index, 1))
        tp_count = max(tp_count, tp_index, 1)
        close_pct = self._tp_payload_close_pct(payload, tp_index=tp_index, tp_count=tp_count)
        is_closed = bool(payload.get("trade_closed"))
        remaining_pct_value = self._remaining_pct_value(payload, close_pct=close_pct, is_closed=is_closed)
        realized = self._fmt_signed(payload.get("realized_pnl_usd"))

        status_line = labels["trade_fully_closed"] if is_closed else labels["trade_remains_open"]
        return (
            f"\u2705 <b>{labels['tp_completed'].format(tp=tp_index, total=tp_count)}</b>\n\n"
            f"<b>{symbol}</b>\n"
            f"<b>{labels['closed']}:</b> {self._fmt_percent(close_pct)}\n"
            f"<b>{labels['remaining']}:</b> {self._fmt_percent(0.0 if is_closed else remaining_pct_value)}\n"
            f"<b>{labels['realized']}:</b> {realized} USDT\n"
            f"{status_line}"
            f"{self._account_margin_line(labels, payload)}"
        )

    def _remaining_pct_value(self, payload: dict, *, close_pct: float, is_closed: bool) -> float:
        if is_closed:
            return 0.0

        for key in ("exchange_remaining_pct", "remaining_pct"):
            if payload.get(key) is None:
                continue
            try:
                value = float(payload.get(key))
            except (TypeError, ValueError):
                continue
            percent_value = value * 100.0 if value <= 1.0 else value
            if percent_value > 0.000001:
                return percent_value

        exchange_qty = (
            payload.get("exchange_position_amt_after_close")
            or payload.get("exchange_position_amt")
            or payload.get("qty_remaining")
        )
        total_qty = payload.get("qty") or payload.get("initial_qty")
        try:
            exchange_qty_float = abs(float(exchange_qty))
            total_qty_float = abs(float(total_qty))
        except (TypeError, ValueError):
            exchange_qty_float = 0.0
            total_qty_float = 0.0
        if exchange_qty_float > 0.0 and total_qty_float > 0.0:
            return (exchange_qty_float / total_qty_float) * 100.0

        return max(100.0 - close_pct, 0.0)

    def _tp_payload_close_pct(self, payload: dict, *, tp_index: int, tp_count: int) -> float:
        for key in ("tp_close_pct", "closed_pct"):
            if payload.get(key) is not None:
                try:
                    return max(float(payload.get(key)), 0.0)
                except (TypeError, ValueError):
                    pass
        fraction = payload.get("tp_close_fraction")
        try:
            return max(float(fraction), 0.0) * 100.0
        except (TypeError, ValueError):
            pass
        fractions = self._normalize_close_fractions(payload.get("tp_close_fractions"), tp_count)
        if 1 <= tp_index <= len(fractions):
            return fractions[tp_index - 1] * 100.0
        return 100.0 / max(tp_count, 1)

    def _tp_close_fraction_from_item(self, item: dict) -> float:
        for key in ("close_fraction", "tp_close_fraction"):
            if item.get(key) is not None:
                try:
                    return max(float(item.get(key)), 0.0)
                except (TypeError, ValueError):
                    pass
        if item.get("close_pct") is not None:
            try:
                return max(float(item.get("close_pct")), 0.0) / 100.0
            except (TypeError, ValueError):
                pass
        return 0.0

    def _is_tp_close_payload(self, payload: dict) -> bool:
        if payload.get("tp_index") is not None:
            return True
        return self._tp_stage_from_reason(payload.get("reason")) > 0

    def _is_soft_stop_close(self, payload: dict) -> bool:
        reason = str(payload.get("reason") or payload.get("close_reason") or payload.get("close_reason_code") or "")
        return reason.upper() == "SOFT_TRAILING_STOP"

    def _tp_stage_from_reason(self, reason) -> int:
        text = str(reason or "")
        if not (text.startswith("TP") and text.endswith("_HIT")):
            return 0
        try:
            return int(text[2:-4])
        except (TypeError, ValueError):
            return 0

    def _safe_int(self, value, *, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _fmt_percent(self, value) -> str:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return "-"
        text = f"{numeric:.2f}".rstrip("0").rstrip(".")
        return f"{text}%"

    def _fmt(self, value, digits: int = 4) -> str:
        if value is None:
            return "-"
        try:
            return f"{float(value):.{digits}f}"
        except Exception:
            return escape(str(value))

    def _fmt_signed(self, value, digits: int = 2) -> str:
        if value is None:
            return "-"
        try:
            return f"{float(value):+.{digits}f}"
        except Exception:
            return escape(str(value))

    def _fmt_pnl_marker(self, value, digits: int = 2) -> str:
        if value is None:
            return "-"
        try:
            numeric = float(value)
        except Exception:
            return escape(str(value))

        if numeric < 0:
            return f"\U0001F534 \u2212{abs(numeric):.{digits}f}"
        if numeric > 0:
            return f"\U0001F7E2 +{numeric:.{digits}f}"
        return f"{numeric:.{digits}f}"

    def _protection_warning_line(self, labels: dict[str, str], payload: dict) -> str:
        status = str(payload.get("protection_status") or "protected").strip().lower()
        if status == "protected":
            return ""

        if status == "partially_protected":
            message = labels.get(
                "protection_partial",
                "Protection: stop-loss is active, but take-profit setup is incomplete.",
            )
        elif status == "unprotected":
            message = labels.get(
                "protection_unprotected",
                "Protection: TP/SL setup failed. Check the position immediately.",
            )
        else:
            message = labels.get(
                "protection_pending",
                "Protection setup is still in progress.",
            )

        return f"\n\n<b>{labels.get('protection_label', 'Protection')}:</b> {escape(message)}"

    def _exit_profile_lines(self, labels: dict[str, str], payload: dict) -> str:
        exit_profile = payload.get("exit_profile") if isinstance(payload.get("exit_profile"), dict) else {}
        if not bool(payload.get("soft_stop_enabled") or exit_profile.get("soft_stop_enabled")):
            return ""
        activation_value = payload.get("soft_stop_activation_pct") or exit_profile.get("soft_stop_activation_pct") or 0.8
        tp_step = self._fmt_percent(payload.get("tp_step_pct") or exit_profile.get("tp_step_pct") or activation_value)
        activation = self._fmt_percent(activation_value)
        start = self._fmt_percent(payload.get("soft_stop_start_pct") or exit_profile.get("soft_stop_start_pct") or 0.1)
        increment = self._fmt_percent(
            payload.get("soft_stop_increment_pct")
            or payload.get("soft_stop_hourly_increment_pct")
            or exit_profile.get("soft_stop_increment_pct")
            or exit_profile.get("soft_stop_hourly_increment_pct")
            or 0.05
        )
        interval = self._interval_text(
            payload.get("soft_stop_increment_interval_seconds")
            or exit_profile.get("soft_stop_increment_interval_seconds")
            or 900
        )
        activation_trigger = (
            payload.get("soft_stop_activation_trigger")
            or exit_profile.get("soft_stop_activation_trigger")
        )
        strategy_version = str(payload.get("strategy_version") or exit_profile.get("strategy_version") or "").lower()
        if activation_trigger is None and strategy_version == "v2":
            activation_trigger = "tp1_hit"
        template_key = (
            "soft_stop_rule_after_tp1_template"
            if str(activation_trigger or "").lower() == "tp1_hit"
            else "soft_stop_rule_template"
        )
        soft_stop_rule = labels.get(template_key, labels.get("soft_stop_rule_template", labels["soft_stop_rule"])).format(
            activation=activation,
            start=start,
            increment=increment,
            interval=interval,
        )
        return (
            f"\n<b>{labels['tp_step']}:</b> {tp_step}"
            f"\n<b>{labels['soft_stop_rule_label']}:</b> {soft_stop_rule}"
            f"\n<b>{labels['exchange_sl_label']}:</b> {labels['exchange_sl_backup']}"
        )

    def _interval_text(self, value) -> str:
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            return "-"
        if seconds % 3600 == 0:
            hours = seconds // 3600
            return f"{hours}h"
        if seconds % 60 == 0:
            return f"{seconds // 60}m"
        return f"{seconds}s"

    def _build_soft_stop_message(self, *, labels: dict[str, str], symbol: str, payload: dict, kind: str) -> str:
        title_key = "soft_stop_raised_title" if kind == "raised" else "soft_stop_activated_title"
        soft_pct = self._fmt_percent(payload.get("soft_stop_current_pct") or 0.0)
        trigger_price = self._fmt(payload.get("soft_stop_trigger_price"))
        current_profit = self._fmt_percent(payload.get("current_profit_pct") or 0.0)
        return (
            f"\U0001F6E1 <b>{labels[title_key]}</b>\n\n"
            f"<b>{symbol}</b>\n"
            f"<b>{labels['soft_stop']}:</b> {soft_pct}\n"
            f"<b>{labels['soft_stop_price']}:</b> {trigger_price}\n"
            f"<b>{labels['current_profit']}:</b> {current_profit}"
        )

    def _account_margin_line(self, labels: dict[str, str], payload: dict) -> str:
        used = payload.get("account_margin_used_usdt")
        if used is None:
            used = payload.get("account_margin_after_entry_usdt")
        limit = payload.get("account_margin_limit_usdt")
        pct = payload.get("account_margin_usage_pct")
        if used is None or limit is None:
            return ""

        pct_text = self._fmt(pct, 2) if pct is not None else "-"
        return (
            f"\n<b>{labels.get('account_margin', 'Account margin')}:</b> "
            f"{self._fmt(used, 2)} / {self._fmt(limit, 2)} USDT ({pct_text}%)"
        )

    def _leverage_adjustment_line(self, payload: dict) -> str:
        if not bool(payload.get("leverage_adjusted")):
            return ""
        requested = payload.get("requested_leverage") or payload.get("default_leverage") or "-"
        exchange_max = payload.get("symbol_max_leverage") or payload.get("exchange_max") or "-"
        final = payload.get("final_leverage") or payload.get("leverage") or exchange_max
        return (
            "\n\n\u26a0\ufe0f <b>\u041f\u043b\u0435\u0447\u0435 \u0441\u043a\u043e\u0440\u0438\u0433\u043e\u0432\u0430\u043d\u043e</b>"
            f"\n\u0411\u0443\u043b\u043e: x{escape(str(requested))}"
            f"\n\u041c\u0430\u043a\u0441: x{escape(str(exchange_max))}"
            f"\n\u0412\u0438\u043a\u043e\u0440\u0438\u0441\u0442\u0430\u043d\u043e: x{escape(str(final))}"
        )

    def _entry_recovery_note_line(self, labels: dict[str, str], payload: dict) -> str:
        if not bool(payload.get("recovered_notification")):
            return ""
        note = str(payload.get("recovery_note") or labels.get("entry_notification_recovered") or "")
        if not note:
            return ""
        return f"\n\n\u2139\ufe0f {escape(note)}"

    def _small_margin_tp_reason_line(self, labels: dict[str, str], payload: dict) -> str:
        if not bool(payload.get("tp_count_forced_by_small_margin")):
            return ""
        reason = labels.get("tp_small_margin_reason", "Reason: small entry margin < 1 USDT")
        margin = payload.get("entry_margin_usdt")
        if margin is None:
            return escape(reason)
        return f"{escape(reason)} ({self._fmt(margin, 2)} USDT)"

    def _compact_payload(self, payload: dict) -> str:
        return escape("; ".join(f"{key}={value}" for key, value in payload.items()))

    def _labels(self, lang: str) -> dict[str, str]:
        normalized = self._normalize_lang(lang)
        labels_by_lang = {
            "uk": {
                "opened_suffix": "\u0432\u0456\u0434\u043a\u0440\u0438\u0442\u043e",
                "tp_hit": "TP{tp} \u0434\u043e\u0441\u044f\u0433\u043d\u0443\u0442\u043e",
                "closed_title": "\u0423\u0433\u043e\u0434\u0443 \u0437\u0430\u043a\u0440\u0438\u0442\u043e",
                "stop_title": "\u0423\u0433\u043e\u0434\u0443 \u0437\u0430\u043a\u0440\u0438\u0442\u043e \u043f\u043e Stop Loss",
                "pnl_pending_title": "\u0423\u0433\u043e\u0434\u0443 \u0437\u0430\u043a\u0440\u0438\u0442\u043e, PnL \u0443\u0442\u043e\u0447\u043d\u044e\u0454\u0442\u044c\u0441\u044f",
                "pnl_pending_body": "\u0423\u0442\u043e\u0447\u043d\u044e\u044e \u0444\u0456\u043d\u0430\u043b\u044c\u043d\u0438\u0439 PnL \u043d\u0430 \u0431\u0456\u0440\u0436\u0456. \u0424\u0456\u043d\u0430\u043b\u044c\u043d\u0435 \u043f\u043e\u0432\u0456\u0434\u043e\u043c\u043b\u0435\u043d\u043d\u044f \u043f\u0440\u0438\u0439\u0434\u0435 \u043f\u0456\u0441\u043b\u044f \u043f\u0456\u0434\u0442\u0432\u0435\u0440\u0434\u0436\u0435\u043d\u043d\u044f Binance.",
                "invalid_api_title": "\u041f\u043e\u0442\u0440\u0456\u0431\u043d\u0430 \u0443\u0432\u0430\u0433\u0430 \u0434\u043e API \u043a\u043b\u044e\u0447\u0430",
                "system_title": "\u0421\u0438\u0441\u0442\u0435\u043c\u043d\u0430 \u043f\u043e\u0434\u0456\u044f",
                "event": "\u041f\u043e\u0434\u0456\u044f",
                "symbol": "\u041c\u043e\u043d\u0435\u0442\u0430",
                "mode": "\u0420\u0435\u0436\u0438\u043c",
                "user": "\u041a\u043e\u0440\u0438\u0441\u0442\u0443\u0432\u0430\u0447",
                "reason": "\u041f\u0440\u0438\u0447\u0438\u043d\u0430",
                "entry_price": "\u0412\u0445\u0456\u0434",
                "sl_price": "SL",
                "stake": "\u0421\u0442\u0430\u0432\u043a\u0430",
                "leverage": "\u041f\u043b\u0435\u0447\u0435",
                "qty": "\u041a\u0456\u043b\u044c\u043a\u0456\u0441\u0442\u044c",
                "closed": "\u0417\u0430\u043a\u0440\u0438\u0442\u043e",
                "remaining": "\u0417\u0430\u043b\u0438\u0448\u043e\u043a",
                "realized": "\u0420\u0435\u0430\u043b\u0456\u0437\u043e\u0432\u0430\u043d\u043e",
                "result": "\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442",
                "tp_levels_label": "TP",
                "tp_completed": "TP{tp} / {total} \u0432\u0438\u043a\u043e\u043d\u0430\u043d\u043e",
                "trade_fully_closed": "\u0423\u0433\u043e\u0434\u0443 \u043f\u043e\u0432\u043d\u0456\u0441\u0442\u044e \u0437\u0430\u043a\u0440\u0438\u0442\u043e",
                "trade_remains_open": "\u0423\u0433\u043e\u0434\u0430 \u0437\u0430\u043b\u0438\u0448\u0430\u0454\u0442\u044c\u0441\u044f \u0432\u0456\u0434\u043a\u0440\u0438\u0442\u043e\u044e",
                "account_margin": "\u041c\u0430\u0440\u0436\u0430 \u0430\u043a\u0430\u0443\u043d\u0442\u0430",
                "protection_label": "\u0417\u0430\u0445\u0438\u0441\u0442",
                "protection_partial": "\u0421\u0442\u043e\u043f-\u043b\u043e\u0441 \u0430\u043a\u0442\u0438\u0432\u043d\u0438\u0439, \u0430\u043b\u0435 TP \u043d\u0430\u043b\u0430\u0448\u0442\u043e\u0432\u0430\u043d\u0456 \u043d\u0435 \u043f\u043e\u0432\u043d\u0456\u0441\u0442\u044e.",
                "protection_unprotected": "\u041d\u0435 \u0432\u0434\u0430\u043b\u043e\u0441\u044f \u0432\u0438\u0441\u0442\u0430\u0432\u0438\u0442\u0438 TP/SL. \u0422\u0435\u0440\u043c\u0456\u043d\u043e\u0432\u043e \u043f\u0435\u0440\u0435\u0432\u0456\u0440\u0442\u0435 \u043f\u043e\u0437\u0438\u0446\u0456\u044e.",
                "protection_pending": "\u0411\u043e\u0442 \u0449\u0435 \u0437\u0430\u0432\u0435\u0440\u0448\u0443\u0454 \u043d\u0430\u043b\u0430\u0448\u0442\u0443\u0432\u0430\u043d\u043d\u044f \u0437\u0430\u0445\u0438\u0441\u0442\u0443.",
                "tp_step": "Крок TP",
                "soft_stop_rule_label": "V2 soft stop",
                "soft_stop_rule": "Після +{activation} стоп переноситься в +{start}, далі +{increment} кожні {interval}",
                "soft_stop_rule_template": "Після +{activation} стоп переноситься в +{start}, далі +{increment} кожні {interval}",
                "soft_stop_rule_after_tp1_template": "Активація: після TP1 (+{activation}); початковий soft stop +{start}, далі +{increment} кожні {interval}",
                "exchange_sl_label": "Біржовий SL",
                "exchange_sl_backup": "використовується як запобіжник",
                "soft_stop_activated_title": "V2 soft stop активовано",
                "soft_stop_raised_title": "V2 soft stop оновлено",
                "soft_stop_closed_title": "Угоду закрито по V2 soft stop",
                "soft_stop": "Soft stop",
                "soft_stop_price": "Ціна soft stop",
                "current_profit": "Поточний прибуток",
                "tp_small_margin_reason": "\u041f\u0440\u0438\u0447\u0438\u043d\u0430: \u043c\u0430\u043b\u0430 \u043c\u0430\u0440\u0436\u0430 \u0432\u0445\u043e\u0434\u0443 < 1 USDT",
                "entry_notification_recovered": "\u041f\u043e\u0432\u0456\u0434\u043e\u043c\u043b\u0435\u043d\u043d\u044f \u0432\u0456\u0434\u043d\u043e\u0432\u043b\u0435\u043d\u043e \u043f\u0456\u0441\u043b\u044f \u043f\u0435\u0440\u0435\u0432\u0456\u0440\u043a\u0438 \u0441\u0442\u0430\u043d\u0443 \u0431\u0456\u0440\u0436\u0456",
            },
            "ru": {
                "opened_suffix": "\u043e\u0442\u043a\u0440\u044b\u0442",
                "tp_hit": "TP{tp} \u0434\u043e\u0441\u0442\u0438\u0433\u043d\u0443\u0442",
                "closed_title": "\u0421\u0434\u0435\u043b\u043a\u0430 \u0437\u0430\u043a\u0440\u044b\u0442\u0430",
                "stop_title": "\u0421\u0434\u0435\u043b\u043a\u0430 \u0437\u0430\u043a\u0440\u044b\u0442\u0430 \u043f\u043e Stop Loss",
                "pnl_pending_title": "\u0421\u0434\u0435\u043b\u043a\u0430 \u0437\u0430\u043a\u0440\u044b\u0442\u0430, PnL \u0443\u0442\u043e\u0447\u043d\u044f\u0435\u0442\u0441\u044f",
                "pnl_pending_body": "\u0423\u0442\u043e\u0447\u043d\u044f\u044e \u0444\u0438\u043d\u0430\u043b\u044c\u043d\u044b\u0439 PnL \u043d\u0430 \u0431\u0438\u0440\u0436\u0435. \u0424\u0438\u043d\u0430\u043b\u044c\u043d\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u043f\u0440\u0438\u0434\u0435\u0442 \u043f\u043e\u0441\u043b\u0435 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u044f Binance.",
                "invalid_api_title": "API \u043a\u043b\u044e\u0447 \u0442\u0440\u0435\u0431\u0443\u0435\u0442 \u0432\u043d\u0438\u043c\u0430\u043d\u0438\u044f",
                "system_title": "\u0421\u0438\u0441\u0442\u0435\u043c\u043d\u043e\u0435 \u0441\u043e\u0431\u044b\u0442\u0438\u0435",
                "event": "\u0421\u043e\u0431\u044b\u0442\u0438\u0435",
                "symbol": "\u041c\u043e\u043d\u0435\u0442\u0430",
                "mode": "\u0420\u0435\u0436\u0438\u043c",
                "user": "\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c",
                "reason": "\u041f\u0440\u0438\u0447\u0438\u043d\u0430",
                "entry_price": "\u0412\u0445\u043e\u0434",
                "sl_price": "SL",
                "stake": "\u0421\u0442\u0430\u0432\u043a\u0430",
                "leverage": "\u041f\u043b\u0435\u0447\u043e",
                "qty": "\u041a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e",
                "closed": "\u0417\u0430\u043a\u0440\u044b\u0442\u043e",
                "remaining": "\u041e\u0441\u0442\u0430\u0442\u043e\u043a",
                "realized": "\u0420\u0435\u0430\u043b\u0438\u0437\u043e\u0432\u0430\u043d\u043e",
                "result": "\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442",
                "tp_levels_label": "TP",
                "tp_completed": "TP{tp} / {total} \u0438\u0441\u043f\u043e\u043b\u043d\u0435\u043d",
                "trade_fully_closed": "\u0421\u0434\u0435\u043b\u043a\u0430 \u043f\u043e\u043b\u043d\u043e\u0441\u0442\u044c\u044e \u0437\u0430\u043a\u0440\u044b\u0442\u0430",
                "trade_remains_open": "\u0421\u0434\u0435\u043b\u043a\u0430 \u043e\u0441\u0442\u0430\u0451\u0442\u0441\u044f \u043e\u0442\u043a\u0440\u044b\u0442\u043e\u0439",
                "account_margin": "\u041c\u0430\u0440\u0436\u0430 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u0430",
                "protection_label": "\u0417\u0430\u0449\u0438\u0442\u0430",
                "protection_partial": "\u0421\u0442\u043e\u043f-\u043b\u043e\u0441 \u0430\u043a\u0442\u0438\u0432\u0435\u043d, \u043d\u043e TP \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d\u044b \u043d\u0435 \u043f\u043e\u043b\u043d\u043e\u0441\u0442\u044c\u044e.",
                "protection_unprotected": "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0432\u044b\u0441\u0442\u0430\u0432\u0438\u0442\u044c TP/SL. \u0421\u0440\u043e\u0447\u043d\u043e \u043f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u043f\u043e\u0437\u0438\u0446\u0438\u044e.",
                "protection_pending": "\u0411\u043e\u0442 \u0435\u0449\u0451 \u0437\u0430\u0432\u0435\u0440\u0448\u0430\u0435\u0442 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0443 \u0437\u0430\u0449\u0438\u0442\u044b.",
                "tp_step": "Шаг TP",
                "soft_stop_rule_label": "V2 soft stop",
                "soft_stop_rule": "После +{activation} стоп переносится в +{start}, дальше +{increment} каждые {interval}",
                "soft_stop_rule_template": "После +{activation} стоп переносится в +{start}, дальше +{increment} каждые {interval}",
                "soft_stop_rule_after_tp1_template": "Активация: после TP1 (+{activation}); начальный soft stop +{start}, дальше +{increment} каждые {interval}",
                "exchange_sl_label": "Биржевой SL",
                "exchange_sl_backup": "используется как страховочный стоп",
                "soft_stop_activated_title": "V2 soft stop активирован",
                "soft_stop_raised_title": "V2 soft stop обновлен",
                "soft_stop_closed_title": "Сделка закрыта по V2 soft stop",
                "soft_stop": "Soft stop",
                "soft_stop_price": "Цена soft stop",
                "current_profit": "Текущая прибыль",
                "tp_small_margin_reason": "\u041f\u0440\u0438\u0447\u0438\u043d\u0430: \u043c\u0430\u043b\u0430\u044f \u043c\u0430\u0440\u0436\u0430 \u0432\u0445\u043e\u0434\u0430 < 1 USDT",
                "entry_notification_recovered": "\u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u0432\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u043e \u043f\u043e\u0441\u043b\u0435 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438 \u0441\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u044f \u0431\u0438\u0440\u0436\u0438",
            },
            "en": {
                "opened_suffix": "opened",
                "tp_hit": "TP{tp} hit",
                "closed_title": "Trade closed",
                "stop_title": "Trade closed by Stop Loss",
                "pnl_pending_title": "Trade closed, PnL is being verified",
                "pnl_pending_body": "I am confirming the final PnL on Binance. The final message will be sent after exchange confirmation.",
                "invalid_api_title": "API key needs attention",
                "system_title": "System event",
                "event": "Event",
                "symbol": "Symbol",
                "mode": "Mode",
                "user": "User",
                "reason": "Reason",
                "entry_price": "Entry",
                "sl_price": "SL",
                "stake": "Stake",
                "leverage": "Leverage",
                "qty": "Qty",
                "closed": "Closed",
                "remaining": "Remaining",
                "realized": "Realized",
                "result": "Result",
                "tp_levels_label": "TP",
                "tp_completed": "TP{tp} / {total} filled",
                "trade_fully_closed": "Trade fully closed",
                "trade_remains_open": "Trade remains open",
                "account_margin": "Account margin",
                "protection_label": "Protection",
                "protection_partial": "Stop-loss is active, but take-profit setup is incomplete.",
                "protection_unprotected": "TP/SL setup failed. Check the position immediately.",
                "protection_pending": "Protection setup is still in progress.",
                "tp_step": "TP step",
                "soft_stop_rule_label": "V2 soft stop",
                "soft_stop_rule": "After +{activation}, stop moves to +{start}, then +{increment} every {interval}",
                "soft_stop_rule_template": "After +{activation}, stop moves to +{start}, then +{increment} every {interval}",
                "soft_stop_rule_after_tp1_template": "Activation: after TP1 (+{activation}); initial soft stop +{start}, then +{increment} every {interval}",
                "exchange_sl_label": "Exchange SL",
                "exchange_sl_backup": "used as safety backup",
                "soft_stop_activated_title": "V2 soft stop activated",
                "soft_stop_raised_title": "V2 soft stop updated",
                "soft_stop_closed_title": "Trade closed by V2 soft stop",
                "soft_stop": "Soft stop",
                "soft_stop_price": "Soft stop price",
                "current_profit": "Current profit",
                "tp_small_margin_reason": "Reason: small entry margin < 1 USDT",
                "entry_notification_recovered": "Notification recovered after exchange state check",
            },
        }
        return labels_by_lang[normalized]
