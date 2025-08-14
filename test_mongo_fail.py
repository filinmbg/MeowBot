import asyncio
import os
import logging
from dotenv import load_dotenv
from telegram import Bot

from database.mongo.connection import init_mongo, is_connected

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

async def main():
    load_dotenv()

    token = os.getenv("TELEGRAM_TOKEN")
    admin_id = os.getenv("ADMIN_CHAT_ID")

    if not token:
        raise RuntimeError("TELEGRAM_TOKEN не знайдено у .env")
    if not admin_id or not admin_id.isdigit():
        raise RuntimeError("ADMIN_CHAT_ID не задано або не число у .env")

    bot = Bot(token=token)

    # Навмисно ПЛОХИЙ URI (автентифікація завалиться)
    bad_uri = "mongodb+srv://bad_user:bad_pass@bad-cluster.mongodb.net/?retryWrites=true&w=majority"
    bad_db = "meowbot"

    print("▶️ Імітую невдале підключення до Mongo...")
    await init_mongo(
        bot=bot,
        admin_chat_id=int(admin_id),
        mongo_uri=bad_uri,
        mongo_db_name=bad_db,
    )

    if is_connected():
        print("⚠️ НЕОЧІКУВАНО: підключення успішне (мало впасти). Перевір тестовий URI.")
    else:
        print("✅ Помилка підключення зімітувалась. Перевір повідомлення в Telegram у адміну.")

if __name__ == "__main__":
    asyncio.run(main())
