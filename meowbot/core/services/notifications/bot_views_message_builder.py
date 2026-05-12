from __future__ import annotations


class BotViewsMessageBuilder:
    def build_my_bot(self, lang: str, data: dict) -> str:
        labels = self._labels(lang)

        symbols = ", ".join(data.get("active_symbols", [])) if data.get("active_symbols") else labels["none"]
        tfs = ", ".join(data.get("active_tfs", [])) if data.get("active_tfs") else labels["none"]

        mode_raw = str(data.get("trading_mode", "sandbox"))
        mode_text = labels["mode_sandbox"] if mode_raw == "sandbox" else labels["mode_live"]

        notifications_text = labels["yes"] if data.get("notifications_enabled") else labels["no"]

        stake_mode = str(data.get("default_stake_mode", "percent"))
        if stake_mode == "percent":
            stake_text = f"{float(data.get('default_stake_value', 1.0)):.2f}%"
        else:
            stake_text = f"{float(data.get('default_stake_value', 0.0)):.2f} USD"

        return (
            f"{labels['my_bot_title']}\n\n"
            f"<b>{labels['mode']}:</b> {mode_text}\n"
            f"<b>{labels['language']}:</b> {data.get('preferred_language', 'uk')}\n"
            f"<b>{labels['sandbox_balance']}:</b> {float(data.get('sandbox_balance_usd', 0.0)):.2f} USD\n"
            f"<b>{labels['open_trades']}:</b> {int(data.get('open_trades_count', 0))}\n"
            f"<b>{labels['closed_trades']}:</b> {int(data.get('closed_trades_count', 0))}\n"
            f"<b>{labels['stake']}:</b> {stake_text}\n"
            f"<b>{labels['leverage']}:</b> x{int(data.get('default_leverage', 20))}\n"
            f"<b>{labels['notifications']}:</b> {notifications_text}\n"
            f"<b>{labels['symbols']}:</b> {symbols}\n"
            f"<b>{labels['timeframes']}:</b> {tfs}"
        )

    def build_trades(self, lang: str, data: dict) -> str:
        labels = self._labels(lang)

        open_trades = data.get("open_trades", []) or []
        recent_closed = data.get("recent_closed_trades", []) or []

        open_lines: list[str] = []
        for trade in open_trades[:5]:
            side = self._side_label(lang, str(trade.get("side", "LONG")))
            open_lines.append(
                f"• <b>{trade.get('symbol', '-')}</b> | {side} | {trade.get('tf_entry', '-')}\n"
                f"  {labels['entry']}: {self._fmt_num(trade.get('entry_price'))} | "
                f"{labels['qty_left']}: {self._fmt_num(trade.get('qty_remaining'), 8)}"
            )

        closed_lines: list[str] = []
        for trade in recent_closed[:5]:
            side = self._side_label(lang, str(trade.get("side", "LONG")))
            pnl = self._realized_pnl(trade)
            close_reason = trade.get("close_reason") or labels["unknown"]
            closed_lines.append(
                f"• <b>{trade.get('symbol', '-')}</b> | {side} | {trade.get('tf_entry', '-')}\n"
                f"  {labels['result']}: {pnl:.2f} USD | "
                f"{labels['close_reason']}: {close_reason}"
            )

        open_block = "\n".join(open_lines) if open_lines else labels["none"]
        closed_block = "\n".join(closed_lines) if closed_lines else labels["none"]

        return (
            f"{labels['trades_title']}\n\n"
            f"<b>{labels['open_trades_section']}</b>\n"
            f"{open_block}\n\n"
            f"<b>{labels['recent_closed_section']}</b>\n"
            f"{closed_block}"
        )

    def _fmt_num(self, value, digits: int = 4) -> str:
        if value is None:
            return "-"
        try:
            return f"{float(value):.{digits}f}"
        except Exception:
            return str(value)

    def _realized_pnl(self, trade: dict) -> float:
        try:
            exchange_pnl = trade.get("exchange_net_realized_pnl_usd")
            if exchange_pnl is None:
                exchange_pnl = trade.get("exchange_net_realized_pnl_usdt")
            if exchange_pnl is None:
                exchange_pnl = trade.get("exchange_realized_pnl_usd")
            if exchange_pnl is None:
                exchange_pnl = trade.get("exchange_realized_pnl_usdt")
            if str(trade.get("mode") or "").lower() == "live" and exchange_pnl is not None:
                return float(exchange_pnl or 0.0)
            return float(trade.get("realized_pnl_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _side_label(self, lang: str, side: str) -> str:
        labels = self._labels(lang)
        if side.upper() == "SHORT":
            return labels["short"]
        return labels["long"]

    def _labels(self, lang: str) -> dict[str, str]:
        if lang == "ru":
            return {
                "my_bot_title": "🤖 <b>Мой бот</b>",
                "mode": "Режим",
                "mode_sandbox": "Sandbox",
                "mode_live": "Live",
                "language": "Язык",
                "sandbox_balance": "Sandbox баланс",
                "open_trades": "Открытых сделок",
                "closed_trades": "Закрытых сделок",
                "stake": "Размер позиции",
                "leverage": "Плечо",
                "notifications": "Уведомления",
                "symbols": "Активные монеты",
                "timeframes": "Активные таймфреймы",
                "yes": "включены",
                "no": "выключены",
                "none": "Нет данных",
                "trades_title": "📈 <b>Сделки</b>",
                "open_trades_section": "Открытые сделки",
                "recent_closed_section": "Последние закрытые",
                "entry": "Вход",
                "qty_left": "Остаток",
                "result": "Результат",
                "close_reason": "Причина",
                "unknown": "-",
                "long": "LONG",
                "short": "SHORT",
            }
        if lang == "en":
            return {
                "my_bot_title": "🤖 <b>My Bot</b>",
                "mode": "Mode",
                "mode_sandbox": "Sandbox",
                "mode_live": "Live",
                "language": "Language",
                "sandbox_balance": "Sandbox balance",
                "open_trades": "Open trades",
                "closed_trades": "Closed trades",
                "stake": "Position size",
                "leverage": "Leverage",
                "notifications": "Notifications",
                "symbols": "Active symbols",
                "timeframes": "Active timeframes",
                "yes": "enabled",
                "no": "disabled",
                "none": "No data",
                "trades_title": "📈 <b>Trades</b>",
                "open_trades_section": "Open trades",
                "recent_closed_section": "Recent closed",
                "entry": "Entry",
                "qty_left": "Qty left",
                "result": "Result",
                "close_reason": "Reason",
                "unknown": "-",
                "long": "LONG",
                "short": "SHORT",
            }
        return {
            "my_bot_title": "🤖 <b>Мій бот</b>",
            "mode": "Режим",
            "mode_sandbox": "Sandbox",
            "mode_live": "Live",
            "language": "Мова",
            "sandbox_balance": "Sandbox баланс",
            "open_trades": "Відкритих трейдів",
            "closed_trades": "Закритих трейдів",
            "stake": "Розмір позиції",
            "leverage": "Плече",
            "notifications": "Нотифікації",
            "symbols": "Активні монети",
            "timeframes": "Активні таймфрейми",
            "yes": "увімкнені",
            "no": "вимкнені",
            "none": "Немає даних",
            "trades_title": "📈 <b>Трейди</b>",
            "open_trades_section": "Відкриті трейди",
            "recent_closed_section": "Останні закриті",
            "entry": "Вхід",
            "qty_left": "Залишок",
            "result": "Результат",
            "close_reason": "Причина",
            "unknown": "-",
            "long": "LONG",
            "short": "SHORT",
        }
