# test/scripts/download.py
import sys
import os
import gzip
import time
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone

# ── Налаштування ──────────────────────────────────────────────────────────────
SYMBOL = "ETHUSDT"

INTERVALS = {
    "1m": 60,
    "15m": 60 * 15,
    "30m": 60 * 30,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}

MAX_LIMIT = 1000
START_DAYS_BACK = 30
OUTDIR = os.path.join("test", "data", SYMBOL)

# ── Імпорт клієнта (надiйний) ────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]  # .../MeowBot
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    # варіант коли binance_conn.py лежить у корені проєкту
    from binance_conn import get_klines  # async функція
except ModuleNotFoundError:
    try:
        # варіант коли файл у пакеті binance_connector/binance_conn.py
        from binance_connector.binance_conn import get_klines  # async функція
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError(
            "Не знайдено модуль 'binance_conn'. "
            "Переконайся, що файл 'binance_conn.py' в корені проєкту АБО "
            "існує пакет 'binance_connector/binance_conn.py' (з __init__.py). "
            f"Шлях, доданий у sys.path: {ROOT}"
        ) from e

# ── Хелпери ──────────────────────────────────────────────────────────────────
def now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)

def ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)

def last_close_dt_from_chunk(raw_rows) -> datetime:
    if not raw_rows:
        raise ValueError("Порожній чанк свічок — нема з чого взяти closeTime")
    close_ms = int(raw_rows[-1][6])  # [6] closeTime(ms)
    return ms_to_dt(close_ms)

def ensure_outdir():
    os.makedirs(OUTDIR, exist_ok=True)

# ── Основна логіка ───────────────────────────────────────────────────────────
def download_interval(interval: str, seconds_per_bar: int):
    print(f"\n📥 Завантаження {interval} для {SYMBOL}...")
    ensure_outdir()

    # Стартова точка (якщо файл вже є — перехопимо нижче)
    start_dt = now_utc() - timedelta(days=START_DAYS_BACK)

    # Дозавантаження з останнього бару, якщо файл існує
    outfile = os.path.join(OUTDIR, f"{SYMBOL}_{interval}.csv.gz")
    if os.path.exists(outfile):
        try:
            with gzip.open(outfile, "rt", encoding="utf-8") as f:
                last_line = None
                for line in f:
                    last_line = line
            if last_line:
                parts = last_line.strip().split(",")
                last_close_ms = int(parts[6])  # [6] closeTime
                start_dt = ms_to_dt(last_close_ms) + timedelta(seconds=seconds_per_bar)
        except Exception:
            # якщо файл пошкоджений — стартуємо з дефолтної давнини
            pass

    to_append = []

    while True:
        # КЛЮЧОВЕ: async-функція викликається через asyncio.run і з явним start_time=
        chunk = asyncio.run(
            get_klines(
                SYMBOL,
                interval,
                start_time=start_dt,
                limit=MAX_LIMIT
            )
        )

        if not chunk:
            break

        to_append.extend(chunk)

        last_close = last_close_dt_from_chunk(chunk)

        # зупинка, якщо ми вже дістались «майже до зараз»
        if (now_utc() - last_close).total_seconds() < seconds_per_bar:
            break

        # посунутись далі
        start_dt = last_close + timedelta(seconds=seconds_per_bar)
        time.sleep(0.25)  # легкий тротл

    # Запис / дозапис
    mode = "ab" if os.path.exists(outfile) else "wb"
    if to_append:
        with gzip.open(outfile, mode) as f:
            for r in to_append:
                f.write( (",".join(map(str, r)) + "\n").encode("utf-8") )

    print(f"✅ {interval} — додано {len(to_append)} рядків → {outfile}")

if __name__ == "__main__":
    for interval, sec in INTERVALS.items():
        download_interval(interval, sec)
