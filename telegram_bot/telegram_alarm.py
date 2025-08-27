# telegram_bot/telegram_alarm.py
from __future__ import annotations

import os
import time
import html
import json
import logging
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple, Callable

# --- HTTP backend -------------------------------------------------------------
try:
    import requests  # type: ignore

    _HAS_REQUESTS = True
    _session = requests.Session()
    _session.headers.update({"Accept": "application/json"})
except Exception:
    _HAS_REQUESTS = False
    import urllib.request
    import urllib.error


# --- Константи / налаштування -------------------------------------------------
TELEGRAM_API_ROOT = "https://api.telegram.org"

# Жорсткі обмеження Telegram
TG_MAX_TEXT = 4096
# Будемо тримати запас під службові префікси
_CHUNK_SIZE = 3800

def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except Exception:
        return default

def _parse_admin_ids(raw: Optional[str]) -> List[int]:
    """
    ADMIN_CHAT_ID може бути одним ID або списком через кому/крапку з комою.
    Підтримуються як приватні chat_id, так і id груп/каналів (у т.ч. від'ємні).
    """
    if not raw:
        return []
    out = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except Exception:
            # ігноруємо невірні значення
            pass
    return out


# --- Основний клас ------------------------------------------------------------
@dataclass
class TelegramAlarmConfig:
    bot_token: str
    admin_ids: List[int]
    parse_mode: str = "HTML"  # HTML безпечніше екранувати
    disable_notification: bool = False
    rate_limit_per_min: int = 20   # максимум повідомлень/хв на чат
    dedup_seconds: int = 60        # не дублювати ідентичні повідомлення всередині вікна
    timeout: int = 10              # HTTP timeout


