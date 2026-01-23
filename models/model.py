import logging
from typing import Literal
from database.mongo.connection import get_db

log = logging.getLogger("meowbot.model")

Action = Literal["LONG", "SHORT", "HOLD"]

async def decide_signal(symbol: str, timeframe: str) -> Action:
    """
    Простий стаб на базі твоїх фіч:
    - якщо macd > 0 і close > ema_20 і rsi_14 < 70 -> LONG
    - якщо macd < 0 і close < ema_20 і rsi_14 > 30 -> SHORT
    - інакше HOLD
    Це тимчасово; згодом підставимо виклик реальної моделі.
    """
    col = get_db()["bars"]
    doc = await col.find({"symbol": symbol, "timeframe": timeframe}).sort("open_time", -1).limit(1).to_list(1)
    if not doc:
        return "HOLD"

    d = doc[0]
    close = float(d.get("close", 0.0))
    ema20 = float(d.get("ema_20", 0.0))
    rsi14 = float(d.get("rsi_14", 50.0))
    macd = float(d.get("macd", 0.0))

    if macd > 0 and close > ema20 and rsi14 < 70:
        return "LONG"
    if macd < 0 and close < ema20 and rsi14 > 30:
        return "SHORT"
    return "HOLD"
