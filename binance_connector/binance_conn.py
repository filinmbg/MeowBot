import os
import logging
import asyncio
from typing import Optional, Dict, Any, Tuple, Union
from datetime import datetime, timezone


from dotenv import load_dotenv
from binance.spot import Spot  # pip install binance-connector

__all__ = [
    "init_binance",
    "is_connected",
    "get_client",
    "get_balances_nonzero",
    "get_account_info",
]

log = logging.getLogger("meowbot.binance")

_client: Optional[Spot] = None
_inited: bool = False
_cached_conf: Optional[Tuple[str, str, Optional[str]]] = None  # (key, secret, base_url)
_notified_failure: bool = False
__all__.extend(["get_klines_simple"])
TimeLike = Union[int, float, str, datetime]


async def init_binance(
    *,
    bot=None,                      # telegram.Bot або application.bot (для алертів)
    admin_chat_id: Optional[int] = None,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
    base_url: Optional[str] = None,   # для testnet: https://testnet.binance.vision
) -> None:
    """
    Ініціалізація єдиного клієнта Binance Spot (binance-connector).
    Перевіряє ключі через /api/v3/account. На фейлі один раз надсилає сповіщення адміну.
    """
    global _client, _inited, _cached_conf, _notified_failure

    if _inited and _client:
        # Забороняємо переініт з іншим конфігом
        if api_key or api_secret or base_url:
            cur = _cached_conf or ("", "", None)
            new_conf = (api_key or cur[0], api_secret or cur[1], base_url or cur[2])
            if new_conf != cur:
                raise RuntimeError("Binance уже ініціалізовано. Переініціалізація з іншою конфігурацією заборонена.")
        return

    load_dotenv()
    key = (api_key or os.getenv("BINANCE_API_KEY") or "").strip()
    secret = (api_secret or os.getenv("BINANCE_API_SECRET") or "").strip()
    base = (base_url or os.getenv("BINANCE_BASE_URL") or "").strip() or None

    if not key or not secret:
        msg = "❌ BINANCE_API_KEY/BINANCE_API_SECRET не задані у .env"
        print(msg)
        log.error(msg)
        await _maybe_notify_admin(bot, admin_chat_id, f"⚠️ Binance init failed: {msg}")
        return

    try:
        # Створюємо синхронний клієнт; виклики пізніше будемо виконувати в окремому треді
        if base:
            _client = Spot(api_key=key, api_secret=secret, base_url=base)
        else:
            _client = Spot(api_key=key, api_secret=secret)

        # Перевірка доступності (public ping)
        await asyncio.to_thread(_client.ping)

        # Перевірка підписаних ендпоінтів (ключі/права)
        account = await asyncio.to_thread(_client.account)

        # OK
        _cached_conf = (key, secret, base)
        _inited = True
        _notified_failure = False
        balances = [
            f"{a['asset']}:{a.get('free','0')}"
            for a in account.get("balances", [])
            if float(a.get("free", 0)) or float(a.get("locked", 0))
        ]
        info = "✅ Підключено до Binance Spot" + (f" (base={base})" if base else "")
        print(info)
        log.info(info + (f" | активні активи: {', '.join(balances) if balances else '—'}"))

    except Exception as e:
        _client = None
        _inited = False
        err = f"❌ Помилка підключення до Binance: {type(e).__name__}: {e}"
        print(err)
        log.exception("Binance init error")
        await _maybe_notify_admin(
            bot,
            admin_chat_id,
            "⚠️ Не вдалося підключитися до Binance. Перевір ключі/права або base_url.\n"
            f"Технічні деталі: <code>{type(e).__name__}: {str(e)}</code>",
        )


def is_connected() -> bool:
    return _inited and (_client is not None)


def get_client() -> Spot:
    if not is_connected():
        raise RuntimeError("Binance не ініціалізовано. Викличте init_binance() на старті.")
    return _client  # type: ignore[return-value]


async def get_account_info() -> Dict[str, Any]:
    """
    Повні дані /api/v3/account (rateLimit, balances, permissions).
    """
    cli = get_client()
    return await asyncio.to_thread(cli.account)


async def get_balances_nonzero(min_total: float = 0.0) -> Dict[str, Dict[str, float]]:
    """
    Повертає лише активи з ненульовим сумарним балансом (free+locked).
    """
    acc = await get_account_info()
    out: Dict[str, Dict[str, float]] = {}
    for b in acc.get("balances", []):
        free = float(b.get("free", 0) or 0)
        locked = float(b.get("locked", 0) or 0)
        total = free + locked
        if total > min_total:
            out[b["asset"]] = {"free": free, "locked": locked, "total": total}
    return out


async def _maybe_notify_admin(bot, admin_chat_id: Optional[int], text: str) -> None:
    global _notified_failure
    if _notified_failure:
        return
    if bot is None or not admin_chat_id:
        return
    try:
        await bot.send_message(chat_id=admin_chat_id, text=text, parse_mode="HTML")
        _notified_failure = True
    except Exception:
        log.warning("Не вдалося надіслати повідомлення адміну про помилку Binance", exc_info=True)

def _to_millis(t: Optional[TimeLike]) -> Optional[int]:
    if t is None:
        return None
    if isinstance(t, datetime):
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return int(t.timestamp() * 1000)
    if isinstance(t, (int, float)):
        return int(t if t > 1_000_000_000_000 else t * 1000)
    if isinstance(t, str):
        s = t.strip()
        try:
            val = float(s)
            return int(val if val > 1_000_000_000_000 else val * 1000)
        except ValueError:
            pass
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    raise TypeError(f"Unsupported time type: {type(t)}")

# ЗАМІНИ існуючу get_klines на цю (той самий async-стиль):
async def get_klines(
    symbol: str,
    interval: str,
    start_time: Optional[TimeLike] = None,
    end_time: Optional[TimeLike] = None,
    limit: int = 1000,  # було 500
):
    """
    /api/v3/klines з опц. start_time/end_time. Повертає "raw" масиви як у Binance.
    Якщо клієнт не ініціалізовано — використовуємо public Spot().
    """
    cli = _client or Spot()  # public fallback для публічних ендпоінтів
    symbol = symbol.upper()

    params = {}
    if start_time is not None:
        params["startTime"] = _to_millis(start_time)
    if end_time is not None:
        params["endTime"] = _to_millis(end_time)

    limit = max(1, min(int(limit), 1000))  # clamp лише тут
    params["limit"] = limit

    return await asyncio.to_thread(cli.klines, symbol, interval, **params)