# scripts/check_bars.py
import sys
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.mongo.connection import init_mongo, get_db

TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"]
SYMBOL = "BTCUSDT"

async def main():
    await init_mongo(bot=None, admin_chat_id=None)
    db = get_db()

    bars = db["bars"]
    bars_last = db["bars_last"]

    total_arch = await bars.count_documents({})
    total_last = await bars_last.count_documents({})
    print(f"bars (archive) total: {total_arch}")
    print(f"bars_last (latest per TF) total: {total_last}")

    print("\n— latest per TF (bars_last):")
    for tf in TIMEFRAMES:
        doc = await bars_last.find_one(
            {"symbol": SYMBOL, "timeframe": tf},
            projection={"_id": 0, "symbol": 1, "timeframe": 1, "open_time": 1, "close_time": 1, "close": 1},
        )
        if doc:
            print(f"✅ {SYMBOL} {tf}: open_time={doc.get('open_time')} close_time={doc.get('close_time')} close={doc.get('close')}")
        else:
            print(f"⚠️ немає (bars_last) для {SYMBOL} {tf}")

    print("\n— archive latest doc snapshot (bars):")
    for tf in TIMEFRAMES:
        doc = await bars.find_one(
            {"symbol": SYMBOL, "timeframe": tf},
            sort=[("open_time", -1)],
            projection={"_id": 0, "symbol": 1, "timeframe": 1, "open_time": 1, "close_time": 1, "close": 1},
        )
        if doc:
            print(f"🗂️  {SYMBOL} {tf}: open_time={doc.get('open_time')} close_time={doc.get('close_time')} close={doc.get('close')}")
        else:
            print(f"… архіву ще немає для {SYMBOL} {tf}")

if __name__ == "__main__":
    asyncio.run(main())
