# -*- coding: utf-8 -*-
"""
backtest/run.py
Качає/інжестить OHLCV і пише у Parquet + рахує індикатори (крім 1m) і теж зберігає в той самий Parquet.
Тепер із вбудованим rate-лімітером і обробкою 429/Retry-After.

Приклади:
  # Топ-20 монет, усі ТФ за останній рік (5 паралелей за замовч.)
  python backtest/run.py

  # Лише ADAUSDT та BTCUSDT на 1m,15m,1h
  python backtest/run.py --symbols ADAUSDT,BTCUSDT --timeframes 1m,15m,1h

  # Силою перезаписати існуючі parquet
  python backtest/run.py --force

  # Не качати, а інжестити CSV -> Parquet + індикатори (якщо CSV уже є)
  python backtest/run.py --from-csv

Параметри дати:
  --start YYYY-MM-DD  (UTC). За замовч.: сьогодні мінус 365 днів.
  --end   YYYY-MM-DD  (UTC). За замовч.: сьогодні.
"""

import os
import sys
import math
import json
import time
import random
import argparse
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd
import numpy as np
from tqdm import tqdm
import aiohttp

# --- перевірка parquet-бекенду ---
_PARQUET_BACKEND_OK = False
try:
    import pyarrow  # noqa: F401
    _PARQUET_BACKEND_OK = True
except Exception:
    try:
        import fastparquet  # noqa: F401
        _PARQUET_BACKEND_OK = True
    except Exception:
        _PARQUET_BACKEND_OK = False

# --- sys.path bootstrap для пакета indicators ---
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    from indicators.critical_indicators import add_critical_indicators
except ModuleNotFoundError as e:
    raise ImportError(
        "Не знайдено 'indicators/critical_indicators.py'. Створи пакет 'indicators' "
        "з __init__.py і поклади туди critical_indicators.py"
    ) from e

DATA_DIR = ROOT / "backtest" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

BINANCE_FAPI = "https://fapi.binance.com"  # Binance USDT-M Futures
KLINES_EP = "/fapi/v1/klines"

TOP20 = [
    "ADAUSDT","ATOMUSDT","BATUSDT","BCHUSDT","BNBUSDT","BTCUSDT","DASHUSDT",
    "ETCUSDT","ETHUSDT","IOTAUSDT","LINKUSDT","LTCUSDT","ONTUSDT","TRXUSDT",
    "VETUSDT","XLMUSDT","XMRUSDT","XRPUSDT","XTZUSDT","ZECUSDT"
]

TF_TO_BINANCE = {"1m":"1m","15m":"15m","30m":"30m","1h":"1h","4h":"4h","1d":"1d"}
TF_TO_MS = {"1m":60_000, "15m":900_000, "30m":1_800_000, "1h":3_600_000, "4h":14_400_000, "1d":86_400_000}
NON_1M_TFS = [tf for tf in TF_TO_BINANCE if tf != "1m"]

CSV_CANDIDATE_COLS_TS = ("open_time","opentime","t","timestamp","time","date")
CSV_CANDIDATE_COLS_CT = ("close_time","closetime")
CSV_CANDIDATE_OHLCV = {
    "open":("open","o"),
    "high":("high","h"),
    "low":("low","l"),
    "close":("close","c"),
    "volume":("volume","vol","v")
}

# ============================ УТИЛІТИ ============================

def _ts(dt: Optional[str]) -> int:
    if not dt:
        return 0
    return int(pd.Timestamp(dt, tz="UTC").timestamp() * 1000)

def _utc_today() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

def _default_start_end() -> Tuple[int,int]:
    end = _utc_today()
    start = end - timedelta(days=365)
    return (int(start.timestamp()*1000), int(end.timestamp()*1000))

def _sym_dir(symbol: str) -> Path:
    p = DATA_DIR / symbol
    p.mkdir(parents=True, exist_ok=True)
    return p

def _parquet_path(symbol: str, tf: str) -> Path:
    return _sym_dir(symbol) / f"{tf}.parquet"

def _csv_path(symbol: str, tf: str) -> Path:
    return _sym_dir(symbol) / f"{tf}.csv"

def _require_parquet_backend():
    if not _PARQUET_BACKEND_OK:
        raise RuntimeError(
            "Для збереження Parquet потрібен 'pyarrow' або 'fastparquet'. "
            "Встанови, наприклад: pip install pyarrow"
        )

