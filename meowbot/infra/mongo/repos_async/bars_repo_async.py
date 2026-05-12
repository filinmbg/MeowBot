from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from pymongo import UpdateOne

from meowbot.core.domain.types import Bar


class BarsRepositoryMongoAsync:
    def __init__(self, db):
        self.db = db

    def _col(self, tf: str):
        return self.db[f"bars_{tf}"]

    async def get_last_close_time(
        self,
        symbol: str,
        tf: str,
        features_ver: str,
    ) -> int | None:
        doc = await self._col(tf).find_one(
            {
                "symbol": symbol,
                "features_ver": features_ver,
            },
            sort=[("close_time", -1)],
            projection={"close_time": 1},
        )
        if not doc:
            return None
        return int(doc["close_time"])

    async def get_tail(
        self,
        *,
        symbol: str,
        tf: str,
        n: int,
        features_ver: str,
        require_features_ok: bool = True,
    ) -> list[Bar]:
        query: dict[str, Any] = {
            "symbol": symbol,
            "features_ver": features_ver,
        }
        if require_features_ok:
            query["features_ok"] = True

        cursor = (
            self._col(tf)
            .find(query)
            .sort("close_time", -1)
            .limit(n)
        )

        docs = await cursor.to_list(length=n)
        docs.reverse()
        return [self._from_doc(doc) for doc in docs]

    async def upsert_many(self, bars: list[Bar]) -> None:
        if not bars:
            return

        ops: list[UpdateOne] = []
        for bar in bars:
            doc = self._to_doc(bar)
            ops.append(
                UpdateOne(
                    {
                        "symbol": bar.symbol,
                        "close_time": bar.close_time,
                    },
                    {"$set": doc},
                    upsert=True,
                )
            )

        await self._col(bars[0].tf).bulk_write(ops, ordered=False)

    def _to_doc(self, bar: Bar) -> dict[str, Any]:
        doc = asdict(bar)
        doc.setdefault("created_at_dt", datetime.now(timezone.utc))
        return doc

    def _from_doc(self, doc: dict[str, Any]) -> Bar:
        data = dict(doc)
        data.pop("_id", None)
        data.pop("created_at_dt", None)
        return Bar(**data)
