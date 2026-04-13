from __future__ import annotations

from typing import Any


class AdminTradeStatsRepositoryMongoAsync:
    def __init__(self, db) -> None:
        self.trades_col = db["trades"]
        self.trade_events_col = db["trade_events"]

    async def get_trade_summary_by_user_ids(
        self,
        *,
        user_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not user_ids:
            return {}

        pipeline = [
            {
                "$match": {
                    "user_id": {"$in": user_ids},
                }
            },
            {
                "$group": {
                    "_id": "$user_id",
                    "open_trades": {
                        "$sum": {
                            "$cond": [{"$eq": ["$status", "OPEN"]}, 1, 0]
                        }
                    },
                    "closed_trades": {
                        "$sum": {
                            "$cond": [{"$eq": ["$status", "CLOSED"]}, 1, 0]
                        }
                    },
                    "wins": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$eq": ["$status", "CLOSED"]},
                                        {"$gt": ["$realized_pnl_usd", 0]},
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                    "losses": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$eq": ["$status", "CLOSED"]},
                                        {"$lt": ["$realized_pnl_usd", 0]},
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                    "realized_pnl_usd": {
                        "$sum": {"$ifNull": ["$realized_pnl_usd", 0.0]}
                    },
                }
            },
        ]

        rows = await self.trades_col.aggregate(pipeline).to_list(length=None)

        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            result[str(row["_id"])] = {
                "open_trades": int(row.get("open_trades", 0) or 0),
                "closed_trades": int(row.get("closed_trades", 0) or 0),
                "wins": int(row.get("wins", 0) or 0),
                "losses": int(row.get("losses", 0) or 0),
                "realized_pnl_usd": float(row.get("realized_pnl_usd", 0.0) or 0.0),
            }
        return result

    async def count_risk_blocks_since(
        self,
        *,
        user_ids: list[str],
        ts_from_ms: int,
    ) -> dict[str, int]:
        if not user_ids:
            return {}

        risk_event_types = [
            "ENTRY_BLOCKED_SUBSCRIPTION",
            "ENTRY_BLOCKED_RISK",
            "ENTRY_BLOCKED_COOLDOWN",
        ]

        pipeline = [
            {
                "$match": {
                    "user_id": {"$in": user_ids},
                    "event_type": {"$in": risk_event_types},
                    "ts": {"$gte": int(ts_from_ms)},
                }
            },
            {
                "$group": {
                    "_id": "$user_id",
                    "cnt": {"$sum": 1},
                }
            },
        ]

        rows = await self.trade_events_col.aggregate(pipeline).to_list(length=None)
        return {str(row["_id"]): int(row.get("cnt", 0) or 0) for row in rows}