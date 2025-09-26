# database/supabase/domain.py
from typing import List, Dict, Any, Optional
from database.supabase.common import upsert_rows, select_rows, insert_rows

# Зберігання барів/індикаторів
def upsert_bars(rows: List[Dict[str, Any]]) -> None:
    # очікуємо поля: symbol, timeframe, ts (UTC ISO або epoch ms), open/high/low/close, volume, ...
    upsert_rows("bars", rows, on_conflict="symbol,timeframe,ts", ignore_duplicates=True)

def upsert_critical_indicators(rows: List[Dict[str, Any]]) -> None:
    # очікуємо: symbol, timeframe, ts, ... (rsi, macd, atr, ... 45+ фіч)
    upsert_rows("critical_indicators", rows, on_conflict="symbol,timeframe,ts", ignore_duplicates=True)

def get_latest_bars(symbol: str, timeframe: str, limit: int = 100) -> List[Dict[str, Any]]:
    return select_rows(
        "bars",
        eq={"symbol": symbol, "timeframe": timeframe},
        order="ts",
        desc=True,
        limit=limit,
    )

def insert_trade(trade: Dict[str, Any]) -> Dict[str, Any]:
    # trade: {id?, user_id, symbol, side, qty, entry_price, sl, tp, leverage, ts_open, ...}
    rows = insert_rows("trades", [trade])
    return rows[0] if rows else {}

def get_open_positions(user_id: str) -> List[Dict[str, Any]]:
    return select_rows("positions", eq={"user_id": user_id, "is_open": True}, order="ts_open", desc=True)
