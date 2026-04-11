from __future__ import annotations

from dataclasses import asdict
from typing import Any

from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Trade


class TradesRepositoryMongoAsync:
    def __init__(self, db):
        self.col = db["trades"]

    async def create_trade(self, trade: Trade) -> None:
        await self.col.insert_one(self._to_doc(trade))

    async def update_trade(self, trade: Trade) -> None:
        await self.col.replace_one(
            {"trade_id": trade.trade_id},
            self._to_doc(trade),
            upsert=False,
        )

    async def get_open_trades(self) -> list[Trade]:
        cursor = self.col.find({"status": self._enum_value(TradeStatus.OPEN)}).sort("opened_at", 1)
        docs = await cursor.to_list(length=10_000)
        return [self._from_doc(doc) for doc in docs]

    async def get_open_trades_by_user(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        cursor = self.col.find(
            {
                "user_id": user_id,
                "status": self._enum_value(TradeStatus.OPEN),
            }
        ).sort("opened_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def get_trades(
        self,
        *,
        user_id: str | None = None,
        symbol: str | None = None,
        tf: str | None = None,
        mode: str | None = None,
        model_id: str | None = None,
        limit: int = 5000,
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

        cursor = self.col.find(query).sort("opened_at", -1).limit(limit)
        docs = await cursor.to_list(length=limit)
        return [self._from_doc(doc) for doc in docs]

    async def get_closed_trades(self, user_id: str) -> list[dict[str, Any]]:
        cursor = self.col.find(
            {
                "user_id": user_id,
                "status": self._enum_value(TradeStatus.CLOSED),
            }
        ).sort("opened_at", -1)
        return await cursor.to_list(length=10_000)

    async def get_all_closed_trades(self) -> list[dict[str, Any]]:
        cursor = self.col.find(
            {
                "status": self._enum_value(TradeStatus.CLOSED),
            }
        ).sort("opened_at", -1)
        return await cursor.to_list(length=10_000)

    async def get_closed_by_symbol(self, symbol: str) -> list[dict[str, Any]]:
        cursor = self.col.find(
            {
                "symbol": symbol,
                "status": self._enum_value(TradeStatus.CLOSED),
            }
        ).sort("opened_at", -1)
        return await cursor.to_list(length=10_000)

    async def get_recent_closed_trades(self, user_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        query: dict[str, Any] = {
            "status": self._enum_value(TradeStatus.CLOSED),
        }
        if user_id is not None:
            query["user_id"] = user_id

        cursor = self.col.find(query).sort("opened_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    def _to_doc(self, trade: Trade) -> dict[str, Any]:
        payload = asdict(trade)
        payload["side"] = self._enum_value(trade.side)
        payload["status"] = self._enum_value(trade.status)
        return payload

    def _from_doc(self, doc: dict[str, Any]) -> Trade:
        data = dict(doc)
        data.pop("_id", None)
        data["side"] = Side(data["side"]) if not isinstance(data["side"], Side) else data["side"]
        data["status"] = (
            TradeStatus(data["status"]) if not isinstance(data["status"], TradeStatus) else data["status"]
        )
        return Trade(**data)

    def _enum_value(self, value: Any) -> Any:
        return value.value if hasattr(value, "value") else value