# main.py
import os
import sys
import uuid
import asyncio
import inspect
import traceback
from dotenv import load_dotenv

load_dotenv()
RETRY_ATTEMPTS = int(os.getenv("DB_RETRY_ATTEMPTS", "5"))
RETRY_BASE_SEC = float(os.getenv("DB_RETRY_BASE_SEC", "1.0"))

TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# ✅ приймаємо і SUPABASE_KEY, і SUPABASE_API_KEY
SB_URL = (os.getenv("SUPABASE_URL") or "").strip()
SB_KEY = (os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_API_KEY") or "").strip()
USE_SUPABASE = bool(SB_URL and SB_KEY)


def incident_code() -> str:
    return uuid.uuid4().hex[:8].upper()


async def notify_admin(subject: str, message: str) -> None:
    if TG_TOKEN and TG_CHAT_ID:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
                payload = {"chat_id": TG_CHAT_ID, "text": f"*{subject}*\n{message}", "parse_mode": "Markdown"}
                r = await client.post(url, json=payload)
                r.raise_for_status()
                return
        except Exception as e:
            print(f"[warn] Telegram notify failed: {e}", file=sys.stderr)
    print(f"[ADMIN NOTIFY]\n{subject}\n{message}", file=sys.stderr)


async def retry_check(name: str, fn, attempts: int, base_sec: float) -> bool:
    for i in range(1, attempts + 1):
        try:
            res = fn()
            ok = await res if inspect.isawaitable(res) else res
            if ok:
                print(f"✅ {name} connected")
                return True
        except Exception as e:
            delay = base_sec * (2 ** (i - 1))
            print(f"❌ {name} attempt {i}/{attempts} failed: {e}")
            if i < attempts:
                print(f"   ↳ retrying in {delay:.1f}s ...")
                await asyncio.sleep(delay)
            else:
                tb = traceback.format_exc()
                inc = incident_code()
                await notify_admin(f"[{name}] connection failed (incident {inc})", f"{e}\n\n{tb}")
                return False
    return False


async def check_mongo() -> bool:
    from database.mongo.connection import init_mongo, is_connected
    await init_mongo(bot=None, admin_chat_id=None)
    if not is_connected():
        raise RuntimeError("Mongo is not connected after init()")
    return True


def check_supabase() -> bool:
    if not USE_SUPABASE:
        print("ℹ️ Supabase disabled: немає SUPABASE_URL або SUPABASE_KEY/SUPABASE_API_KEY у .env")
        return True  # не вважаємо це помилкою, просто пропускаємо
    from database.supabase.connection import init_supabase, is_connected
    init_supabase()
    if not is_connected():
        raise RuntimeError("Supabase is not connected after init()")
    # Додатковий дружній лог
    print(f"✅ Supabase connected: {SB_URL}")
    return True


async def start_pipeline() -> None:
    loop = asyncio.get_running_loop()
    loader_main = None
    try:
        from loaders.loader_start import main as _lm
        loader_main = _lm
    except ImportError:
        try:
            from loader_start import main as _lm
            loader_main = _lm
        except ImportError:
            print("[warn] loader_start not found (neither loaders.loader_start nor loader_start)")
            return
    if inspect.iscoroutinefunction(loader_main):
        await loader_main()
    else:
        await loop.run_in_executor(None, loader_main)


async def amain() -> int:
    if not await retry_check("MongoDB", check_mongo, RETRY_ATTEMPTS, RETRY_BASE_SEC):
        print("⛔ Stopping: MongoDB connection failed.")
        return 2

    if not await retry_check("Supabase", check_supabase, RETRY_ATTEMPTS, RETRY_BASE_SEC):
        print("⛔ Stopping: Supabase connection failed.")
        return 2

    print("🚀 Starting pipeline ...")
    await start_pipeline()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
