from datetime import datetime
from bson import ObjectId
from pymongo.errors import PyMongoError
from database.mongo.mongo_connector import get_collection
from database.supabase.trade_logger import insert_trade_log
from core.logger import logger, send_admin_alert

async def close_trade(trade_id: str, close_price: float):
    """
    Закриває трейд:
    - оновлює статус у Mongo
    - повертає кошти у гаманець
    - додає лог у Supabase (stage = "close")
    """
    try:
        trades = get_collection("trades")
        users = get_collection("users")

        trade = await trades.find_one({"_id": ObjectId(trade_id)})
        if not trade:
            raise ValueError("Трейд не знайдено")

        if trade["status"] != "open":
            raise ValueError("Трейд уже закритий")

        user_id = trade["user_id"]
        entry_price = trade["entry_price"]
        quantity = trade["quantity"]
        side = trade["side"]
        amount_usd = trade["amount_usd"]

        pnl = 0
        if side == "LONG":
            pnl = (close_price - entry_price) * quantity
        elif side == "SHORT":
            pnl = (entry_price - close_price) * quantity

        total_return = round(amount_usd + pnl, 2)

        # Оновлюємо трейд
        await trades.update_one(
            {"_id": ObjectId(trade_id)},
            {
                "$set": {
                    "status": "closed",
                    "closed_at": datetime.utcnow(),
                    "close_price": close_price,
                    "pnl": round(pnl, 2),
                    "final_amount": total_return
                }
            }
        )

        # Повертаємо кошти
        await users.update_one(
            {"_id": user_id},
            {
                "$pull": {"active_trades": trade_id},
                "$inc": {"wallet.balance": total_return}
            }
        )

        # Supabase лог
        await insert_trade_log(
            trade_id=trade_id,
            stage="close",
            data={
                "symbol": trade["symbol"],
                "side": trade["side"],
                "entry_price": entry_price,
                "close_price": close_price,
                "quantity": quantity,
                "amount_usd": amount_usd,
                "pnl": round(pnl, 2),
                "final_amount": total_return
            }
        )

        logger.info(f"✅ Trade {trade_id} closed. PnL: ${round(pnl, 2)}")
        return pnl

    except Exception as e:
        msg = f"❌ Error closing trade {trade_id}: {e}"
        logger.error(msg)
        send_admin_alert(msg)
