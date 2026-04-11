from __future__ import annotations


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
    ) -> str:
        payload = payload or {}
        labels = self._labels(lang)

        if event_type == "OPENED":
            tf = payload.get("tf_entry", "-")
            entry_price = self._fmt(payload.get("entry_price"))
            sl_price = self._fmt(payload.get("sl_price"))
            stake_usd = self._fmt(payload.get("stake_usd"), 2)
            leverage = payload.get("default_leverage", "-")
            qty = self._fmt(payload.get("qty"), 8)

            tp1_price = self._calc_tp_price(payload.get("entry_price"), 0.005)
            tp2_price = self._calc_tp_price(payload.get("entry_price"), 0.010)
            tp3_price = self._calc_tp_price(payload.get("entry_price"), 0.015)

            return (
                f"{labels['opened_title']}\n\n"
                f"🪙 <b>{symbol} · {tf}</b>\n"
                f"💵 <b>{labels['entry_price']}:</b> {entry_price}\n"
                f"🛡 <b>{labels['sl_price']}:</b> {sl_price}\n\n"
                f"🎯 <b>TP1:</b> {tp1_price}\n"
                f"🎯 <b>TP2:</b> {tp2_price}\n"
                f"🎯 <b>TP3:</b> {tp3_price}\n\n"
                f"💼 <b>{labels['stake']}:</b> {stake_usd} USDT\n"
                f"⚡ <b>{labels['leverage']}:</b> x{leverage}\n"
                f"📦 <b>{labels['qty']}:</b> {qty}"
            )

        if event_type == "TP_HIT":
            tp_index = int(payload.get("tp_index", 0) or 0)
            closed_pct = self._tp_closed_pct(tp_index)
            realized = self._fmt_signed(payload.get("realized_pnl_usd"))

            return (
                f"{labels['tp_title_prefix']} TP{tp_index} {labels['tp_title_suffix']}\n\n"
                f"🪙 <b>{symbol}</b>\n"
                f"📦 <b>{labels['closed']}:</b> {closed_pct}%\n"
                f"💰 <b>{labels['realized']}:</b> {realized} USDT"
            )

        if event_type == "CLOSED":
            reason = str(payload.get("reason", "") or "")
            realized = self._fmt_signed(payload.get("realized_pnl_usd"))

            if reason == "STOP_LOSS_HIT":
                return (
                    f"{labels['stop_title']}\n\n"
                    f"🪙 <b>{symbol}</b>\n"
                    f"💸 <b>{labels['result']}:</b> {realized} USDT"
                )

            return (
                f"{labels['closed_title']}\n\n"
                f"🪙 <b>{symbol}</b>\n"
                f"💰 <b>{labels['realized']}:</b> {realized} USDT"
            )

        return (
            f"{labels['system_title']}\n\n"
            f"<b>{labels['event']}:</b> {event_type}\n"
            f"<b>{labels['symbol']}:</b> {symbol}\n"
            f"<b>{labels['mode']}:</b> {mode}\n"
            f"<b>{labels['user']}:</b> {user_id or '-'}"
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
            f"🛠 <b>Debug event</b>\n\n"
            f"<b>Event:</b> {event_type}\n"
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>User:</b> {user_id}\n"
            f"<b>Mode:</b> {mode}\n"
            f"<b>Payload:</b> <code>{self._compact_payload(payload)}</code>"
        )

    def _calc_tp_price(self, entry_price, pct: float) -> str:
        if entry_price is None:
            return "-"
        try:
            value = float(entry_price) * (1.0 + pct)
            return self._fmt(value)
        except Exception:
            return "-"

    def _tp_closed_pct(self, tp_index: int) -> int:
        if tp_index == 1:
            return 70
        if tp_index == 2:
            return 20
        if tp_index == 3:
            return 10
        return 0

    def _fmt(self, value, digits: int = 4) -> str:
        if value is None:
            return "-"
        try:
            return f"{float(value):.{digits}f}"
        except Exception:
            return str(value)

    def _fmt_signed(self, value, digits: int = 2) -> str:
        if value is None:
            return "-"
        try:
            return f"{float(value):+.{digits}f}"
        except Exception:
            return str(value)

    def _compact_payload(self, payload: dict) -> str:
        parts: list[str] = []
        for key, value in payload.items():
            parts.append(f"{key}={value}")
        return "; ".join(parts)

    def _labels(self, lang: str) -> dict[str, str]:
        if lang == "ru":
            return {
                "opened_title": "🟢 <b>Открыт LONG</b>",
                "tp_title_prefix": "🎯",
                "tp_title_suffix": "достигнут",
                "closed_title": "🏁 <b>Сделка закрыта полностью</b>",
                "stop_title": "🛑 <b>Сделка закрыта по Stop Loss</b>",
                "system_title": "ℹ️ <b>Системное событие</b>",
                "event": "Событие",
                "symbol": "Символ",
                "mode": "Режим",
                "user": "Пользователь",
                "entry_price": "Вход",
                "sl_price": "SL",
                "stake": "Сумма",
                "leverage": "Плечо",
                "qty": "Объём",
                "closed": "Закрыто",
                "realized": "Реализовано",
                "result": "Результат",
            }
        if lang == "en":
            return {
                "opened_title": "🟢 <b>LONG opened</b>",
                "tp_title_prefix": "🎯",
                "tp_title_suffix": "hit",
                "closed_title": "🏁 <b>Trade fully closed</b>",
                "stop_title": "🛑 <b>Trade closed by Stop Loss</b>",
                "system_title": "ℹ️ <b>System event</b>",
                "event": "Event",
                "symbol": "Symbol",
                "mode": "Mode",
                "user": "User",
                "entry_price": "Entry",
                "sl_price": "SL",
                "stake": "Stake",
                "leverage": "Leverage",
                "qty": "Qty",
                "closed": "Closed",
                "realized": "Realized",
                "result": "Result",
            }
        return {
            "opened_title": "🟢 <b>Відкрито LONG</b>",
            "tp_title_prefix": "🎯",
            "tp_title_suffix": "досягнуто",
            "closed_title": "🏁 <b>Трейд закрито повністю</b>",
            "stop_title": "🛑 <b>Трейд закрито по Stop Loss</b>",
            "system_title": "ℹ️ <b>Системна подія</b>",
            "event": "Подія",
            "symbol": "Символ",
            "mode": "Режим",
            "user": "Користувач",
            "entry_price": "Вхід",
            "sl_price": "SL",
            "stake": "Сума",
            "leverage": "Плече",
            "qty": "Обсяг",
            "closed": "Закрито",
            "realized": "Реалізовано",
            "result": "Результат",
        }