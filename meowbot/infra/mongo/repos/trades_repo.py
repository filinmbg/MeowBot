from __future__ import annotations

from dataclasses import asdict
from typing import Any

from meowbot.core.domain.types import Trade
from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.ports.trades_repo import TradesRepository


class TradesRepositoryMongo(TradesRepository):
    def __init__(self, db):
        self.col = db["trades"]

    def create_trade(self, trade: Trade) -> None:
        payload = self._to_doc(trade)
        self.col.insert_one(payload)

    def update_trade(self, trade: Trade) -> None:
        payload = self._to_doc(trade)
        self.col.replace_one({"trade_id": trade.trade_id}, payload, upsert=False)

    def get_trade_by_id(self, trade_id: str) -> Trade | None:
        doc = self.col.find_one({"trade_id": trade_id})
        if not doc:
            return None
        return self._from_doc(doc)

    def get_open_trades(self) -> list[Trade]:
        docs = self.col.find({"status": self._enum_value(TradeStatus.OPEN)}).sort("opened_at", 1)
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
        return payload

    def _from_doc(self, doc: dict[str, Any]) -> Trade:
        data = dict(doc)
        data.pop("_id", None)

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