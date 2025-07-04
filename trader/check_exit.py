from database.mongo.mongo_connector import get_collection
from binance_connector.client import get_price
from wallet.close_trade import close_trade
from database.supabase.trade_logger import insert_trade_log
from core.logger import logger, send_admin_alert
from utils.semaphore import GlobalSemaphore
from telegram_bot.notifier import notify_user_tp, notify_user_sl

semaphore = GlobalSemaphore(max_concurrent=5)


async def fetch_open_trades():
    try:
        trades_col = get_collection("trades", interval="global")
        return [doc async for doc in trades_col.find({"status": "open"})]
    except Exception as e:
        msg = f"❌ Помилка отримання відкритих трейдів: {e}"
        logger.error(msg)
        send_admin_alert(msg)
        return []


async def fetch_open_trades():
    try:
        trades_col = get_collection("trades", interval="global")
        return [doc async for doc in trades_col.find({"status": "open"})]
    except Exception as e:
        msg = f"❌ Помилка отримання відкритих трейдів: {e}"
        logger.error(msg)
        send_admin_alert(msg)
        return []


def handle_reverse_signal(trade, signal_model, trades_col, price):
    trade_id = str(trade["_id"])
    symbol = trade["symbol"]
    side = trade["side"]
    stop_loss = trade.get("stop_loss", trade["entry_price"] * 0.98)

    if signal_model and signal_model.get(symbol) and signal_model[symbol] != side:
        new_sl = round(price * 0.998, 2) if side == "LONG" else round(price * 1.002, 2)
        if new_sl != stop_loss:
            trades_col.update_one({"_id": trade["_id"]}, {"$set": {"stop_loss": new_sl}})
            insert_trade_log(trade_id, "update", {
                "type": "reversed_signal",
                "new_stop_loss": new_sl,
                "price": price
            })
            logger.info(f"⚠️ {symbol} SL оновлено через сигнал: {new_sl}")
            trade["stop_loss"] = new_sl


async def check_take_profit(trade, price, trades_col):
    trade_id = str(trade["_id"])
    side = trade["side"]
    entry = trade["entry_price"]
    symbol = trade["symbol"]
    user_id = trade.get("user_id")

    tp_1 = trade.get("tp_1_done", False)
    tp_2 = trade.get("tp_2_done", False)

    if not tp_1:
        target = entry * 1.005 if side == "LONG" else entry * 0.995
        if (side == "LONG" and price >= target) or (side == "SHORT" and price <= target):
            trades_col.update_one({"_id": trade["_id"]}, {
                "$set": {"tp_1_done": True, "stop_loss": entry},
                "$inc": {"closed_percent": 25}
            })
            insert_trade_log(trade_id, "update", {
                "type": "tp_1",
                "price": price,
                "new_stop_loss": entry
            })
            logger.info(f"🎯 TP1 виконано для {symbol}")
            trade["tp_1_done"] = True
            trade["stop_loss"] = entry
            if user_id:
                await notify_user_tp(user_id, symbol, 1, price)

    elif tp_1 and not tp_2:
        target = entry * 1.01 if side == "LONG" else entry * 0.99
        if (side == "LONG" and price >= target) or (side == "SHORT" and price <= target):
            trades_col.update_one({"_id": trade["_id"]}, {
                "$set": {"tp_2_done": True, "stop_dynamic": True},
                "$inc": {"closed_percent": 25}
            })
            insert_trade_log(trade_id, "update", {
                "type": "tp_2",
                "price": price,
                "stop_dynamic": True
            })
            logger.info(f"🎯 TP2 виконано для {symbol}")
            trade["tp_2_done"] = True
            trade["stop_dynamic"] = True
            if user_id:
                await notify_user_tp(user_id, symbol, 2, price)


def adjust_dynamic_sl(trade, price, trades_col):
    if not trade.get("stop_dynamic"):
        return

    trade_id = str(trade["_id"])
    side = trade["side"]
    stop_loss = trade.get("stop_loss")

    diff = abs((price - stop_loss) / price)
    if diff < 0.005:
        return

    sl_move = price * (0.01 / 100)
    new_sl = stop_loss
    if side == "LONG":
        new_sl = max(stop_loss, stop_loss + sl_move)
    else:
        new_sl = min(stop_loss, stop_loss - sl_move)

    if new_sl != stop_loss:
        trades_col.update_one({"_id": trade["_id"]}, {"$set": {"stop_loss": round(new_sl, 2)}})
        insert_trade_log(trade_id, "update", {
            "type": "dynamic_sl_adjust",
            "old_sl": stop_loss,
            "new_sl": round(new_sl, 2),
            "price": price
        })
        logger.info(f"🔁 SL оновлено динамічно: {round(new_sl, 2)}")
        trade["stop_loss"] = new_sl


def check_stop_loss(trade, price):
    side = trade["side"]
    stop_loss = trade.get("stop_loss")

    if stop_loss is None:
        logger.warning(f"⛔️ У трейда {trade['_id']} відсутній stop_loss. Пропускаємо перевірку.")
        return False

    if side == "LONG" and price <= stop_loss:
        return True
    if side == "SHORT" and price >= stop_loss:
        return True
    return False


async def process_trade(trade, signal_model):
    try:
        trades_col = get_collection("trades", interval="global")
        symbol = trade["symbol"]
        price = await get_price(symbol)
        if price is None:
            return

        handle_reverse_signal(trade, signal_model, trades_col, price)
        await check_take_profit(trade, price, trades_col)
        adjust_dynamic_sl(trade, price, trades_col)

        if check_stop_loss(trade, price):
            logger.warning(f"🛑 SL досягнуто для {symbol}. Закриваємо трейд.")
            await close_trade(str(trade["_id"]), price)
            if trade.get("user_id"):
                await notify_user_sl(trade["user_id"], symbol, price)

    except Exception as e:
        msg = f"❌ process_trade() помилка: {e}"
        logger.error(msg)
        send_admin_alert(msg)


async def check_exit_conditions(signal_model: dict = None):
    trades = await fetch_open_trades()
    for trade in trades:
        await semaphore.run(process_trade(trade, signal_model))
