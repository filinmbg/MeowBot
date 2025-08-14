import asyncio
import logging
from dotenv import load_dotenv
from database.mongo.connection import init_mongo, get_db, is_connected

# Логування для наочності
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

async def main():
    load_dotenv()

    # Ініціалізація Mongo (без Telegram-бота, адмін-id = None)
    await init_mongo(
        bot=None,
        admin_chat_id=None
    )

    if not is_connected():
        print("❌ Не вдалося підключитися до MongoDB")
        return

    print("✅ Підключення успішне!")

    # Тестовий запит до бази
    db = get_db()
    collections = await db.list_collection_names()
    print(f"📦 Список колекцій: {collections}")

if __name__ == "__main__":
    asyncio.run(main())
