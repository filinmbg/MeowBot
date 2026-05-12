from __future__ import annotations

import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from collections import deque
from typing import Iterable

import requests
import pandas as pd
from tqdm import tqdm


# =========================
# CONFIG
# =========================

BASE_URL = "https://fapi.binance.com"
KLINES_ENDPOINT = "/fapi/v1/klines"

OUT_DIR = Path("test-manual/data")

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "DOTUSDT",
]

TIMEFRAMES = [
    "1m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
]

START_DATE = "2020-01-01"
END_DATE = None

LIMIT = 1500

MAX_WORKERS = 4

# Binance Futures klines:
# LIMIT=1500 має вагу 10.
# Баланс-режим: ~200 запитів/хв = 2000 weight/min.
MAX_WEIGHT_PER_MINUTE = 2000
REQUEST_WEIGHT = 10
RATE_LIMIT_WINDOW_SEC = 60

MIN_SECONDS_BETWEEN_REQUESTS = 60 / (MAX_WEIGHT_PER_MINUTE / REQUEST_WEIGHT)

REQUEST_SLEEP = 0.02
MAX_RETRIES = 10
RETRY_SLEEP = 30


COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
]


print_lock = Lock()

rate_lock = Lock()
request_times = deque()
last_request_time = 0.0
global_pause_until = 0.0


# =========================
# RATE LIMITER
# =========================

def wait_for_rate_limit() -> None:
    """
    Глобальний limiter для всіх потоків:
    - рахує Binance request weight;
    - не перевищує MAX_WEIGHT_PER_MINUTE;
    - робить рівномірні запити без залпів;
    - після 429/418 ставить глобальну паузу.
    """
    global last_request_time

    while True:
        with rate_lock:
            now = time.time()

            if now < global_pause_until:
                wait_time = global_pause_until - now
            else:
                while request_times and now - request_times[0] > RATE_LIMIT_WINDOW_SEC:
                    request_times.popleft()

                current_weight = len(request_times) * REQUEST_WEIGHT
                time_from_last = now - last_request_time

                if (
                    current_weight + REQUEST_WEIGHT <= MAX_WEIGHT_PER_MINUTE
                    and time_from_last >= MIN_SECONDS_BETWEEN_REQUESTS
                ):
                    request_times.append(now)
                    last_request_time = now
                    return

                if current_weight + REQUEST_WEIGHT > MAX_WEIGHT_PER_MINUTE and request_times:
                    wait_by_weight = RATE_LIMIT_WINDOW_SEC - (now - request_times[0]) + 0.1
                else:
                    wait_by_weight = 0.0

                wait_by_spacing = max(
                    MIN_SECONDS_BETWEEN_REQUESTS - time_from_last,
                    0.0,
                )

                wait_time = max(wait_by_weight, wait_by_spacing, 0.01)

        time.sleep(wait_time)


# =========================
# HELPERS
# =========================

def dt_to_ms(value: str | None) -> int | None:
    if value is None:
        return None
    return int(pd.Timestamp(value, tz="UTC").timestamp() * 1000)


def now_ms() -> int:
    return int(pd.Timestamp.utcnow().timestamp() * 1000)


def timeframe_to_ms(tf: str) -> int:
    unit = tf[-1]
    amount = int(tf[:-1])

    if unit == "m":
        return amount * 60_000

    if unit == "h":
        return amount * 60 * 60_000

    raise ValueError(f"Unsupported timeframe: {tf}")


def format_seconds(seconds: float) -> str:
    if seconds <= 0:
        return "0s"

    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def request_klines(
    session: requests.Session,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int | None,
) -> list:
    global global_pause_until

    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "limit": LIMIT,
    }

    if end_ms is not None:
        params["endTime"] = end_ms

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            wait_for_rate_limit()

            response = session.get(
                BASE_URL + KLINES_ENDPOINT,
                params=params,
                timeout=30,
            )

            if response.status_code in (418, 429):
                wait = RETRY_SLEEP * attempt

                with rate_lock:
                    global_pause_until = max(
                        global_pause_until,
                        time.time() + wait,
                    )

                with print_lock:
                    tqdm.write(
                        f"⚠️ Binance rate limit {response.status_code} "
                        f"{symbol} {interval}. Global sleep {wait}s"
                    )

                time.sleep(wait)
                continue

            response.raise_for_status()
            return response.json()

        except Exception as e:
            last_error = e
            wait = RETRY_SLEEP * attempt

            with print_lock:
                tqdm.write(
                    f"⚠️ Retry {attempt}/{MAX_RETRIES} "
                    f"{symbol} {interval}: {e}. Sleep {wait}s"
                )

            time.sleep(wait)

    raise RuntimeError(f"{symbol} {interval} request failed: {last_error}")


def load_existing_last_open_time(path: Path) -> int | None:
    if not path.exists():
        return None

    try:
        df = pd.read_csv(path, usecols=["open_time"])
        if df.empty:
            return None
        return int(df["open_time"].max())
    except Exception:
        return None


def save_data(path: Path, rows: list[list]) -> None:
    if not rows:
        return

    df = pd.DataFrame(rows, columns=COLUMNS)
    df = df.drop(columns=["ignore"])

    numeric_cols = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
    ]

    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    int_cols = [
        "open_time",
        "close_time",
        "trades",
    ]

    for col in int_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("int64")

    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)

    df = df[
        [
            "datetime",
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_buy_base_volume",
            "taker_buy_quote_volume",
        ]
    ]

    write_header = not path.exists()

    df.to_csv(
        path,
        mode="a",
        index=False,
        header=write_header,
        compression="gzip",
    )


