import os
from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorClient
from core.logger import logger
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("MONGO_DB", "meowbot")

client = None
db = None
database = None

async def init_mongo():
    global client, db
    try:
        client = AsyncIOMotorClient(MONGO_URI)
        db = client[DB_NAME]
        logger.info(f"✅ Connected to MongoDB: {MONGO_URI} (DB: {DB_NAME})")
    except Exception as e:
        logger.error(f"❌ MongoDB connection error: {e}")
        raise e

def get_db():
    global db
    return db

# приклад асинхронного використання
async def count_bars(symbol: str, interval: str) -> int:
    collection = db[f"{symbol}_{interval}"]
    return await collection.count_documents({})

async def insert_if_new(symbol: str, interval: str, df: pd.DataFrame) -> bool:
    collection = db[f"{symbol}_{interval}"]
    if df.empty:
        return False
    last_bar_time = df.index[-1]
    exists = await collection.find_one({"open_time": last_bar_time})
    if exists:
        return False
    records = df.reset_index().to_dict(orient="records")
    await collection.insert_many(records)
    return True

def get_collection(symbol: str, interval: str):
    global db
    return db[f"{symbol}_{interval}"]

async def insert_bars(symbol: str, interval: str, records: list[dict]):
    global db
    if db is None:
        raise RuntimeError("MongoDB is not initialized.")

    collection_name = f"{symbol}_{interval}"
    collection = db[collection_name]

    if not records:
        logger.warning(f"🚫 No bars to insert for {symbol} [{interval}]")
        return

    try:
        await collection.insert_many(records)
        logger.info(f"✅ Inserted {len(records)} bars for {symbol} [{interval}]")
    except Exception as e:
        logger.error(f"❌ Failed to insert bars for {symbol} [{interval}]: {e}")

async def delete_bars(symbol: str, interval: str):
    global db
    if db is None:
        raise RuntimeError("MongoDB is not initialized.")

    collection_name = f"{symbol}_{interval}"
    collection = db[collection_name]

    try:
        result = await collection.delete_many({})
        logger.info(f"🗑 Видалено {result.deleted_count} барів з {symbol} [{interval}]")
    except Exception as e:
        logger.error(f"❌ Помилка видалення барів {symbol} [{interval}]: {e}")

async def insert_trade_signal_log(symbol: str, interval: str, model_name: str, prediction: str):
    collection = get_collection_by_interval(symbol, interval)
    doc = {
        "type": "signal_log",
        "symbol": symbol,
        "interval": interval,
        "model": model_name,
        "prediction": prediction,
        "timestamp": datetime.utcnow()
    }
    await collection.insert_one(doc)


def get_collection_by_interval(symbol: str, interval: str):
    if not database:
        raise ValueError("⛔ MongoDB не ініціалізовано")

    collection_name = f"{symbol}_{interval}".lower()
    return database[collection_name]

def init_database(client, db_name):
    global database
    database = client[db_name]