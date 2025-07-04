from pymongo import MongoClient, errors
from datetime import datetime
import os
import logging
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("UserManager")

MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise ValueError("❌ MONGO_URI не знайдено в .env")

DB_NAME = os.getenv("MONGO_DB_NAME", "meowbot")

try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    db = client[DB_NAME]
    users = db["users"]
    logger.info(f"✅ Connected to MongoDB [users]")
except Exception as e:
    logger.critical(f"❌ MongoDB connection failed: {e}")
    raise SystemExit(1)


def get_or_create_user(telegram_user: dict) -> dict:
    """
    Створює нового користувача або повертає існуючого.
    Перший користувач стає admin. Telegram user = dict з id, username тощо.
    """
    try:
        # === Перевірка вхідних даних
        telegram_id = telegram_user.get("id")
        if telegram_id is None:
            raise ValueError("❌ 'telegram_user' не містить поля 'id'")

        username = telegram_user.get("username") or f"user_{telegram_id}"

        # === Пошук існуючого
        existing = users.find_one({"telegram_id": telegram_id})
        if existing:
            logger.info(f"🔁 Користувач уже існує: @{username}")
            return existing

        # === Перевірка на наявність адміністратора
        try:
            has_admin = users.find_one({"role": "admin"})
        except Exception as e:
            logger.warning(f"⚠️ Не вдалося перевірити admin статус: {e}")
            has_admin = True  # уникаємо призначення admin

        role = "admin" if not has_admin else "user"

        # === Формування профілю
        new_user = {
            "telegram_id": telegram_id,
            "username": username,
            "role": role,
            "subscription": "free",
            "created_at": datetime.utcnow(),

            "wallet": {
                "balance": 1000.0,
                "currency": "USDT"
            },

            "active_trades": [],
            "active_bots": ["SAFE"],

            "trade_settings": {
                "entry_mode": "percent",     # or 'fixed'
                "entry_value": 10.0,         # 10% або $10
                "max_open_trades": 1
            },

            "notifications": {
                "enabled": True,
                "send_signals": True,
                "send_results": True
            },

            "stats": {
                "total_trades": 0,
                "total_profit": 0.0,
                "total_loss": 0.0
            }
        }

        result = users.insert_one(new_user)
        logger.info(f"✅ Новий користувач створений: @{username} (роль: {role})")
        return users.find_one({"_id": result.inserted_id})

    except errors.PyMongoError as db_err:
        logger.error(f"❌ MongoDB error: {db_err}")
        raise

    except Exception as e:
        logger.error(f"❌ Створення користувача зірвалося: {e}")
        raise

def get_user_by_id(user_id: str) -> dict | None:
    """
    Повертає користувача по telegram_id (user_id).
    """
    try:
        return users.find_one({"telegram_id": user_id})
    except errors.PyMongoError as e:
        logger.error(f"❌ MongoDB error в get_user_by_id: {e}")
        return None