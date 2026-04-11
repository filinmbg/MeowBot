from __future__ import annotations

from typing import Any


class TelegramTradeMessageFormatter:
    def format_trade_opened(
        self,
        *,
        trade_id: str,
        user_id: str,
        symbol: str,
        tf: str,
        rule_id: str,
        entry_price: float,
        stake_usd: float,
        qty: float,
        sl_price: float,
    ) -> str:
        return (
            "🟢 <b>Відкрито трейд</b>\n\n"
            f"<b>Trade ID:</b> <code>{trade_id}</code>\n"
            f"<b>User:</b> <code>{user_id}</code>\n"
            f"<b>Символ:</b> {symbol}\n"
            f"<b>Таймфрейм:</b> {tf}\n"
            f"<b>Правило:</b> {rule_id}\n"
            f"<b>Entry:</b> {entry_price:.4f}\n"
            f"<b>Stake:</b> {stake_usd:.2f} USD\n"
            f"<b>Qty:</b> {qty:.8f}\n"
            f"<b>SL:</b> {sl_price:.4f}"
        )

    def format_trade_closed(
        self,
        *,
        trade_id: str,
        user_id: str,
        symbol: str,
        tf: str,
        rule_id: str,
        exit_reason: str,
        entry_price: float,
        exit_price: float,
        realized_pnl_usd: float,
        tp_hit_count: int,
    ) -> str:
        pnl_emoji = "🟩" if realized_pnl_usd > 0 else "🟥"

        return (
            "🔴 <b>Трейд закрито</b>\n\n"
            f"<b>Trade ID:</b> <code>{trade_id}</code>\n"
            f"<b>User:</b> <code>{user_id}</code>\n"
            f"<b>Символ:</b> {symbol}\n"
            f"<b>Таймфрейм:</b> {tf}\n"
            f"<b>Правило:</b> {rule_id}\n"
            f"<b>Причина:</b> {exit_reason}\n"
            f"<b>Entry:</b> {entry_price:.4f}\n"
            f"<b>Exit:</b> {exit_price:.4f}\n"
            f"<b>TP hit count:</b> {tp_hit_count}\n"
            f"<b>PnL:</b> {pnl_emoji} {realized_pnl_usd:.2f} USD"
        )

    def format_stats_summary(self, stats: dict[str, Any], title: str) -> str:
        return (
            f"📊 <b>{title}</b>\n\n"
            f"<b>Trades:</b> {stats.get('total_trades', 0)}\n"
            f"<b>Open:</b> {stats.get('open_trades', 0)}\n"
            f"<b>Closed:</b> {stats.get('closed_trades', 0)}\n"
            f"<b>Wins:</b> {stats.get('wins', 0)}\n"
            f"<b>Losses:</b> {stats.get('losses', 0)}\n"
            f"<b>Winrate:</b> {stats.get('winrate_pct', 0)}%\n"
            f"<b>Start balance:</b> {stats.get('start_balance_usd', 0):.2f} USD\n"
            f"<b>Current balance:</b> {stats.get('current_balance_usd', 0):.2f} USD\n"
            f"<b>Net profit:</b> {stats.get('net_profit_usd', 0):.2f} USD\n"
            f"<b>Profit factor:</b> {stats.get('profit_factor', 0)}"
        )