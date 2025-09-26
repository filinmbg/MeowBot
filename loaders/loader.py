import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, List
from database.mongo.symbols import get_symbols
from dotenv import load_dotenv
from binance.spot import Spot
from database.mongo.connection import init_mongo, get_db, is_connected as mongo_ok
from binance_connector.binance_conn import init_binance, get_client
from indicators.indicator_start import process_and_save
from models.model import decide_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("meowbot.loader")

TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"]

def _interval_ms(tf: str) -> int:
    return {"15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}[tf]

def _last_closed_close_time_ms(tf: str) -> int:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    step = _interval_ms(tf)
    return (now_ms // step) * step - 1

async def _fetch_since_open_time(cli: Spot, symbol: str, tf: str, last_open_time: Optional[int]) -> List[List[float]]:
    end = _last_closed_close_time_ms(tf)
    if last_open_time is None:
        return []
    kl = await asyncio.to_thread(get_client().klines, symbol, tf, endTime=end, limit=500)
    return [k for k in kl if int(k[0]) > last_open_time and int(k[6]) <= end]

async def _last_open_time_in_db(symbol: str, tf: str) -> Optional[int]:
    col = get_db()["bars"]
    doc = await col.find({"symbol": symbol, "timeframe": tf}).sort("open_time", -1).limit(1).to_list(1)
    if not doc:
        return None
    return int(doc[0]["open_time"])

async def _fetch_last_n_closed(cli: Spot, symbol: str, tf: str, n: int = 150) -> List[List[float]]:
    end = _last_closed_close_time_ms(tf)
    kl = await asyncio.to_thread(cli.klines, symbol, tf, endTime=end, limit=n)
    return [k for k in kl if int(k[6]) <= end][-n:]

async def _handle_new_bar(symbol: str, tf: str):
    cli = get_client()
    last150 = await _fetch_last_n_closed(cli, symbol, tf, 150)
    await process_and_save(symbol, tf, last150)
    action = await decide_signal(symbol, tf)
    log.info("Model decision for %s %s: %s", symbol, tf, action)

async def loop_runner():
    load_dotenv()
    await init_mongo()
    if not mongo_ok():
        raise SystemExit("Mongo не підключено — вийшов.")
    await init_binance()
    get_client()  # ensure ready

    symbols = await get_symbols()
    if not symbols:
        log.warning("❌ У колекції symbols немає активних монет!")
        return

    log.info("Starting 1-min loop...")
    while True:
        try:
            for sym in symbols:
                for tf in TIMEFRAMES:
                    last_open = await _last_open_time_in_db(sym, tf)
                    new_closed = await _fetch_since_open_time(get_client(), sym, tf, last_open)
                    if not new_closed:
                        continue
                    saved = await process_and_save(sym, tf, new_closed)
                    if saved > 0:
                        await _handle_new_bar(sym, tf)
        except Exception as e:
            log.exception("Loop error: %s", e)
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(loop_runner())
