from __future__ import annotations


class StatsMessageBuilder:
    def build(self, stats: dict, title: str, no_data_text: str) -> str:
        if stats.get("total", 0) == 0:
            return f"📊 <b>{title}</b>\n\n{no_data_text}"

        return (
            f"📊 <b>{title}</b>\n\n"
            f"<b>Trades:</b> {stats['total']}\n"
            f"<b>Wins:</b> {stats['wins']}\n"
            f"<b>Losses:</b> {stats['losses']}\n"
            f"<b>Winrate:</b> {stats['winrate']:.2f}%\n"
            f"<b>PnL:</b> {stats['pnl']:.2f} USD"
        )