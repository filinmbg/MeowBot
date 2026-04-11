class TelegramTradeNotifyService:
    def format_open(self, trade):
        return (
            f"🟢 OPEN {trade.symbol} {trade.timeframe}\n"
            f"Rule: {trade.rule_id}\n"
            f"Entry: {trade.entry_price}"
        )

    def format_close(self, trade):
        return (
            f"🔴 CLOSE {trade.symbol} {trade.timeframe}\n"
            f"Rule: {trade.rule_id}\n"
            f"PnL: {round(trade.pnl * 100, 2)}%"
        )