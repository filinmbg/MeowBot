from __future__ import annotations


SUPPRESSED_USER_BLOCK_REASONS = {
    "open_trade_exists_for_symbol",
    "max_open_trades_per_symbol_exceeded",
    "risk_limit_reached",
}


class RiskSubscriptionMessageBuilder:
    def build_user_message(self, event_type: str, payload: dict) -> str | None:
        reason = str(payload.get("reason") or "")
        symbol = str(payload.get("symbol") or "")
        tf_entry = str(payload.get("tf_entry") or "")
        current_margin_ratio_pct = payload.get("current_margin_ratio_pct")

        if reason in SUPPRESSED_USER_BLOCK_REASONS:
            return None

        if event_type == "ENTRY_BLOCKED_SUBSCRIPTION":
            if reason == "symbol_not_allowed":
                return (
                    f"⛔ <b>Трейд не відкрито</b>\n\n"
                    f"🪙 <b>{symbol}</b> · {tf_entry}\n"
                    f"Причина: ця монета недоступна у вашому тарифі."
                )

            if reason == "timeframe_not_allowed":
                return (
                    f"⛔ <b>Трейд не відкрито</b>\n\n"
                    f"🪙 <b>{symbol}</b> · {tf_entry}\n"
                    f"Причина: цей таймфрейм недоступний у ваших налаштуваннях."
                )

            if reason == "max_open_trades_total_exceeded":
                return (
                    f"⛔ <b>Трейд не відкрито</b>\n\n"
                    f"🪙 <b>{symbol}</b> · {tf_entry}\n"
                    f"Причина: досягнуто ліміт відкритих трейдів для вашого тарифу."
                )

            if reason in {"max_open_trades_per_symbol_exceeded", "open_trade_exists_for_symbol"}:
                return (
                    f"⛔ <b>Трейд не відкрито</b>\n\n"
                    f"🪙 <b>{symbol}</b> · {tf_entry}\n"
                    f"Причина: по цій монеті вже є відкритий трейд."
                )

            if reason == "long_disabled":
                return (
                    f"⛔ <b>Трейд не відкрито</b>\n\n"
                    f"🪙 <b>{symbol}</b> · {tf_entry}\n"
                    f"Причина: LONG-входи вимкнені у ваших налаштуваннях."
                )

        if event_type == "ENTRY_BLOCKED_RISK":
            if reason == "risk_limit_reached":
                return (
                    f"⛔ <b>Трейд не відкрито</b>\n\n"
                    f"🪙 <b>{symbol}</b> · {tf_entry}\n"
                    f"Причина: досягнуто глобальний ризик-ліміт (5 активних ризикових трейдів).\n"
                    f"Дочекайтесь спрацювання TP1 по існуючих позиціях."
                )

            if reason == "margin_ratio_blocked":
                ratio_text = (
                    f"{float(current_margin_ratio_pct):.2f}%"
                    if current_margin_ratio_pct is not None
                    else "невідомо"
                )
                return (
                    f"⛔ <b>Трейд не відкрито</b>\n\n"
                    f"🪙 <b>{symbol}</b> · {tf_entry}\n"
                    f"Причина: високий margin ratio акаунта.\n"
                    f"Поточний рівень: <b>{ratio_text}</b>"
                )

            if reason == "max_margin_per_trade_exceeded":
                return (
                    f"⛔ <b>Трейд не відкрито</b>\n\n"
                    f"🪙 <b>{symbol}</b> · {tf_entry}\n"
                    f"Причина: перевищено ваш ліміт маржі на одну угоду."
                )

            if reason == "leverage_above_symbol_limit":
                return (
                    f"⛔ <b>Трейд не відкрито</b>\n\n"
                    f"🪙 <b>{symbol}</b> · {tf_entry}\n"
                    f"Причина: вибране плече перевищує допустимий ліміт для цієї монети."
                )

        if event_type == "ENTRY_WARNING_RISK":
            ratio_text = (
                f"{float(current_margin_ratio_pct):.2f}%"
                if current_margin_ratio_pct is not None
                else "невідомо"
            )
            return (
                f"⚠️ <b>Попередження по ризику</b>\n\n"
                f"🪙 <b>{symbol}</b> · {tf_entry}\n"
                f"Поточний margin ratio акаунта: <b>{ratio_text}</b>\n"
                f"Трейд відкрито, але ризик уже підвищений."
            )

        return None

    def build_admin_message(self, event_type: str, payload: dict, *, user_id: str, symbol: str) -> str:
        reason = str(payload.get("reason") or payload.get("warning_code") or "-")
        tf_entry = str(payload.get("tf_entry") or "-")
        margin = payload.get("current_margin_ratio_pct")
        margin_text = f"{float(margin):.2f}%" if margin is not None else "-"

        return (
            f"📣 <b>Risk / Subscription event</b>\n\n"
            f"User: <code>{user_id}</code>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"TF: <code>{tf_entry}</code>\n"
            f"Type: <code>{event_type}</code>\n"
            f"Reason: <code>{reason}</code>\n"
            f"Margin ratio: <code>{margin_text}</code>"
        )
