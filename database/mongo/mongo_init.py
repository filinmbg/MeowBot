from pymongo import MongoClient
from loguru import logger
import os

# === 1. Підключення
client = MongoClient(os.getenv("MONGODB_URI") or "mongodb://localhost:27017/")
db = client["meowbot"]

logger.info(f"✅ Connected to MongoDB (from mongo_init)")

# === 2. Функції

def get_db():
    return db

def get_collection(name: str):
    return db[name]