def deduplicate_file(path: Path) -> None:
    if not path.exists():
        return

    df = pd.read_csv(path)
    before = len(df)

    df = df.drop_duplicates(subset=["open_time"])
    df = df.sort_values("open_time")

    df.to_csv(path, index=False, compression="gzip")

    after = len(df)

    if before != after:
        with print_lock:
            tqdm.write(f"Deduplicated {path.name}: {before} -> {after}")


# =========================
# DOWNLOAD
# =========================

def download_symbol_timeframe(
    symbol: str,
    timeframe: str,
    global_bar: tqdm,
) -> dict:
    symbol_dir = OUT_DIR / symbol
    symbol_dir.mkdir(parents=True, exist_ok=True)

    path = symbol_dir / f"{symbol}_{timeframe}.csv.gz"

    start_ms = dt_to_ms(START_DATE)
    end_ms = dt_to_ms(END_DATE) if END_DATE else now_ms()
    tf_ms = timeframe_to_ms(timeframe)

    existing_last = load_existing_last_open_time(path)

    if existing_last is not None:
        current = existing_last + tf_ms
    else:
        current = start_ms

    total_candles = max((end_ms - start_ms) // tf_ms, 1)
    already_candles = max((current - start_ms) // tf_ms, 0)

    task_name = f"{symbol} {timeframe}"
    total_rows = 0
    started_at = time.time()

    with requests.Session() as session:
        with tqdm(
            total=total_candles,
            initial=min(already_candles, total_candles),
            desc=task_name,
            unit="candle",
            leave=False,
            dynamic_ncols=True,
        ) as bar:
            while current < end_ms:
                rows = request_klines(
                    session=session,
                    symbol=symbol,
                    interval=timeframe,
                    start_ms=current,
                    end_ms=end_ms,
                )

                if not rows:
                    break

                save_data(path, rows)

                last_open = int(rows[-1][0])
                next_current = last_open + tf_ms

                downloaded = max((next_current - current) // tf_ms, len(rows))

                current = next_current
                total_rows += len(rows)

                bar.update(downloaded)
                global_bar.update(downloaded)

                elapsed = time.time() - started_at
                speed_rows = total_rows / elapsed if elapsed > 0 else 0

                bar.set_postfix(
                    rows=total_rows,
                    speed=f"{speed_rows:.1f} r/s",
                )

                if len(rows) < LIMIT:
                    break

                time.sleep(REQUEST_SLEEP)

    deduplicate_file(path)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "rows": total_rows,
        "path": str(path),
    }


def build_tasks(
    symbols: Iterable[str],
    timeframes: Iterable[str],
) -> list[tuple[str, str]]:
    return [(symbol, tf) for symbol in symbols for tf in timeframes]


def estimate_global_total_candles(tasks: list[tuple[str, str]]) -> int:
    start_ms = dt_to_ms(START_DATE)
    end_ms = dt_to_ms(END_DATE) if END_DATE else now_ms()

    total = 0

    for _, tf in tasks:
        tf_ms = timeframe_to_ms(tf)
        total += max((end_ms - start_ms) // tf_ms, 1)

    return total


def download_all(
    symbols: Iterable[str],
    timeframes: Iterable[str],
) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    tasks = build_tasks(symbols, timeframes)
    global_total = estimate_global_total_candles(tasks)

    results = []
    errors = []

    print("=" * 80)
    print("MANUAL DATA DOWNLOAD")
    print(f"Folder: {OUT_DIR}")
    print(f"Symbols: {len(SYMBOLS)}")
    print(f"Timeframes: {TIMEFRAMES}")
    print(f"Workers: {MAX_WORKERS}")
    print(f"Max weight/min: {MAX_WEIGHT_PER_MINUTE}")
    print(f"Request weight: {REQUEST_WEIGHT}")
    print(f"Request interval: {MIN_SECONDS_BETWEEN_REQUESTS:.3f}s")
    print(f"Start date: {START_DATE}")
    print("=" * 80)

    started_at = time.time()

    with tqdm(
        total=global_total,
        desc="GLOBAL",
        unit="candle",
        dynamic_ncols=True,
    ) as global_bar:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(
                    download_symbol_timeframe,
                    symbol,
                    tf,
                    global_bar,
                ): (symbol, tf)
                for symbol, tf in tasks
            }

            for future in as_completed(futures):
                symbol, tf = futures[future]

                try:
                    result = future.result()
                    results.append(result)

                    tqdm.write(
                        f"✅ DONE {symbol} {tf} | "
                        f"rows: {result['rows']} | "
                        f"{result['path']}"
                    )

                except Exception as e:
                    errors.append((symbol, tf, str(e)))
                    tqdm.write(f"❌ ERROR {symbol} {tf}: {e}")

    elapsed = time.time() - started_at

    print("\n" + "=" * 80)
    print("SUMMARY")
    print(f"Finished tasks: {len(results)} / {len(tasks)}")
    print(f"Errors: {len(errors)}")
    print(f"Total time: {format_seconds(elapsed)}")
    print("=" * 80)

    if errors:
        print("\nErrors:")
        for symbol, tf, error in errors:
            print(f"- {symbol} {tf}: {error}")


if __name__ == "__main__":
    download_all(SYMBOLS, TIMEFRAMES)