# test/mongo_setup_async.py
import os
import asyncio
from pymongo import ASCENDING
from pymongo.errors import ServerSelectionTimeoutError

# 🔌 твій конектор
from database.mongo.connection import init_mongo, get_db, is_connected

# опціонально: підставимо "test" як дефолт
os.environ.setdefault("MONGO_DB_NAME", "test")

async def main():
    print(f"[INFO] init_mongo() with MONGO_URI={os.getenv('MONGO_URI','<unset>')} "
          f"MONGO_DB_NAME={os.getenv('MONGO_DB_NAME','<unset>')}")
    await init_mongo()  # читає із .env (MONGO_URI, MONGO_DB_NAME)

    if not is_connected():
        raise RuntimeError("Mongo не ініціалізовано. Перевір .env (MONGO_URI, MONGO_DB_NAME).")

    db = get_db()

    bars = db["bars"]
    await bars.create_index("_id", unique=True)
    await bars.create_index([("symbol", ASCENDING), ("tf", ASCENDING), ("close_time", ASCENDING)])

    # symbols — щоб вибірка активних працювала швидко
    symbols = db["symbols"]
    await symbols.create_index([("symbol", ASCENDING)], unique=True)
    await symbols.create_index([("active", ASCENDING), ("symbol", ASCENDING)])

    print("[OK] Indexes created on collections: bars, symbols")

if __name__ == "__main__":
    asyncio.run(main())
