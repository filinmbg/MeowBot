import os
import logging
from typing import Optional, Tuple

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

__all__ = [
    "init_mongo",
    "get_client",
    "get_db",
    "is_connected",
]

log = logging.getLogger("meowbot.mongo")

# ---- Singleton state ----
_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None
_inited: bool = False
_cached_conf: Optional[Tuple[str, str]] = None  # (MONGO_URI, MONGO_DB_NAME)
_notified_failure: bool = False  # щоб не спамити адміну при багаторазових спробах


async def init_mongo(
    *,
    bot=None,                  # telegram.Bot або application.bot
    admin_chat_id: Optional[int] = None,
    mongo_uri: Optional[str] = None,
    mongo_db_name: Optional[str] = None,
) -> None:
    """
    Ініціалізує глобальне підключення до MongoDB (Motor, async).
    Якщо помилка — один раз надсилає повідомлення адміну (якщо admin_chat_id задано).
    Інші модулі мають користуватися ТІЛЬКИ get_db()/get_client(), нових клієнтів не створювати.
    """
    global _client, _db, _inited, _cached_conf, _notified_failure

    if _inited and _client and _db:
        # Уже ініціалізовано — перевіримо, чи не змінилась конфігурація
        if mongo_uri or mongo_db_name:
            current = _cached_conf or ("", "")
            new_conf = (mongo_uri or current[0], mongo_db_name or current[1])
            if new_conf != current:
                raise RuntimeError(
                    "Mongo вже ініціалізовано. Заборонено переініціалізацію з іншою конфігурацією."
                )
        return

    # Завантажимо .env на випадок, якщо ще не завантажений
    load_dotenv()

    uri = (mongo_uri or os.getenv("MONGO_URI") or "").strip()
    dbname = (mongo_db_name or os.getenv("MONGO_DB_NAME") or "").strip()

    if not uri or not dbname:
        msg = "❌ MONGO_URI або MONGO_DB_NAME не задані у .env"
        print(msg)
        log.error(msg)
        await _maybe_notify_admin(bot, admin_chat_id, f"⚠️ MongoDB init failed: {msg}")
        return

    try:
        _client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        _db = _client[dbname]

        # Перевірка реального конекту
        await _db.command("ping")

        _cached_conf = (uri, dbname)
        _inited = True
        _notified_failure = False

        ok_msg = f"✅ Підключено до MongoDB: db='{dbname}'"
        print(ok_msg)
        log.info(ok_msg)

    except Exception as e:
        _client = None
        _db = None
        _inited = False

        err = f"❌ Помилка підключення до MongoDB: {e!r}"
        print(err)
        log.exception("Mongo init error")
        await _maybe_notify_admin(
            bot,
            admin_chat_id,
            "⚠️ Не вдалося підключитися до MongoDB. Перевірте MONGO_URI/MONGO_DB_NAME та доступ.\n"
            f"Технічні деталі: <code>{type(e).__name__}: {str(e)}</code>",
        )


def is_connected() -> bool:
    return _inited and (_client is not None) and (_db is not None)


def get_client() -> AsyncIOMotorClient:
    if not is_connected():
        raise RuntimeError("Mongo не ініціалізовано. Викличте init_mongo() на старті бота.")
    return _client  # type: ignore[return-value]


def get_db() -> AsyncIOMotorDatabase:
    if not is_connected():
        raise RuntimeError("Mongo не ініціалізовано. Викличте init_mongo() на старті бота.")
    return _db  # type: ignore[return-value]


async def _maybe_notify_admin(bot, admin_chat_id: Optional[int], text: str) -> None:
    """
    Надіслати повідомлення адміну один раз при фейлі.
    """
    global _notified_failure
    if _notified_failure:
        return
    if bot is None or not admin_chat_id:
        return
    try:
        # HTML дозволено, але без зайвих форматів
        await bot.send_message(chat_id=admin_chat_id, text=text, parse_mode="HTML")
        _notified_failure = True
    except Exception:
        # Не можемо написати адміну — просто залогимо
        log.warning("Не вдалося надіслати повідомлення адміну про помилку Mongo", exc_info=True)
