from __future__ import annotations

from dataclasses import asdict, fields
from enum import Enum
from typing import Any

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from meowbot.core.domain.types import Trade
from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.ports.trades_repo import TradesRepository

MAX_TRADE_DOC_STRING_LENGTH = 2000
MAX_TRADE_DOC_LIST_ITEMS = 200
COMPACT_PROTECTION_KEYS = {
    "symbol",
    "trade_id",
    "entry_order_id",
    "actual_position_amt",
    "protection_status",
    "exchange_error_code",
    "exchange_error_message",
    "tp_order_count_requested",
    "tp_order_count_created",
    "stop_created",
    "tp_count",
    "tp_levels",
    "tp_close_fractions",
    "tp_order_ids",
    "tp_algo_ids",
    "sl_order_id",
    "sl_algo_id",
}
TRADE_FIELD_NAMES = {field.name for field in fields(Trade)}


class TradesRepositoryMongo(TradesRepository):
    def __init__(self, db):
        self.col = db["trades"]

    def ensure_indexes(self) -> None:
        self.col.create_index([("trade_id", ASCENDING)], name="idx_trade_id_unique", unique=True)
        self.col.create_index(
            [("user_id", ASCENDING), ("symbol", ASCENDING), ("status", ASCENDING)],
            name="uniq_open_trade_user_symbol",
            unique=True,
            partialFilterExpression={"status": self._enum_value(TradeStatus.OPEN)},
        )

    def create_trade(self, trade: Trade) -> None:
        payload = self._to_doc(trade)
        try:
            self.col.insert_one(payload)
        except DuplicateKeyError as exc:
            raise RuntimeError(
                "duplicate_open_trade:"
                f"user_id={trade.user_id};symbol={str(trade.symbol).upper()};trade_id={trade.trade_id}"
            ) from exc

    def update_trade(self, trade: Trade) -> None:
        payload = self._to_doc(trade)
        self.col.replace_one({"trade_id": trade.trade_id}, payload, upsert=False)

    def get_trade_by_id(self, trade_id: str) -> Trade | None:
        doc = self.col.find_one({"trade_id": trade_id})
        if not doc:
            return None
        return self._from_doc(doc)

    def get_open_trades(self) -> list[Trade]:
        docs = self.col.find(
            {
                "status": self._enum_value(TradeStatus.OPEN),
                "user_id": {"$ne": "tg:123456789"},
            }
        ).sort("opened_at", 1)
        return [self._from_doc(doc) for doc in docs]

    def get_trades(
        self,
        *,
        user_id: str | None = None,
        symbol: str | None = None,
        tf: str | None = None,
        mode: str | None = None,
        model_id: str | None = None,
        limit: int = 1000,
    ) -> list[Trade]:
        query: dict[str, Any] = {}

        if user_id is not None:
            query["user_id"] = user_id
        if symbol is not None:
            query["symbol"] = symbol
        if tf is not None:
            query["tf_entry"] = tf
        if mode is not None:
            query["mode"] = mode
        if model_id is not None:
            query["model_id"] = model_id

        docs = (
            self.col.find(query)
            .sort("opened_at", -1)
            .limit(limit)
        )
        return [self._from_doc(doc) for doc in docs]

    def _to_doc(self, trade: Trade) -> dict[str, Any]:
        payload = asdict(trade)
        payload["side"] = self._enum_value(trade.side)
        payload["status"] = self._enum_value(trade.status)
        return self._compact_trade_doc(payload)

    def _compact_trade_doc(self, payload: dict[str, Any]) -> dict[str, Any]:
        compacted = self._compact_value(payload)
        if isinstance(compacted.get("protection_details"), dict):
            protection = compacted["protection_details"]
            compacted["protection_details"] = {
                key: protection[key]
                for key in COMPACT_PROTECTION_KEYS
                if key in protection
            }
            dropped = sorted(set(protection) - COMPACT_PROTECTION_KEYS)
            if dropped:
                compacted["protection_details"]["dropped_verbose_keys"] = dropped[:50]
        return compacted

    def _compact_value(self, value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {
                str(key): self._compact_value(item)
                for key, item in value.items()
                if key != "_id"
            }
        if isinstance(value, list):
            items = [self._compact_value(item) for item in value[:MAX_TRADE_DOC_LIST_ITEMS]]
            if len(value) > MAX_TRADE_DOC_LIST_ITEMS:
                items.append({"truncated_items": len(value) - MAX_TRADE_DOC_LIST_ITEMS})
            return items
        if isinstance(value, tuple):
            return self._compact_value(list(value))
        if isinstance(value, bytes):
            return f"<bytes:{len(value)}>"
        if isinstance(value, str) and len(value) > MAX_TRADE_DOC_STRING_LENGTH:
            return f"{value[:MAX_TRADE_DOC_STRING_LENGTH]}...<truncated:{len(value) - MAX_TRADE_DOC_STRING_LENGTH}>"
        return value

    def _from_doc(self, doc: dict[str, Any]) -> Trade:
        data = dict(doc)
        data.pop("_id", None)
        data.pop("is_open", None)
        data.pop("tp1_hit", None)
        data.pop("tp2_hit", None)
        data.pop("tp3_hit", None)
        data.pop("closed", None)
        data = {key: value for key, value in data.items() if key in TRADE_FIELD_NAMES}

        side_raw = data.get("side")
        status_raw = data.get("status")

        data["side"] = Side(side_raw) if not isinstance(side_raw, Side) else side_raw
        data["status"] = (
            TradeStatus(status_raw)
            if not isinstance(status_raw, TradeStatus)
            else status_raw
        )

        return Trade(**data)

    def _enum_value(self, value: Any) -> Any:
        return value.value if hasattr(value, "value") else value
