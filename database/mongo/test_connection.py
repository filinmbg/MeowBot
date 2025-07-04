from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGO_DB_NAME", "meowbot")

try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    db = client[DB_NAME]

    # Спроба отримати інформацію про сервер
    info = client.server_info()
    print("✅ Підключення до MongoDB успішне!")
    print("Версія сервера:", info["version"])

    # Перевіримо наявні колекції
    collections = db.list_collection_names()
    print("Колекції в базі:", collections)

except Exception as e:
    print("❌ Помилка підключення до MongoDB:")
    print(e)
