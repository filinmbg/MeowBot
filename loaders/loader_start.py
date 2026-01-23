# loaders/loader_start.py
import os
import json
import time
import asyncio
import logging
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv
import pandas as pd

# індикатори з твого файлу
try:
    from indicators.critical_indicators import add_critical_indicators
except ImportError:
    from critical_indicators import add_critical_indicators  # якщо файл лежить у корені

# Mongo (motor-стек з твого connection.py)
from database.mongo.connection import init_mongo, get_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("meowbot.loader_start")

# ---- файлові шляхи для last.json ----
BASE_DIR = os.getenv("MEOWBOT_DIR") or os.path.join(os.getcwd(), "meowbot")
BARS_DIR = os.path.join(BASE_DIR, "bars")

# ---- Таймфрейми ----
DEFAULT_TFS = ["15m", "30m", "1h", "4h", "1d"]
TIMEFRAMES = [s.strip() for s in os.getenv("TIMEFRAMES", ",".join(DEFAULT_TFS)).split(",") if s.strip()]

# ---- Параметри запитів ----
MAX_CONCURRENCY = int(os.getenv("BINANCE_CONCURRENCY", "3"))
FETCH_LIMIT = 150
BINANCE_MAX_LIMIT = 1000  # обмеження API

# ---- Зберігати у Mongo? (окрім last.json у ФС) ----
SAVE_BARS_TO_MONGO = os.getenv("SAVE_BARS_TO_MONGO", "1").lower() in {"1", "true", "yes"}

# ---- Мапа TF у мс ----
TF_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000,
    "12h": 43_200_000, "1d": 86_400_000, "3d": 259_200_000, "1w": 604_800_000, "1M": 2_592_000_000,
}

# ---- Binance Spot клієнт (public klines) ----
def _build_spot_client():
    from binance.spot import Spot
    base_url = os.getenv("BINANCE_BASE_URL")  # опційно для тестнету
    return Spot(base_url=base_url) if base_url else Spot()

# ---- Mongo: дістати активні символи ----
async def _read_symbols_mongo() -> List[str]:
    await init_mongo(bot=None, admin_chat_id=None)
    col = get_db()["symbols"]
    symbols: List[str] = []
    async for doc in col.find({"active": True}, {"_id": 0, "symbol": 1}):
        s = str(doc.get("symbol", "")).strip().upper()
        if s:
            symbols.append(s)
    if not symbols:
        log.warning("⚠️ У колекції 'symbols' немає активних монет. Додай хоча б BTCUSDT.")
    return symbols

def _ensure_dirs(symbol: str, tf: str) -> str:
    d = os.path.join(BARS_DIR, symbol.upper(), tf)
    os.makedirs(d, exist_ok=True)
    return d

def _path_last(symbol: str, tf: str) -> str:
    return os.path.join(BARS_DIR, symbol.upper(), tf, "last.json")

def _read_last_close_time(symbol: str, tf: str) -> Optional[int]:
    p = _path_last(symbol, tf)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("close_time"))
    except Exception:
        return None

def _read_last_bar_fs(symbol: str, tf: str) -> Optional[Dict[str, Any]]:
    """Прочитати last.json та повернути словник бара, або None."""
    p = _path_last(symbol, tf)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "close_time" in data:
            return data
    except Exception as e:
        log.warning("Не вдалося прочитати %s: %s", p, e)
    return None

def _last_closed_close_time_ms(tf: str, now_ms: Optional[int] = None) -> int:
    tf_ms = TF_MS[tf]
    now_ms = now_ms or int(time.time() * 1000)
    start_current = (now_ms // tf_ms) * tf_ms
    return start_current - 1  # close_time попередньої (закритої) свічки

def _bars_to_df(symbol: str, tf: str, klines: List[List[Any]]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for k in klines:
        rows.append({
            "symbol": symbol.upper(),
            "timeframe": tf,
            "open_time": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "close_time": int(k[6]),
        })
    return pd.DataFrame(rows)

def _save_last_bar(symbol: str, tf: str, row: Dict[str, Any]) -> None:
    """Зберегти останній бар у файлову систему як last.json."""
    d = _ensure_dirs(symbol, tf)
    p = os.path.join(d, "last.json")
    clean: Dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, (int, float, str, bool)) or v is None:
            clean[k] = v
        else:
            try:
                clean[k] = float(v)
            except Exception:
                clean[k] = str(v)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)