class TelegramAlarm:
    """
    Легкий відправник повідомлень в Telegram для алертів.
    - send_text(): надсилає довгі повідомлення чанками
    - send_exception(): форматований traceback
    - send_to_admins(): розсилка всім admin_ids
    Є простий rate-limit і дедуплікація на чат.
    """

    def __init__(self, cfg: TelegramAlarmConfig):
        self.cfg = cfg
        # state: {chat_id: [timestamps_sec]}
        self._bucket: dict[int, List[float]] = {}
        # dedup: {chat_id: (last_hash, t_sec)}
        self._dedup: dict[int, Tuple[int, float]] = {}

    # ---- створення з оточення
    @classmethod
    def from_env(cls) -> "TelegramAlarm":
        # ⬇️ Тут читаємо саме ці дві змінні:
        # TELEGRAM_TOKEN — токен бота
        # ADMIN_CHAT_ID  — один або кілька chat_id адмінів (через кому)
        token = os.getenv("TELEGRAM_TOKEN", "").strip()
        admins = _parse_admin_ids(os.getenv("ADMIN_CHAT_ID"))
        if not token or not admins:
            # Дозволяємо існувати об'єкту, але фактична відправка буде no-op з попередженням
            pass
        cfg = TelegramAlarmConfig(
            bot_token=token,
            admin_ids=admins,
            parse_mode=os.getenv("TELEGRAM_PARSE_MODE", "HTML"),
            disable_notification=_env_bool("TELEGRAM_DISABLE_NOTIFICATION", False),
            rate_limit_per_min=_env_int("TELEGRAM_ALARM_RATE", 20),
            dedup_seconds=_env_int("TELEGRAM_ALARM_DEDUP", 60),
            timeout=_env_int("TELEGRAM_TIMEOUT", 10),
        )
        return cls(cfg)

    # ---- HTTP
    def _post(self, method: str, payload: dict) -> Tuple[bool, Optional[str]]:
        if not self.cfg.bot_token or not self.cfg.admin_ids:
            return False, "TELEGRAM_TOKEN або ADMIN_CHAT_ID не налаштовані"

        url = f"{TELEGRAM_API_ROOT}/bot{self.cfg.bot_token}/{method}"
        try:
            if _HAS_REQUESTS:
                r = _session.post(url, json=payload, timeout=self.cfg.timeout)
                ok = (200 <= r.status_code < 300)
                if not ok:
                    return False, f"HTTP {r.status_code} {r.text}"
                jr = r.json()
            else:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                    body = resp.read().decode("utf-8")
                jr = json.loads(body)
            if not jr.get("ok", False):
                return False, f"Telegram error: {jr}"
            return True, None
        except Exception as e:
            return False, f"Exception during POST: {e}"

    # ---- rate limit / dedup
    def _allowed_to_send(self, chat_id: int, text_hash: int) -> bool:
        now = time.time()
        # dedup
        last = self._dedup.get(chat_id)
        if last and last[0] == text_hash and (now - last[1]) < self.cfg.dedup_seconds:
            return False
        self._dedup[chat_id] = (text_hash, now)

        # token bucket (проста реалізація "N за останню хвилину")
        bucket = self._bucket.setdefault(chat_id, [])
        # чистимо старі записи
        cutoff = now - 60.0
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) >= self.cfg.rate_limit_per_min:
            return False
        bucket.append(now)
        return True

    # ---- утиліти форматування
    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    @staticmethod
    def escape_html(s: str) -> str:
        return html.escape(s, quote=False)

    def _chunk_text(self, text: str) -> List[str]:
        if len(text) <= TG_MAX_TEXT:
            return [text]
        # ріжемо із запасом
        chunks: List[str] = []
        cur = 0
        while cur < len(text):
            chunks.append(text[cur:cur + _CHUNK_SIZE])
            cur += _CHUNK_SIZE
        return chunks

    # ---- публічні методи
    def send_text(self, chat_id: int, text: str, disable_notification: Optional[bool] = None) -> bool:
        if not self.cfg.bot_token or not self.cfg.admin_ids:
            logging.getLogger(__name__).warning(
                "TelegramAlarm: пропуск відправки — немає TELEGRAM_TOKEN або ADMIN_CHAT_ID"
            )
            return False

        disable = self.cfg.disable_notification if disable_notification is None else disable_notification
        text_hash = hash(text)
        if not self._allowed_to_send(chat_id, text_hash):
            return False

        # чунки
        chunks = self._chunk_text(text)
        ok_all = True
        for i, chunk in enumerate(chunks, 1):
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_notification": disable,
                "parse_mode": self.cfg.parse_mode,
            }
            ok, err = self._post("sendMessage", payload)
            if not ok:
                ok_all = False
                logging.getLogger(__name__).error("TelegramAlarm: send failed: %s", err)
                # якщо один чанк не пішов — решту можна спробувати, але повернемо False
        return ok_all

    def send_to_admins(self, text: str, disable_notification: Optional[bool] = None) -> None:
        for admin_id in self.cfg.admin_ids:
            try:
                self.send_text(admin_id, text, disable_notification=disable_notification)
            except Exception as e:
                logging.getLogger(__name__).error("TelegramAlarm: exception on send_to_admins: %s", e)

    def send_exception(self, exc: BaseException, context: Optional[str] = None, extra: Optional[str] = None) -> None:
        head = f"❌ <b>Помилка</b> — {self._utc_now()}"
        if context:
            head += f"\n<b>Контекст:</b> {self.escape_html(context)}"
        etype = type(exc).__name__
        emsg = self.escape_html(str(exc))
        tb = self.escape_html("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        body = f"<b>{etype}:</b> {emsg}\n<pre>{tb}</pre>"
        if extra:
            body += f"\n<b>Extra:</b>\n<pre>{self.escape_html(extra)}</pre>"
        self.send_to_admins(f"{head}\n{body}")


# --- logging.Handler ----------------------------------------------------------
class TelegramLogHandler(logging.Handler):
    """
    Лог-хендлер, який шле записи рівня ERROR/CRITICAL (або вище) в Telegram.
    При наявності exc_info додає traceback.
    """
    def __init__(self, alarm: Optional[TelegramAlarm] = None, level=logging.ERROR, include_trace: bool = True):
        super().__init__(level=level)
        self.alarm = alarm or TelegramAlarm.from_env()
        self.include_trace = include_trace

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno < self.level:
                return
            lvl_emoji = {
                logging.CRITICAL: "🛑",
                logging.ERROR: "❌",
                logging.WARNING: "⚠️",
                logging.INFO: "ℹ️",
                logging.DEBUG: "🐞",
            }.get(record.levelno, "❔")
            ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            base = f"{lvl_emoji} <b>{record.levelname}</b> — {ts}\n" \
                   f"<b>Logger:</b> {html.escape(record.name)}\n" \
                   f"<b>Msg:</b> {html.escape(self.format(record))}"

            if self.include_trace and record.exc_info:
                tb = self.alarm.escape_html("".join(traceback.format_exception(*record.exc_info)))
                base += f"\n<pre>{tb}</pre>"

            self.alarm.send_to_admins(base)
        except Exception:
            # не допускаємо падіння логування
            pass


# --- Зручні допоміжні функції -------------------------------------------------
_GLOBAL_ALARM: Optional[TelegramAlarm] = None

def get_alarm() -> TelegramAlarm:
    global _GLOBAL_ALARM
    if _GLOBAL_ALARM is None:
        _GLOBAL_ALARM = TelegramAlarm.from_env()
    return _GLOBAL_ALARM

def setup_telegram_logging(logger: Optional[logging.Logger] = None,
                           level: int = logging.ERROR,
                           include_trace: bool = True) -> TelegramLogHandler:
    """
    Додає TelegramLogHandler до вказаного логера (або до root-логера).
    """
    lg = logger or logging.getLogger()
    handler = TelegramLogHandler(alarm=get_alarm(), level=level, include_trace=include_trace)
    # простенький форматер (текст, що піде у Msg:)
    handler.setFormatter(logging.Formatter("%(message)s"))
    lg.addHandler(handler)
    return handler

def notify_on_exception(context: Optional[str] = None,
                        reraise: bool = True) -> Callable:
    """
    Декоратор: надсилає traceback у Telegram при винятку.
    """
    def deco(fn: Callable):
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                get_alarm().send_exception(e, context=context or fn.__name__)
                if reraise:
                    raise
        return wrapper
    return deco


# --- Приклад використання -----------------------------------------------------
if __name__ == "__main__":
    # Налаштування через env:
    #   TELEGRAM_TOKEN="123456:ABC..."         (обовʼязково)
    #   ADMIN_CHAT_ID="12345678,-100987654321" (один або кілька через кому/крапку з комою)
    # Необовʼязково:
    #   TELEGRAM_ALARM_RATE=20
    #   TELEGRAM_ALARM_DEDUP=60
    #   TELEGRAM_DISABLE_NOTIFICATION=0/1
    #   TELEGRAM_PARSE_MODE=HTML/MarkdownV2
    #   TELEGRAM_TIMEOUT=10

    logging.basicConfig(level=logging.INFO)
    setup_telegram_logging(level=logging.ERROR)

    log = logging.getLogger("demo")
    log.info("Це info (не піде в Telegram)")
    log.error("Це error (піде в Telegram)")

    @notify_on_exception("demo_task")
    def boom():
        raise RuntimeError("Щось пішло не так!")

    try:
        boom()
    except RuntimeError:
        pass

    get_alarm().send_to_admins("✅ <b>Тестове повідомлення</b>: якщо ти це бачиш — все працює.")