def _ensure_f64(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    return df

def _ensure_ms_int64(a: np.ndarray) -> np.ndarray:
    arr = pd.to_numeric(a, errors="coerce").astype("int64")
    # якщо значення схожі на секунди (10-значні) — переводимо в мс
    if (arr.max() < 10_000_000_000) and (arr.max() > 1_000_000_000):
        arr = arr * 1000
    return arr

def _save_parquet(symbol: str, tf: str, df: pd.DataFrame):
    _require_parquet_backend()
    outp = _parquet_path(symbol, tf)
    # порядок і типи колонок
    for c in ("open_time","close_time"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("int64")
    for c in ("open","high","low","close","volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    # сортуємо і пишемо
    df = df.sort_values("close_time").reset_index(drop=True)
    df.to_parquet(outp, index=False)
    return outp

def _have_all_ohlcv_cols(df: pd.DataFrame) -> bool:
    need = {"open_time","close_time","open","high","low","close","volume"}
    return need.issubset(df.columns)

# ============================ RATE LIMITER ============================

class RateLimiter:
    """
    Простий token-bucket на весь процес.
    rate_per_min — цільова середня швидкість запитів (по всіх паралелях).
    burst — максимальний «зрив» в короткому проміжку.
    """
    def __init__(self, rate_per_min: int = 900, burst: int = 200):
        self.rate_per_sec = rate_per_min / 60.0
        self.capacity = float(burst)
        self.tokens = float(burst)
        self.last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last
            self.last = now
            # поповнюємо токени
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_sec)
            if self.tokens < 1.0:
                need = 1.0 - self.tokens
                await asyncio.sleep(need / self.rate_per_sec)
                now = time.monotonic()
                elapsed = now - self.last
                self.last = now
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_sec)
            self.tokens -= 1.0

# Безпечні налаштування (нижче ліміту 2400 rpm від Binance)
RATE_LIMITER = RateLimiter(rate_per_min=900, burst=200)

# ============================ BINANCE FETCH ============================

async def fetch_json(session: aiohttp.ClientSession, url: str, params: Dict[str, Any],
                     retries: int = 8, timeout_s: int = 30) -> Any:
    """
    GET з глобальним rate-limit, обробкою 429 та експоненційним backoff (+джиттер).
    """
    last_err = None
    for attempt in range(retries):
        # Глобальний ліміт запитів
        await RATE_LIMITER.acquire()
        try:
            async with session.get(url, params=params, timeout=timeout_s) as r:
                if r.status == 200:
                    return await r.json()

                # Якщо Binance повернув 429 — намагаємось дочекатися
                if r.status == 429:
                    retry_after = r.headers.get("Retry-After")
                    try:
                        wait_s = float(retry_after)
                    except (TypeError, ValueError):
                        wait_s = min(60.0, (2.0 ** attempt)) + random.uniform(0, 1.0)
                    txt = await r.text()
                    last_err = RuntimeError(f"HTTP 429: {txt[:200]}")
                    await asyncio.sleep(wait_s)
                    continue

                # Інші тимчасові помилки — теж backoff
                if r.status in (418, 451, 500, 503):
                    txt = await r.text()
                    last_err = RuntimeError(f"HTTP {r.status}: {txt[:200]}")
                else:
                    txt = await r.text()
                    raise RuntimeError(f"HTTP {r.status}: {txt[:200]}")
        except Exception as e:
            last_err = e

        # Загальний backoff (експоненційний + джиттер)
        await asyncio.sleep(min(30.0, (1.5 ** attempt)) + random.uniform(0, 0.5))

    raise RuntimeError(f"Failed GET {url} after {retries} attempts: {last_err}")

async def fetch_klines(session: aiohttp.ClientSession, symbol: str, tf: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """
    Качає всі свічки [start_ms, end_ms) для symbol/tf. Endpoint: /fapi/v1/klines
    Binance ліміт: до 1500 свічок за запит — біжимо в циклі.
    """
    url = BINANCE_FAPI + KLINES_EP
    interval = TF_TO_BINANCE[tf]
    limit = 1500

    all_rows: List[List[Any]] = []
    cur = start_ms
    tf_ms = TF_TO_MS[tf]

    while True:
        params = {"symbol": symbol, "interval": interval, "limit": limit, "startTime": cur, "endTime": end_ms}
        data = await fetch_json(session, url, params=params)
        if not data:
            break
        all_rows.extend(data)
        # просуваємося: останній close_time + 1 мс
        last_close = int(data[-1][6])
        next_cur = last_close + 1
        if next_cur <= cur:
            break
        cur = next_cur
        if cur >= end_ms:
            break

    if not all_rows:
        return pd.DataFrame(columns=["open_time","open","high","low","close","volume","close_time"])

    arr = np.array(all_rows, dtype=object)
    df = pd.DataFrame({
        "open_time": arr[:,0].astype(np.int64),
        "open":      arr[:,1].astype(np.float64),
        "high":      arr[:,2].astype(np.float64),
        "low":       arr[:,3].astype(np.float64),
        "close":     arr[:,4].astype(np.float64),
        "volume":    arr[:,5].astype(np.float64),
        "close_time":arr[:,6].astype(np.int64),
    })
    df = df.sort_values("close_time").reset_index(drop=True)
    df = df[(df["close_time"] >= start_ms) & (df["close_time"] < end_ms)].reset_index(drop=True)
    return df

# ============================ CSV INGEST ============================

def _lower_map(cols) -> Dict[str,str]:
    return {str(c).lower(): c for c in cols}

def load_csv_ohlcv(csv_path: Path, tf: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    lc = _lower_map(df.columns)

    # OHLCV
    def pick(name: str) -> Optional[str]:
        for cand in CSV_CANDIDATE_OHLCV[name]:
            c = lc.get(cand)
            if c: return c
        return None
    col_o = pick("open"); col_h = pick("high"); col_l = pick("low"); col_c = pick("close")
    col_v = pick("volume")

    if not all([col_o, col_h, col_l, col_c]):
        raise ValueError(f"{csv_path} не містить колонок open/high/low/close")

    # час
    col_ot = None
    for cand in CSV_CANDIDATE_COLS_TS:
        if cand in lc:
            col_ot = lc[cand]; break
    col_ct = None
    for cand in CSV_CANDIDATE_COLS_CT:
        if cand in lc:
            col_ct = lc[cand]; break

    tf_ms = TF_TO_MS[tf]
    if col_ot is not None:
        open_time = _ensure_ms_int64(df[col_ot].values)
        if col_ct is not None:
            close_time = _ensure_ms_int64(df[col_ct].values)
        else:
            close_time = open_time + tf_ms
    else:
        ts_col = None
        for cand in ("timestamp","time","date"):
            if cand in lc:
                ts_col = lc[cand]; break
        if ts_col is None:
            raise ValueError(f"{csv_path}: нема open_time/close_time або timestamp/time/date")
        ts = pd.to_datetime(df[ts_col], utc=True, errors="coerce")
        if ts.isna().all():
            raise ValueError(f"{csv_path}: не вдалось розпарсити час у колонці {ts_col}")
        open_time = (ts.view("int64") // 1_000_000).astype("int64")
        close_time = open_time + tf_ms

    out = pd.DataFrame({
        "open_time": open_time,
        "close_time": close_time,
        "open":  pd.to_numeric(df[col_o], errors="coerce").astype("float64"),
        "high":  pd.to_numeric(df[col_h], errors="coerce").astype("float64"),
        "low":   pd.to_numeric(df[col_l], errors="coerce").astype("float64"),
        "close": pd.to_numeric(df[col_c], errors="coerce").astype("float64"),
        "volume": pd.to_numeric(df[col_v], errors="coerce").astype("float64") if col_v else 0.0,
    })
    out = out.dropna(subset=["open","high","low","close"]).sort_values("close_time").reset_index(drop=True)
    return out

# ============================ ІНДИКАТОРИ ============================

REQUIRED_INDS = [
    "ema_50","ema_200","rsi_14",
    "stoch_k_14_3","stoch_d_14_3","mfi_14",
    "atr_14","adx_14"
]

def ensure_critical_indicators(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """На 1m індикатори не рахуємо. На інших ТФ — додаємо, якщо їх ще нема."""
    if tf == "1m":
        return df
    if all(col in df.columns for col in REQUIRED_INDS):
        return df
    return add_critical_indicators(df)

# ============================ ПАЙПЛАЙН ============================

async def process_symbol_tf(session: aiohttp.ClientSession,
                            symbol: str,
                            tf: str,
                            start_ms: int,
                            end_ms: int,
                            force: bool,
                            from_csv: bool) -> str:
    """
    Повертає шлях до parquet.
    1) Якщо parquet існує і !force — просто повертаємо його (нічого не робимо).
    2) Інакше якщо from_csv або існує CSV — читаємо CSV -> Parquet.
    3) Інакше качаємо з Binance -> Parquet.
    Далі: якщо tf != 1m — рахуємо індикатори і перезаписуємо Parquet з ними.
    """
    outp = _parquet_path(symbol, tf)
    if outp.exists() and not force:
        return str(outp)

    # 1) CSV → Parquet ?
    csvp = _csv_path(symbol, tf)
    df: pd.DataFrame
    if from_csv or csvp.exists():
        df = load_csv_ohlcv(csvp, tf)
    else:
        # 2) Качаємо з Binance
        df = await fetch_klines(session, symbol, tf, start_ms, end_ms)

    # Сейв базових OHLCV
    _save_parquet(symbol, tf, df)

    # 3) Індикатори (крім 1m)
    df_enriched = ensure_critical_indicators(df, tf)
    if df_enriched is not df:
        _save_parquet(symbol, tf, df_enriched)

    return str(outp)

async def worker_sem(sem: asyncio.Semaphore, coro_fn, *args, **kwargs):
    async with sem:
        return await coro_fn(*args, **kwargs)

async def pipeline(symbols: List[str],
                   timeframes: List[str],
                   start_ms: int,
                   end_ms: int,
                   max_parallel: int,
                   force: bool,
                   from_csv: bool):
    tasks = []
    sem = asyncio.Semaphore(max_parallel)
    timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=60)
    headers = {"User-Agent": "MeowBot/1.0 (+parquet+ratelimit)"}

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for sym in symbols:
            for tf in timeframes:
                tasks.append(worker_sem(sem, process_symbol_tf, session, sym, tf, start_ms, end_ms, force, from_csv))

        results = []
        with tqdm(total=len(tasks), desc="Завантаження+Parquet+Індикатори", unit="job") as pbar:
            for fut in asyncio.as_completed(tasks):
                try:
                    res = await fut
                    results.append(res)
                except Exception as e:
                    results.append(f"ERROR: {e}")
                pbar.update(1)

    # Короткий звіт
    ok = sum(1 for r in results if isinstance(r, str) and not r.startswith("ERROR"))
    err = [r for r in results if isinstance(r, str) and r.startswith("ERROR")]
    if err:
        print(f"\nГотово з помилками: {ok}/{len(results)} успішно.")
        for e in err[:10]:
            print("  -", e)
        if len(err) > 10:
            print(f"  ... ще {len(err)-10} помилок")
    else:
        print(f"\nГотово: {ok}/{len(results)} файлів оновлено.")

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(TOP20), help="список символів через кому")
    ap.add_argument("--timeframes", default="1m,15m,30m,1h,4h,1d", help="список ТФ через кому")
    ap.add_argument("--start", default="", help="UTC дата початку (YYYY-MM-DD), за замовч. сьогодні-365д")
    ap.add_argument("--end",   default="", help="UTC дата кінця (YYYY-MM-DD), за замовч. сьогодні")
    ap.add_argument("--max_parallel", type=int, default=5, help="макс. одночасних завдань (рекомендую 3–5)")
    ap.add_argument("--force", action="store_true", help="перезаписати існуючі parquet")
    ap.add_argument("--from-csv", action="store_true", help="не качати; взяти CSV (якщо є) і конвертувати в Parquet")
    return ap.parse_args()

def main():
    args = parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    for tf in timeframes:
        if tf not in TF_TO_BINANCE:
            raise ValueError(f"Непідтримуваний таймфрейм: {tf}")

    if args.start and args.end:
        start_ms = _ts(args.start)
        end_ms = _ts(args.end)
    else:
        start_ms, end_ms = _default_start_end()

    print("Символи:", symbols)
    print("Таймфрейми:", timeframes)
    print("Період:", datetime.utcfromtimestamp(start_ms/1000).strftime("%Y-%m-%d"), "→",
          datetime.utcfromtimestamp(end_ms/1000).strftime("%Y-%m-%d"))
    print("Паралельність:", args.max_parallel)
    print("Режим:", "FROM-CSV" if args.from_csv else "DOWNLOAD")
    if not _PARQUET_BACKEND_OK:
        print("⚠️ Увага: не знайдено бекенду для Parquet. Встанови 'pyarrow' або 'fastparquet'.")

    asyncio.run(pipeline(
        symbols=symbols,
        timeframes=timeframes,
        start_ms=start_ms,
        end_ms=end_ms,
        max_parallel=args.max_parallel,
        force=args.force,
        from_csv=args.from_csv
    ))

if __name__ == "__main__":
    main()
