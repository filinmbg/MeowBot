from __future__ import annotations

from typing import Any

from meowbot.core.configs.strategy_version_test_users import (
    detect_strategy_version,
    normalize_strategy_plan_code,
)


PLAN_ORDER = {"free": 0, "basic": 1, "pro": 2, "vip": 3}


class AdminTestUsersSummaryMessageBuilder:
    def build(
        self,
        rows: list[dict[str, Any]],
        *,
        period_hours: int,
        strategy_summary_rows: list[dict[str, Any]] | None = None,
    ) -> str:
        if not rows:
            return "📋 <b>Тестові юзери summary</b>\n\nДаних немає."

        normalized_rows = [self._normalize_row(row) for row in rows]
        normalized_rows.sort(
            key=lambda row: (
                0 if row["strategy_version"] == "v1" else 1,
                PLAN_ORDER.get(row["plan_code"], 99),
                str(row.get("email") or ""),
            )
        )

        v1_rows = [row for row in normalized_rows if row["strategy_version"] == "v1"]
        v2_rows = [row for row in normalized_rows if row["strategy_version"] == "v2"]
        totals = {
            "v1": self._calculate_totals(v1_rows),
            "v2": self._calculate_totals(v2_rows),
        }

        lines: list[str] = ["📋 <b>Тестові юзери summary</b>", ""]
        lines.append("🔹 <b>Strategy V1</b>")
        lines.extend(self._build_user_rows(v1_rows, empty_text="V1 test users не знайдені."))
        lines.append("")
        lines.append("🔸 <b>Strategy V2 / LONG_BREAKOUT_V18</b>")
        lines.extend(self._build_user_rows(v2_rows, empty_text="V2 test users не знайдені."))
        lines.append("")
        lines.extend(self._build_comparison(totals, period_hours=period_hours))

        return "\n".join(lines)

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        plan_code = normalize_strategy_plan_code(row.get("plan_code") or row.get("subscription_type"))
        strategy_version = detect_strategy_version(
            strategy_version=row.get("strategy_version"),
            plan_code=row.get("plan_code") or row.get("subscription_type"),
            email=row.get("email"),
            username=row.get("username"),
        )
        open_trades = int(row.get("open_trades", 0) or 0)
        closed_trades = int(row.get("closed_trades", 0) or 0)
        wins = int(row.get("wins", 0) or 0)
        losses = int(row.get("losses", 0) or 0)
        pnl = float(row.get("realized_pnl_usd", 0.0) or 0.0)
        start_balance = float(row.get("start_balance_usd", 0.0) or 0.0)
        roi_pct = float(row.get("roi_pct", 0.0) or 0.0)
        if start_balance > 0:
            roi_pct = (pnl / start_balance) * 100.0

        return {
            **row,
            "plan_code": plan_code,
            "strategy_version": strategy_version,
            "open_trades": open_trades,
            "closed_trades": closed_trades,
            "wins": wins,
            "losses": losses,
            "realized_pnl_usd": pnl,
            "start_balance_usd": start_balance,
            "roi_pct": roi_pct,
            "risk_blocks_period": int(row.get("risk_blocks_period", 0) or 0),
        }

    def _build_user_rows(self, rows: list[dict[str, Any]], *, empty_text: str) -> list[str]:
        if not rows:
            return [empty_text]

        lines: list[str] = []
        for row in rows:
            email = str(row.get("email") or "-")
            pnl = float(row["realized_pnl_usd"])
            roi_pct = float(row["roi_pct"])
            open_trades = int(row["open_trades"])
            closed_trades = int(row["closed_trades"])
            wins = int(row["wins"])
            losses = int(row["losses"])
            winrate = (wins / closed_trades * 100.0) if closed_trades else 0.0
            risk_blocks = int(row["risk_blocks_period"])
            lines.append(
                "\n".join(
                    [
                        f"• <b>{email}</b>",
                        f"  Plan: <code>{row['plan_code']}</code> | Strategy: <code>{str(row['strategy_version']).upper()}</code>",
                        f"  PnL: <code>{pnl:.2f} USD</code> | ROI: <code>{roi_pct:.2f}%</code>",
                        f"  Open / Closed: <code>{open_trades}</code> / <code>{closed_trades}</code> | Wins: <code>{wins}</code> | Losses: <code>{losses}</code>",
                        f"  Winrate: <code>{winrate:.2f}%</code> | Risk blocks: <code>{risk_blocks}</code>",
                    ]
                )
            )
            lines.append("")

        if lines and lines[-1] == "":
            lines.pop()
        return lines

    @staticmethod
    def _calculate_totals(rows: list[dict[str, Any]]) -> dict[str, float | int]:
        open_trades = sum(int(row["open_trades"]) for row in rows)
        closed_trades = sum(int(row["closed_trades"]) for row in rows)
        wins = sum(int(row["wins"]) for row in rows)
        losses = sum(int(row["losses"]) for row in rows)
        pnl = sum(float(row["realized_pnl_usd"]) for row in rows)
        start_balance = sum(float(row["start_balance_usd"]) for row in rows)
        risk_blocks = sum(int(row["risk_blocks_period"]) for row in rows)
        winrate = (wins / closed_trades * 100.0) if closed_trades else 0.0
        roi = (pnl / start_balance * 100.0) if start_balance > 0 else 0.0
        return {
            "open_trades": open_trades,
            "closed_trades": closed_trades,
            "trades": open_trades + closed_trades,
            "wins": wins,
            "losses": losses,
            "pnl": pnl,
            "roi": roi,
            "winrate": winrate,
            "risk_blocks": risk_blocks,
        }

    def _build_comparison(
        self,
        totals: dict[str, dict[str, float | int]],
        *,
        period_hours: int,
    ) -> list[str]:
        v1 = totals["v1"]
        v2 = totals["v2"]
        return [
            "📊 <b>V1 vs V2 comparison</b>",
            "<b>V1:</b>",
            f"- Total PnL: <code>{float(v1['pnl']):.2f} USD</code>",
            f"- ROI: <code>{float(v1['roi']):.2f}%</code>",
            f"- Trades: <code>{int(v1['trades'])}</code>",
            f"- Winrate: <code>{float(v1['winrate']):.2f}%</code>",
            f"- Risk blocks ({period_hours}h): <code>{int(v1['risk_blocks'])}</code>",
            "",
            "<b>V2:</b>",
            f"- Total PnL: <code>{float(v2['pnl']):.2f} USD</code>",
            f"- ROI: <code>{float(v2['roi']):.2f}%</code>",
            f"- Trades: <code>{int(v2['trades'])}</code>",
            f"- Winrate: <code>{float(v2['winrate']):.2f}%</code>",
            f"- Risk blocks ({period_hours}h): <code>{int(v2['risk_blocks'])}</code>",
            "",
            "<b>Winner:</b>",
            f"- by PnL: <code>{self._winner(float(v1['pnl']), float(v2['pnl']))}</code>",
            f"- by ROI: <code>{self._winner(float(v1['roi']), float(v2['roi']))}</code>",
            f"- by winrate: <code>{self._winner(float(v1['winrate']), float(v2['winrate']))}</code>",
        ]

    @staticmethod
    def _winner(v1_value: float, v2_value: float) -> str:
        if v1_value > v2_value:
            return "V1"
        if v2_value > v1_value:
            return "V2"
        return "Tie"
