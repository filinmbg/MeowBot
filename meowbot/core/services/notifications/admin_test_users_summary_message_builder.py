from __future__ import annotations

from typing import Any


class AdminTestUsersSummaryMessageBuilder:
    def build(self, rows: list[dict[str, Any]], *, period_hours: int) -> str:
        if not rows:
            return "📊 <b>Тестові юзери</b>\n\nДаних немає."

        lines: list[str] = []
        lines.append(f"📊 <b>Зведення по тестових юзерах</b>")
        lines.append(f"Період risk blocks: <b>{period_hours}h</b>")
        lines.append("")

        total_open = 0
        total_closed = 0
        total_wins = 0
        total_losses = 0
        total_pnl = 0.0
        total_start_balance = 0.0
        total_risk_blocks = 0

        for row in rows:
            email = row.get("email") or "-"
            name = row.get("display_name") or "-"
            plan = row.get("plan_code") or "-"
            mode = row.get("trading_mode") or "-"
            open_trades = int(row.get("open_trades", 0) or 0)
            closed_trades = int(row.get("closed_trades", 0) or 0)
            wins = int(row.get("wins", 0) or 0)
            losses = int(row.get("losses", 0) or 0)
            pnl = float(row.get("realized_pnl_usd", 0.0) or 0.0)
            balance = float(row.get("current_balance_usd", 0.0) or 0.0)
            start_balance = float(row.get("start_balance_usd", 0.0) or 0.0)
            roi_pct = float(row.get("roi_pct", 0.0) or 0.0)
            cooldowns = int(row.get("active_cooldowns", 0) or 0)
            risk_blocks = int(row.get("risk_blocks_period", 0) or 0)

            winrate = 0.0
            if closed_trades > 0:
                winrate = (wins / closed_trades) * 100.0

            lines.append(
                "\n".join(
                    [
                        f"👤 <b>{email}</b>",
                        f"Name: <code>{name}</code>",
                        f"Plan: <code>{plan}</code> | Mode: <code>{mode}</code>",
                        f"Open: <code>{open_trades}</code> | Closed: <code>{closed_trades}</code>",
                        f"Wins: <code>{wins}</code> | Losses: <code>{losses}</code> | Winrate: <code>{winrate:.2f}%</code>",
                        f"PnL: <code>{pnl:.2f}$</code> | Balance: <code>{balance:.2f}$</code> | ROI: <code>{roi_pct:.2f}%</code>",
                        f"Cooldowns: <code>{cooldowns}</code> | Risk blocks ({period_hours}h): <code>{risk_blocks}</code>",
                    ]
                )
            )
            lines.append("")

            total_open += open_trades
            total_closed += closed_trades
            total_wins += wins
            total_losses += losses
            total_pnl += pnl
            total_start_balance += start_balance
            total_risk_blocks += risk_blocks

        total_roi = 0.0
        if total_start_balance > 0:
            total_roi = (total_pnl / total_start_balance) * 100.0

        total_winrate = 0.0
        if total_closed > 0:
            total_winrate = (total_wins / total_closed) * 100.0

        lines.append("────────")
        lines.append("<b>Загальний підсумок</b>")
        lines.append(
            f"Open: <code>{total_open}</code> | Closed: <code>{total_closed}</code>"
        )
        lines.append(
            f"Wins: <code>{total_wins}</code> | Losses: <code>{total_losses}</code> | Winrate: <code>{total_winrate:.2f}%</code>"
        )
        lines.append(
            f"PnL: <code>{total_pnl:.2f}$</code> | ROI: <code>{total_roi:.2f}%</code> | Risk blocks ({period_hours}h): <code>{total_risk_blocks}</code>"
        )

        return "\n".join(lines)