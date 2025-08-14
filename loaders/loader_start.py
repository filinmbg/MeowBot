import os
import logging
import asyncio
from datetime import datetime, timezone
from typing import List
from database.mongo.symbols import get_symbols
from dotenv import load_dotenv
from database.mongo.connection import init_mongo, is_connected as mongo_ok
from binance_connector.binance_conn import init_binance, get_client
from indicators.indicator_start import process_and_save
from binance.spot import Spot  # типізація

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("meowbot.loader_start")


TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"]

def _interval_ms(tf: str) -> int:
    return {"15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}[tf]

def _last_closed_close_time_ms(tf: str, now_ms: int) -> int:
    step = _interval_ms(tf)
    return (now_ms // step) * step - 1

async def _fetch_closed_klines(cli: Spot, symbol: str, interval: str, limit: int = 1500) -> List[List[float]]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    end = _last_closed_close_time_ms(interval, now_ms)
    kl = await asyncio.to_thread(cli.klines, symbol, interval, endTime=end, limit=limit)
    return [k for k in kl if int(k[6]) <= end]

async def main():
    load_dotenv()
    await init_mongo()
    if not mongo_ok():
        raise SystemExit("Mongo не підключено — вийшов.")
    await init_binance()
    cli = get_client()

    symbols = await get_symbols()
    if not symbols:
        log.warning("❌ У колекції symbols немає активних монет!")
        return

    total_saved = 0
    for sym in symbols:
        for tf in TIMEFRAMES:
            log.info("Fetching closed klines for %s %s ...", sym, tf)
            bars = await _fetch_closed_klines(cli, sym, tf, limit=1500)
            saved = await process_and_save(sym, tf, bars)
            total_saved += saved
            log.info("Saved %d new bars for %s %s", saved, sym, tf)

    log.info("Done. Total newly saved: %d", total_saved)

if __name__ == "__main__":
    asyncio.run(main())
