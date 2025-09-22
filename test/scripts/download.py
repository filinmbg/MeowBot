import sys
import os
import gzip
import time
from datetime import datetime, timedelta

# Додаємо шлях до binance_connector
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from binance_connector.client import get_klines

SYMBOL = "ETHUSDT"
INTERVALS = {
    "1m": 60,
    "15m": 60 * 15,
    "30m": 60 * 30,
    "1h": 60 * 60,
    "4h": 60 * 60 * 4,
    "1d": 60 * 60 * 24
}
START_DATE = datetime(2022, 1, 1)
MAX_LIMIT = 1500

OUTDIR = os.path.join("test", "data", SYMBOL)
os.makedirs(OUTDIR, exist_ok=True)


def progress_bar(tag, current, total):
    percent = (current / total) * 100
    bar = f"[{'#' * int(percent // 2):<50}]"
    sys.stdout.write(f"\r{tag:<4}: {bar} {percent:.2f}%")
    sys.stdout.flush()


def download_interval(interval, seconds_per_bar):
    print(f"\n📥 Завантаження {interval}...")
    now = datetime.utcnow()
    total_secs = (now - START_DATE).total_seconds()
    total_bars = int(total_secs // seconds_per_bar)

    start = START_DATE
    all_data = []
    bar_count = 0

    while start < now:
        data = get_klines(SYMBOL, interval, start, limit=MAX_LIMIT)
        if not data:
            break

        all_data.extend(data)
        bar_count += len(data)
        progress_bar(interval, bar_count, total_bars)

        last_close = datetime.fromtimestamp(data[-1][0] / 1000)
        start = last_close + timedelta(seconds=seconds_per_bar)
        time.sleep(0.3)

    outfile = os.path.join(OUTDIR, f"{SYMBOL}_{interval}.csv.gz")
    with gzip.open(outfile, "wt", encoding="utf-8") as f:
        for row in all_data:
            f.write(",".join(map(str, row)) + "\n")

    print(f"\n✅ {interval} — збережено {len(all_data)} рядків у {outfile}")


if __name__ == "__main__":
    for interval, sec in INTERVALS.items():
        download_interval(interval, sec)
