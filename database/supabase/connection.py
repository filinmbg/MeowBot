# database/supabase/connection.py
import os
import logging
from typing import Optional, Tuple

from dotenv import load_dotenv

try:
    from supabase import create_client, Client  # pip install supabase
except Exception:
    create_client = None
    Client = object  # type: ignore

import httpx

__all__ = ["init_supabase", "get_supabase", "is_connected"]

log = logging.getLogger("meowbot.supabase")

_client: Optional["Client"] = None
_inited: bool = False
_last_ok: bool = False
_cached_conf: Optional[Tuple[str, str]] = None  # (url, key)


def _health_check(url: str) -> bool:
    """Ping Supabase auth health endpoint. 2xx/4xx вважаємо що сервіс «живий»."""
    health_url = url.rstrip("/") + "/auth/v1/health"
    try:
        r = httpx.get(health_url, timeout=5)
        return r.status_code < 500
    except Exception as e:
        log.warning("Supabase health check failed: %s", e)
        return False


def init_supabase(*, url: Optional[str] = None, key: Optional[str] = None) -> None:
    """
    Ініціалізує глобальний клієнт Supabase та робить health-check.
    """
    global _client, _inited, _last_ok, _cached_conf

    if _inited and (_client is not None):
        if url or key:
            cur = _cached_conf or ("", "")
            new_conf = (url or cur[0], key or cur[1])
            if new_conf != cur:
                raise RuntimeError("Supabase вже ініціалізовано з іншою конфігурацією.")
        return

    load_dotenv()
    sb_url = (url or os.getenv("SUPABASE_URL") or "").strip()
    # ✅ підтримуємо обидві назви змінної з ключем
    sb_key = (key or os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_API_KEY") or "").strip()

    if not sb_url or not sb_key:
        raise RuntimeError("SUPABASE_URL або SUPABASE_KEY/SUPABASE_API_KEY не задані у .env")

    if create_client is None:
        raise RuntimeError("Пакет 'supabase' не встановлено. Встанови: pip install supabase")

    cli = create_client(sb_url, sb_key)

    if not _health_check(sb_url):
        raise RuntimeError("Supabase health-check не пройшов")

    _client = cli
    _inited = True
    _last_ok = True
    _cached_conf = (sb_url, sb_key)
    log.info("✅ Supabase OK: %s", sb_url)


def is_connected() -> bool:
    return _inited and (_client is not None) and _last_ok


def get_supabase() -> "Client":
    if not is_connected():
        raise RuntimeError("Supabase не ініціалізовано. Виклич init_supabase() на старті.")
    return _client  # type: ignore[return-value]
