from datetime import datetime
from database.supabase.supabase_connector import supabase
from core.logger import logger, send_admin_alert


def insert_trade_log(trade_id: str, stage: str, data: dict):
    """
    Запис у Supabase: trades/{trade_id}_{stage}
    Наприклад: trades/BTCUSDT_long_1_open
    """
    try:
        entry = {
            "trade_id": trade_id,
            "stage": stage,
            "user_id": str(data.get("user_id")),
            "symbol": data.get("symbol"),
            "side": data.get("side"),
            "entry_price": data.get("entry_price"),
            "amount_usd": data.get("amount_usd"),
            "quantity": data.get("quantity"),
            "status": data.get("status"),
            "closed_percent": data.get("closed_percent", 0),
            "stop_loss": data.get("stop_loss"),
            "tp_1_done": data.get("tp_1_done", False),
            "tp_2_done": data.get("tp_2_done", False),
            "stop_dynamic": data.get("stop_dynamic", False),
            "timestamp": datetime.utcnow().isoformat()
        }
        response = supabase.table("trades").insert(entry).execute()
        logger.info(f"📝 Trade log [{stage}] збережено: {trade_id}")
        return response

    except Exception as e:
        msg = f"❌ Supabase insert_trade_log({stage}) error: {e}"
        logger.error(msg)
        send_admin_alert(msg)
