import logging
import pandas as pd
from typing import List

# ✅ використовуємо твій файл indicators.py у корені проєкту
from indicators import add_critical_indicators
from database.mongo.connection import get_db

log = logging.getLogger("meowbot.indicator_start")

async def process_and_save(symbol: str, timeframe: str, bars: List[List[float]]) -> int:
    """
    bars: список klines від Binance: [open_time, open, high, low, close, volume, close_time, ...]
    Зберігає лише НОВІ бари (за ключем symbol+timeframe+open_time).
    Повертає кількість збережених документів.
    """
    if not bars:
        return 0

    rows = []
    for k in bars:
        rows.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": int(k[6]),
            }
        )

    df = pd.DataFrame(rows)
    # ✅ додаємо саме ті індикатори, що у тренуванні
    df = add_critical_indicators(df)

    col = get_db()["bars"]
    # idempotent: створить індекс один раз
    await col.create_index(
        [("symbol", 1), ("timeframe", 1), ("open_time", 1)],
        unique=True,
        name="uniq_symbol_tf_open",
    )

    inserted = 0
    for d in df.to_dict(orient="records"):
        res = await col.update_one(
            {"symbol": d["symbol"], "timeframe": d["timeframe"], "open_time": d["open_time"]},
            {"$setOnInsert": d},
            upsert=True,
        )
        if res.upserted_id is not None:
            inserted += 1

    log.info("Saved %s new bars for %s %s", inserted, symbol, timeframe)
    return inserted
