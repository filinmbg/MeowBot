from datetime import datetime
from bson import ObjectId
from pymongo.errors import PyMongoError
from database.mongo.mongo_connector import get_collection
from database.supabase.trade_logger import insert_trade_log
from core.logger import logger, send_admin_alert
from telegram_bot.notifier import notify_user_open_trade

async def open_trade(user: dict, symbol: str, side: str, price: float, percent: float = 10.0):
    try:
        user_id = user["_id"]
        wallet = user.get("wallet", {})
        balance = wallet.get("balance", 0)

        if balance <= 0:
            raise ValueError("❌ Недостатньо коштів")

        amount_usd = round(balance * percent / 100, 2)
        if amount_usd < 1:
            raise ValueError("❌ Сума занадто мала для відкриття трейду")

        quantity = round(amount_usd / price, 6)

        stop_loss = round(price * 0.98, 2) if side.upper() == "LONG" else round(price * 1.02, 2)
        tp1 = round(price * 1.005, 2) if side.upper() == "LONG" else round(price * 0.995, 2)
        tp2 = round(price * 1.01, 2) if side.upper() == "LONG" else round(price * 0.99, 2)

        trade = {
            "user_id": user_id,
            "symbol": symbol.upper(),
            "side": side.upper(),
            "entry_price": price,
            "amount_usd": amount_usd,
            "quantity": quantity,
            "status": "open",
            "opened_at": datetime.utcnow(),
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
        }
        trades = get_collection("trades")
        users = get_collection("users")

        result = trades.insert_one(trade)
        trade_id = str(result.inserted_id)

        # Зберегти ID трейду в юзера + оновити баланс
        users.update_one(
            {"_id": user_id},
            {
                "$push": {"active_trades": trade_id},
                "$inc": {"wallet.balance": -amount_usd}
            }
        )

        # 📤 Supabase лог — стадія "open"
        insert_trade_log(
            trade_id=trade_id,
            stage="open",
            data={
                "symbol": symbol.upper(),
                "side": side.upper(),
                "entry_price": price,
                "amount_usd": amount_usd,
                "quantity": quantity,
                "user": user.get("username"),
                "wallet_balance_after": balance - amount_usd,
                "stop_loss": stop_loss,
                "tp1": tp1,
                "tp2": tp2,
            }
        )

        logger.info(f"✅ Trade opened for {user['username']} [{symbol} {side}] - ${amount_usd:.2f}")
        await notify_user_open_trade(user_id, symbol.upper(), side.upper(), amount_usd)
        return trade_id

    except ValueError as ve:
        msg = f"⛔ {user['username']}: {ve}"
        logger.warning(msg)
        send_admin_alert(msg)

    except PyMongoError as db_err:
        msg = f"❌ DB Error (open_trade): {db_err}"
        logger.error(msg)
        send_admin_alert(msg)

    except Exception as e:
        msg = f"❌ Unexpected Error (open_trade): {e}"
        logger.error(msg)
        send_admin_alert(msg)


