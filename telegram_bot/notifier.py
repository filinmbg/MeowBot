# telegram_bot/notifier.py

import os
import httpx
from core.logger import logger, send_admin_alert
from database.mongo.mongo_connector import get_collection
from database.mongo.user_manager import get_user_by_id

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

async def send_telegram_message(chat_id: int, text: str):
    try:
        async with httpx.AsyncClient() as client:
            await client.post(BASE_URL, json={"chat_id": chat_id, "text": text})
    except Exception as e:
        logger.warning(f"⚠️ Помилка надсилання повідомлення в Telegram: {e}")
        send_admin_alert(f"Telegram error: {e}")

def get_user_chat_id(user_id):
    try:
        users = get_collection("users")
        user = users.find_one({"_id": user_id})
        return user.get("telegram_id")
    except Exception as e:
        logger.warning(f"⚠️ Не вдалося отримати telegram_id для користувача {user_id}: {e}")
        return None

async def notify_user_open_trade(user_id, symbol, side, amount):
    chat_id = get_user_chat_id(user_id)
    if chat_id:
        text = f"📈 Відкрито трейд:\nМонета: {symbol}\nНапрям: {side}\nСума: ${amount:.2f}"
        await send_telegram_message(chat_id, text)

async def notify_user_tp(user_id, symbol, level, price):
    chat_id = get_user_chat_id(user_id)
    if chat_id:
        text = f"🎯 Досягнуто TP{level} для {symbol}!\nЦіна: {price}"
        await send_telegram_message(chat_id, text)

async def notify_user_sl(user_id, symbol, price):
    chat_id = get_user_chat_id(user_id)
    if chat_id:
        text = f"🛑 Stop Loss спрацював для {symbol}\nЦіна: {price}"
        await send_telegram_message(chat_id, text)

async def notify_user_trade_update(user_id: str, message: str):
    try:
        chat_id = await get_chat_id_by_user_id(user_id)
        if chat_id:
            await bot.send_message(chat_id=chat_id, text=message)
        else:
            logger.warning(f"❗️ Chat ID not found for user {user_id}")
    except Exception as e:
        logger.error(f"❌ Telegram notify error: {e}")

async def get_chat_id_by_user_id(user_id: str):
    user = await get_user_by_id(user_id)
    return user.get("telegram_id") if user else None