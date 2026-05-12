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
                                        {"$gt": [self._pnl_value_expression(), 0]},
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
                                        {"$lt": [self._pnl_value_expression(), 0]},
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                    "realized_pnl_usd": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$status", "CLOSED"]},
                                self._pnl_value_expression(),
                                0.0,
                            ]
                        }
                    },
                }
            },
        ]

        rows = await self.trades_col.aggregate(pipeline).to_list(length=len(user_ids))

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

    async def get_strategy_summary_by_user_ids(
        self,
        *,
        user_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not user_ids:
            return []

        pipeline = [
            {
                "$match": {
                    "user_id": {"$in": user_ids},
                }
            },
            {
                "$project": {
                    "status": 1,
                    "strategy_version": {
                        "$toLower": {"$ifNull": ["$strategy_version", "v1"]}
                    },
                    "subscription_type": {
                        "$toLower": {"$ifNull": ["$subscription_type", "unknown"]}
                    },
                    "pnl_value": self._pnl_value_expression(),
                }
            },
            {
                "$group": {
                    "_id": {
                        "strategy_version": "$strategy_version",
                        "subscription_type": "$subscription_type",
                    },
                    "open_trades": {
                        "$sum": {"$cond": [{"$eq": ["$status", "OPEN"]}, 1, 0]}
                    },
                    "closed_trades": {
                        "$sum": {"$cond": [{"$eq": ["$status", "CLOSED"]}, 1, 0]}
                    },
                    "wins": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$eq": ["$status", "CLOSED"]},
                                        {"$gt": ["$pnl_value", 0]},
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
                                        {"$lt": ["$pnl_value", 0]},
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                    "realized_pnl_usd": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$status", "CLOSED"]},
                                "$pnl_value",
                                0.0,
                            ]
                        }
                    },
                }
            },
            {
                "$sort": {
                    "_id.subscription_type": 1,
                    "_id.strategy_version": 1,
                }
            },
        ]

        rows = await self.trades_col.aggregate(pipeline).to_list(length=100)
        result: list[dict[str, Any]] = []
        for row in rows:
            key = row.get("_id") or {}
            closed_trades = int(row.get("closed_trades", 0) or 0)
            wins = int(row.get("wins", 0) or 0)
            result.append(
                {
                    "subscription_type": str(key.get("subscription_type") or "unknown").lower(),
                    "strategy_version": str(key.get("strategy_version") or "v1").lower(),
                    "open_trades": int(row.get("open_trades", 0) or 0),
                    "closed_trades": closed_trades,
                    "wins": wins,
                    "losses": int(row.get("losses", 0) or 0),
                    "realized_pnl_usd": float(row.get("realized_pnl_usd", 0.0) or 0.0),
                    "winrate": (wins / closed_trades * 100.0) if closed_trades else 0.0,
                }
            )
        return result

    def _pnl_value_expression(self) -> dict[str, Any]:
        local_pnl = {
            "$convert": {
                "input": "$realized_pnl_usd",
                "to": "double",
                "onError": 0.0,
                "onNull": 0.0,
            }
        }
        exchange_pnl = {
            "$convert": {
                "input": {
                    "$ifNull": [
                        "$exchange_net_realized_pnl_usd",
                        {
                            "$ifNull": [
                                "$exchange_net_realized_pnl_usdt",
                                {"$ifNull": ["$exchange_realized_pnl_usd", "$exchange_realized_pnl_usdt"]},
                            ]
                        },
                    ]
                },
                "to": "double",
                "onError": 0.0,
                "onNull": 0.0,
            }
        }
        return {
            "$cond": [
                {
                    "$and": [
                        {"$eq": [{"$toLower": {"$ifNull": ["$mode", ""]}}, "live"]},
                        {
                            "$ne": [
                                {
                                    "$ifNull": [
                                        "$exchange_net_realized_pnl_usd",
                                        {
                                            "$ifNull": [
                                                "$exchange_net_realized_pnl_usdt",
                                                {"$ifNull": ["$exchange_realized_pnl_usd", "$exchange_realized_pnl_usdt"]},
                                            ]
                                        },
                                    ]
                                },
                                None,
                            ]
                        },
                    ]
                },
                exchange_pnl,
                local_pnl,
            ]
        }

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

        rows = await self.trade_events_col.aggregate(pipeline).to_list(length=len(user_ids))
        return {str(row["_id"]): int(row.get("cnt", 0) or 0) for row in rows}
