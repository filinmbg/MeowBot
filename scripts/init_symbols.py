# scripts/init_symbols.py
import sys
import time
import asyncio
from pathlib import Path

from dotenv import load_dotenv

# >>> додамо корінь проєкту в sys.path, щоб імпорти типу "database.mongo.connection" працювали
ROOT = Path(__file__).resolve().parents[1]   # D:/Project/MeowBot
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# підхопимо .env з кореня
load_dotenv(ROOT / ".env")

from database.mongo.connection import init_mongo, get_db  # тепер імпорт знайдеться

DOCS = [
    {
        "symbol": "BTCUSDT",
        "base": "BTC",
        "quote": "USDT",
        "active": True,
        "source": "manual",
        "ts": int(time.time() * 1000),
    }
]

async def main():
    await init_mongo(bot=None, admin_chat_id=None)
    db = get_db()
    col = db["symbols"]

    # унікальний індекс, щоб не дублювати
    await col.create_index("symbol", unique=True, name="uniq_symbol")

    # upsert BTCUSDT
    for d in DOCS:
        await col.update_one({"symbol": d["symbol"]}, {"$setOnInsert": d}, upsert=True)

    total = await col.count_documents({})
    print(f"✅ symbols collection ready. total={total}")

if __name__ == "__main__":
    asyncio.run(main())
