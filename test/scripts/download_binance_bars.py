from __future__ import annotations

import csv
import gzip
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import requests


BASE_URL = "https://api.binance.com/api/v3/klines"


@dataclass
class DownloadConfig:
    symbol: str
    interval: str
    out_dir: Path
    start_ms: int
    end_ms: int
    limit: int = 1000
    sleep_sec: float = 0.15


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def interval_to_filename(interval: str) -> str:
    return interval


def fetch_klines(symbol, interval, start_ms, end_ms, limit):
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
        "startTime": start_ms,
        "endTime": end_ms,
    }

    resp = requests.get(BASE_URL, params=params, timeout=20)
    resp.raise_for_status()

    data = resp.json()

    if not isinstance(data, list):
        raise RuntimeError(data)

    return data


def save_klines_gz(rows: List[list], filepath: Path):

    with gzip.open(filepath, "wt", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        writer.writerow([
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_asset_volume",
            "number_of_trades",
            "taker_buy_base_asset_volume",
            "taker_buy_quote_asset_volume",
        ])

        for r in rows:
            writer.writerow([
                r[0],
                r[1],
                r[2],
                r[3],
                r[4],
                r[5],
                r[6],
                r[7],
                r[8],
                r[9],
                r[10],
            ])


def download_all(cfg: DownloadConfig):

    ensure_dir(cfg.out_dir)

    total_range = cfg.end_ms - cfg.start_ms

    cursor = cfg.start_ms

    rows: List[list] = []

    start_time = time.time()

    while True:

        batch = fetch_klines(
            cfg.symbol,
            cfg.interval,
            cursor,
            cfg.end_ms,
            cfg.limit,
        )

        if not batch:
            break

        rows.extend(batch)

        last_open = int(batch[-1][0])

        cursor = last_open + 1

        progress = (cursor - cfg.start_ms) / total_range * 100

        elapsed = time.time() - start_time

        bars = len(rows)

        print(
            f"{cfg.symbol} {cfg.interval} | "
            f"{progress:6.2f}% | "
            f"{bars:10d} bars",
            end="\r",
        )

        if len(batch) < cfg.limit:
            break

        time.sleep(cfg.sleep_sec)

    print()

    # dedupe
    dedup = {}

    for r in rows:
        dedup[int(r[0])] = r

    rows_sorted = [dedup[k] for k in sorted(dedup.keys())]

    out_name = f"{cfg.symbol}_{interval_to_filename(cfg.interval)}.csv.gz"

    out_path = cfg.out_dir / out_name

    save_klines_gz(rows_sorted, out_path)

    print(
        f"Saved {len(rows_sorted)} bars -> {out_path}"
    )


def years_ago_ms(years: int):
    return int(time.time() * 1000) - years * 365 * 24 * 60 * 60 * 1000


if __name__ == "__main__":

    symbol = "BTCUSDT"

    root = Path(r"D:\Project\MeowBot\test\data\BTCUSDT")

    ensure_dir(root)

    end_ms = int(time.time() * 1000)

    start_ms = years_ago_ms(5)

    timeframes = [
        "1m",
        "5m",
        "15m",
        "1h",
        "4h",
    ]

    for tf in timeframes:

        print(f"\nDownloading {symbol} {tf}")

        cfg = DownloadConfig(
            symbol=symbol,
            interval=tf,
            out_dir=root,
            start_ms=start_ms,
            end_ms=end_ms,
            limit=1000,
        )

        download_all(cfg)