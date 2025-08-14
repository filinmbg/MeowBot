import logging
from typing import List
from database.mongo.connection import get_db

log = logging.getLogger("meowbot.symbols")

COLLECTION_NAME = "symbols"

async def get_symbols() -> List[str]:
    """
    Отримати всі активні символи з колекції `symbols`.
    Формат документу: {"symbol": "BTCUSDT", "active": True}
    """
    col = get_db()[COLLECTION_NAME]
    docs = await col.find({"active": True}).sort("symbol", 1).to_list(length=None)
    return [d["symbol"] for d in docs if "symbol" in d]

async def add_symbol(symbol: str, active: bool = True) -> None:
    """
    Додати або оновити символ.
    """
    col = get_db()[COLLECTION_NAME]
    symbol = symbol.upper().strip()
    await col.update_one(
        {"symbol": symbol},
        {"$set": {"symbol": symbol, "active": active}},
        upsert=True
    )
    log.info("Symbol %s set active=%s", symbol, active)

async def remove_symbol(symbol: str) -> None:
    """
    Видалити символ з колекції.
    """
    col = get_db()[COLLECTION_NAME]
    await col.delete_one({"symbol": symbol.upper().strip()})
    log.info("Symbol %s removed", symbol)

async def set_active(symbol: str, active: bool) -> None:
    """
    Змінити прапорець active.
    """
    col = get_db()[COLLECTION_NAME]
    await col.update_one({"symbol": symbol.upper().strip()}, {"$set": {"active": active}})
    log.info("Symbol %s set active=%s", symbol, active)
