# test/load_bars_to_mongo_async.py
import os
import time
import asyncio
from datetime import datetime
import httpx
from pymongo import UpdateOne

# 🔌 твій конектор
from database.mongo.connection import init_mongo, get_db, is_connected

# опціонально: дефолт DB
os.environ.setdefault("MONGO_DB_NAME", "test")

BINANCE_SPOT = "https://api.binance.com"
BINANCE_FUT  = "https://fapi.binance.com"
TF_MAP = {"1m":"1m","15m":"15m","30m":"30m","1h":"1h","4h":"4h","1d":"1d"}

async def get_active_symbols_from_db() -> list[str]:
    # мінімальна реалізація, щоб не тягнути весь модуль
    db = get_db()
    docs = await db["symbols"].find({"active": True}).sort("symbol", 1).to_list(length=None)
    return [d["symbol"] for d in docs if "symbol" in d]

async def ensure_indexes():
    db = get_db()
    bars = db["bars"]
    await bars.create_index("_id", unique=True)
    await bars.create_index([("symbol", 1), ("tf", 1), ("close_time", 1)])

async def fetch_write_symbol_tf(db, http, symbol: str, tf: str, market: str, start_iso: str, limit=1500):
    start_ms = int(datetime.fromisoformat(start_iso).timestamp() * 1000)
    base = BINANCE_FUT if market == "futures" else BINANCE_SPOT
    path = "/fapi/v1/klines" if market == "futures" else "/api/v3/klines"

    next_start = start_ms
    coll = db["bars"]

    while True:
        params = {
            "symbol": symbol,
            "interval": TF_MAP[tf],
            "startTime": next_start,
            "limit": min(limit, 1500 if market == "futures" else 1000),
        }
        r = await http.get(base + path, params=params)
        r.raise_for_status()
        data = r.json()
        if not data:
            break

        ops = []
        last_ct = None
        now_ms = int(time.time() * 1000)
        for k in data:
            open_time = int(k[0]); close_time = int(k[6])
            doc = {
                "_id": f"{symbol}:{tf}:{close_time}",
                "symbol": symbol, "tf": tf,
                "open_time": open_time, "close_time": close_time,
                "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]),
                "volume": float(k[5]),
                "quote_volume": float(k[7]), "num_trades": int(k[8]),
                "taker_buy_base": float(k[9]), "taker_buy_quote": float(k[10]),
                "exchange": "binance", "market": market,
                "inserted_at": now_ms
            }
            ops.append(UpdateOne({"_id": doc["_id"]}, {"$setOnInsert": doc}, upsert=True))
            last_ct = close_time

        if ops:
            # motor.bulk_write → через to_thread, простіше викликати синхронний драйвер через run_in_executor
            await asyncio.to_thread(coll.bulk_write, ops, False)

        next_start = (last_ct or next_start) + 1
        # якщо менше ліміту — на цьому інтервальному обрізку все
        if len(data) < params["limit"]:
            break
    print(f"[OK] {symbol} {tf} ({market})")

async def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=None, help="Якщо не задано — беремо активні з БД (collection symbols)")
    ap.add_argument("--timeframes", nargs="+", default=["1m","15m","30m","1h","4h","1d"])
    ap.add_argument("--market", choices=["spot","futures"], default="futures")
    ap.add_argument("--start", default="2025-09-01T00:00:00+03:00", help="ISO-час локальний або UTC")
    ap.add_argument("--concurrency", type=int, default=6)
    args = ap.parse_args()

    await init_mongo()
    if not is_connected():
        raise RuntimeError("Mongo не ініціалізовано. Перевір .env (MONGO_URI, MONGO_DB_NAME).")

    await ensure_indexes()
    db = get_db()

    # символи
    symbols = args.symbols or (await get_active_symbols_from_db())
    if not symbols:
        symbols = ["BTCUSDT"]
        print("[WARN] У symbols порожньо, використовую дефолт BTCUSDT")

    limits = httpx.Limits(max_keepalive_connections=10, max_connections=10)
    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(limits=limits, timeout=timeout, http2=True) as http:
        sem = asyncio.Semaphore(args.concurrency)
        tasks = []
        for s in symbols:
            for tf in args.timeframes:
                async def run_one(sym=s, t=tf):
                    async with sem:
                        await fetch_write_symbol_tf(db, http, sym, t, args.market, args.start)
                tasks.append(asyncio.create_task(run_one()))
        await asyncio.gather(*tasks)

    print("[DONE] load complete")

if __name__ == "__main__":
    asyncio.run(main())
