import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv

from database.mongo.connection import init_mongo, get_db, is_connected

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

async def main():
    load_dotenv()

    # 1) Піднімаємо єдине підключення (без Telegram-сповіщень у тесті)
    await init_mongo(bot=None, admin_chat_id=None)

    if not is_connected():
        print("❌ Підключення НЕ встановлено. Перевір MONGO_URI/MONGO_DB_NAME у .env")
        return

    print("✅ Конект встановлено. Запускаю CRUD-тест…")
    db = get_db()
    col = db["users"]  # використовуємо наявну колекцію

    # 2) INSERT
    doc = {
        "username": "test_user",
        "role": "tester",
        "created_at": datetime.utcnow(),
        "note": "inserted_by_crud_test",
    }
    ins = await col.insert_one(doc)
    print(f"➕ Вставлено документ _id={ins.inserted_id}")

    # 3) FIND (перевірка вставки)
    found = await col.find_one({"_id": ins.inserted_id})
    print(f"🔎 Знайдено: {found}")

    # 4) UPDATE
    upd = await col.update_one({"_id": ins.inserted_id}, {"$set": {"role": "tester_updated"}})
    print(f"✏️ Оновлено документів: matched={upd.matched_count}, modified={upd.modified_count}")

    found2 = await col.find_one({"_id": ins.inserted_id})
    print(f"🔎 Після оновлення: {found2}")

    # 5) DELETE (очищення за собою)
    dele = await col.delete_one({"_id": ins.inserted_id})
    print(f"🗑️ Видалено документів: {dele.deleted_count}")

    print("🎉 CRUD-тест пройдено.")

if __name__ == "__main__":
    asyncio.run(main())
