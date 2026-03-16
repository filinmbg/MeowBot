from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional

from pymongo.database import Database

from meowbot.core.domain.types import Trade
from meowbot.core.domain.enums import TradeStatus
from meowbot.core.ports.trades_repo import TradesRepository


def _trade_to_doc(t: Trade) -> dict:
    d = asdict(t)
    # зручно мати trade_id як _id
    d["_id"] = t.trade_id
    return d


def _doc_to_trade(doc: dict) -> Trade:
    doc = dict(doc)
    doc.pop("_id", None)
    # enums зберігаються як строки, тому конструктор Trade прийме їх як str?
    # НІ — у нас Side/TradeStatus це Enum(str, Enum), тому працює зі строками.
    return Trade(**doc)


class TradesRepositoryMongo(TradesRepository):
    def __init__(self, db: Database, collection_name: str = "trades"):
        self.db = db
        self.col = db[collection_name]

    def ping(self) -> bool:
        try:
            self.db.command("ping")
            return True
        except Exception:
            return False

    def get_open_trades(self) -> List[Trade]:
        cur = self.col.find({"status": TradeStatus.OPEN.value})
        return [_doc_to_trade(x) for x in cur]

    def get_open_trade_by_symbol(self, symbol: str) -> Optional[Trade]:
        doc = self.col.find_one({"status": TradeStatus.OPEN.value, "symbol": symbol})
        return _doc_to_trade(doc) if doc else None

    def was_entry_bar_used(self, symbol: str, tf: str, close_time: int) -> bool:
        doc = self.col.find_one(
            {"symbol": symbol, "tf_entry": tf, "entry_bar_close_time": close_time},
            {"_id": 1},
        )
        return doc is not None

    def create_trade(self, trade: Trade) -> None:
        doc = _trade_to_doc(trade)
        self.col.insert_one(doc)

    def update_trade(self, trade: Trade) -> None:
        doc = _trade_to_doc(trade)
        _id = doc.pop("_id")
        self.col.replace_one({"_id": _id}, doc, upsert=True)

    def close_trade(self, trade_id: str, closed_at: int, close_price: float, reason: str, pnl_usd: float) -> None:
        self.col.update_one(
            {"_id": trade_id},
            {
                "$set": {
                    "status": TradeStatus.CLOSED.value,
                    "closed_at": closed_at,
                    "close_price": close_price,
                    "exit_reason": reason,
                    "realized_pnl_usd": pnl_usd,
                }
            },
        )