def _pyify_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Перетворити numpy/pandas типи у звичайні Python (int/float/str/bool/None) для Mongo."""
    out: Dict[str, Any] = {}
    for k, v in row.items():
        try:
            import numpy as _np
            if isinstance(v, (_np.integer,)):
                out[k] = int(v)
            elif isinstance(v, (_np.floating,)):
                out[k] = float(v)
            else:
                out[k] = v
        except Exception:
            out[k] = v
    return out

async def _upsert_last_bar_mongo(row: Dict[str, Any]) -> int:
    """
    Зберігаємо той самий останній бар у Mongo (колекція 'bars') з унікальним індексом.
    Повертає 1, якщо був upsert (створено новий), інакше 0.
    """
    col = get_db()["bars"]
    await col.create_index([("symbol", 1), ("timeframe", 1), ("open_time", 1)], unique=True, name="uniq_symbol_tf_open")

    row = _pyify_row(row)
    filt = {"symbol": row["symbol"], "timeframe": row["timeframe"], "open_time": row["open_time"]}
    update = {"$setOnInsert": row, "$set": {"updated_at": int(time.time() * 1000)}}
    res = await col.update_one(filt, update, upsert=True)
    return 1 if res.upserted_id is not None else 0

async def _upsert_latest_bar_mongo(row: Dict[str, Any]) -> int:
    """
    Один-останній документ на (symbol, timeframe) у 'bars_last'.
    Перезаписуємо весь бар + оновлюємо updated_at.
    """
    col = get_db()["bars_last"]
    await col.create_index([("symbol", 1), ("timeframe", 1)], unique=True, name="uniq_symbol_tf")

    row = _pyify_row(row)
    now_ms = int(time.time() * 1000)
    filt = {"symbol": row["symbol"], "timeframe": row["timeframe"]}
    update = {"$set": {**row, "updated_at": now_ms}, "$setOnInsert": {"created_at": now_ms}}
    res = await col.update_one(filt, update, upsert=True)
    return 1 if res.upserted_id is not None else 0

async def _fetch_closed_klines(spot, symbol: str, tf: str, end_close_ms: int, limit: int = FETCH_LIMIT) -> List[List[Any]]:
    limit = min(limit, BINANCE_MAX_LIMIT)
    def blocking():
        # endTime включно; API повертає kline зі close_time <= endTime
        return spot.klines(symbol, tf, endTime=end_close_ms, limit=limit)
    kl = await asyncio.to_thread(blocking)
    return [k for k in kl if int(k[6]) <= end_close_ms]

async def _process_symbol_tf(spot, symbol: str, tf: str) -> int:
    """
    якщо last.json відсутній або його close_time != очікуваному — тягнемо 150 закритих,
    рахуємо індикатори, зберігаємо тільки останню свічку як last.json + (опц.) upsert у Mongo.
    Повертає 1 — оновлено, 0 — нічого робити не треба.
    """
    expected_close = _last_closed_close_time_ms(tf)
    current_close = _read_last_close_time(symbol, tf)

    # --- last.json вже актуальний — синхронізуємо Mongo з файлу та виходимо
    if current_close == expected_close:
        log.info("skip %s %s — last.json актуальний (%s)", symbol, tf, current_close)
        if SAVE_BARS_TO_MONGO:
            row = _read_last_bar_fs(symbol, tf)
            if row:
                try:
                    inserted_arch = await _upsert_last_bar_mongo(row)      # гарантуємо наявність в архіві
                    inserted_last = await _upsert_latest_bar_mongo(row)    # і «останній» документ
                    if inserted_arch:
                        log.info("🗄️  bars(upsert-from-fs) %s %s open_time=%s", symbol, tf, row.get("open_time"))
                    if inserted_last:
                        log.info("🗄️  bars_last(upsert-from-fs) %s %s", symbol, tf)
                except Exception as e:
                    log.warning("Mongo upsert-from-fs failed for %s %s: %s", symbol, tf, e)
            else:
                log.warning("Не знайшов last.json для %s %s при спробі синхронізації Mongo", symbol, tf)
        return 0

    # --- треба оновити last.json
    kl = await _fetch_closed_klines(spot, symbol, tf, expected_close, FETCH_LIMIT)
    if not kl:
        log.warning("немає свічок для %s %s до %s", symbol, tf, expected_close)
        return 0

    df = _bars_to_df(symbol, tf, kl)
    df = add_critical_indicators(df)

    last_row = df.sort_values("close_time").iloc[-1].to_dict()
    if int(last_row["close_time"]) != expected_close:
        log.warning("останній бар %s %s має close_time=%s, очікували %s",
                    symbol, tf, last_row["close_time"], expected_close)

    # ФС
    _save_last_bar(symbol, tf, last_row)

    # Mongo (за замовчуванням увімкнено через SAVE_BARS_TO_MONGO=1)
    if SAVE_BARS_TO_MONGO:
        try:
            inserted_arch = await _upsert_last_bar_mongo(last_row)      # архів (bars)
            inserted_last = await _upsert_latest_bar_mongo(last_row)    # один-останній (bars_last)
            if inserted_arch:
                log.info("🗄️  bars(upsert) %s %s open_time=%s", symbol, tf, last_row["open_time"])
            if inserted_last:
                log.info("🗄️  bars_last(upsert) %s %s", symbol, tf)
        except Exception as e:
            log.warning("Mongo upsert bars/bars_last failed for %s %s: %s", symbol, tf, e)

    log.info("✅ saved %s %s last.json (close_time=%s)", symbol, tf, last_row["close_time"])
    return 1

async def main():
    load_dotenv()

    # символи з Mongo
    symbols = await _read_symbols_mongo()
    if not symbols:
        log.warning("❌ Немає активних символів у Mongo — додай BTCUSDT через scripts/init_symbols.py")
        return

    # каталоги для last.json
    os.makedirs(BARS_DIR, exist_ok=True)
    for s in symbols:
        for tf in TIMEFRAMES:
            _ensure_dirs(s, tf)

    spot = _build_spot_client()

    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    async def guarded(sym: str, tf: str) -> int:
        async with sem:
            try:
                return await _process_symbol_tf(spot, sym, tf)
            except Exception as e:
                log.warning("fail %s %s: %s", sym, tf, e)
                return 0

    tasks = [guarded(sym, tf) for sym in symbols for tf in TIMEFRAMES]
    results = await asyncio.gather(*tasks)
    log.info("Done. Updated last.json: %d", sum(results))

if __name__ == "__main__":
    asyncio.run(main())
