import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv
from core.logger import logger, send_admin_alert
from database.mongo.mongo_connector import init_mongo
from database.supabase.supabase_connector import init_supabase
from telegram_bot.telegram_bot import main as telegram_main
from trader.check_exit import check_exit_conditions

# 👇 додано
from Loader.mongo_check import run_check as run_mongo_check
from Loader.loader import loader_main

load_dotenv()

signal_model = {}  # якщо потрібно передати сигнали — заповни тут {"BTCUSDT": "SHORT", ...}

async def trading_loop():
    logger.info("📈 Трейдинг цикл запущено")
    try:
        while True:
            start = datetime.utcnow()
            logger.info(f"⏱ Цикл перевірки трейдів: {start.isoformat()}")
            await check_exit_conditions(signal_model)
            duration = (datetime.utcnow() - start).total_seconds()
            await asyncio.sleep(max(0, 60 - duration))
    except Exception as e:
        logger.error(f"❌ trading_loop помилка: {e}")
        send_admin_alert(f"❌ trading_loop помилка: {e}")

async def main():
    await init_mongo()
    await init_supabase()

    # 👇 перевірка і початкове завантаження
    await run_mongo_check()        # виконує перевірку і дозавантаження барів
    loader_task = asyncio.create_task(loader_main())  # запускає оновлення барів

    # 👇 Telegram бот
    bot_task = asyncio.create_task(telegram_main())

    # 👇 трейдинг логіка
    await trading_loop()

if __name__ == "__main__":
    try:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("🛑 Зупинено вручну")
