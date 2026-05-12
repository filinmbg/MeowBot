from __future__ import annotations

import logging
import time
from dataclasses import asdict, fields
from enum import Enum
from typing import Any

from pymongo import ASCENDING, UpdateOne
from pymongo.errors import DuplicateKeyError, OperationFailure

from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Trade


log = logging.getLogger("meowbot")
DEFAULT_TRADE_ROWS_LIMIT = 100
MAX_TRADE_ROWS_LIMIT = 1_000
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


class TradesRepositoryMongoAsync:
    def __init__(self, db):
        self.col = db["trades"]

    async def ensure_indexes(self) -> None:
        await self._ensure_index([("trade_id", ASCENDING)], name="idx_trade_id_unique", unique=True)
        await self._ensure_index(
            [("user_id", ASCENDING), ("symbol", ASCENDING), ("status", ASCENDING)],
            name="uniq_open_trade_user_symbol",
            unique=True,
            partialFilterExpression={"status": self._enum_value(TradeStatus.OPEN)},
        )

    async def _ensure_index(self, keys, *, name: str, **options) -> None:
        try:
            await self.col.create_index(keys, name=name, **options)
        except OperationFailure as exc:
            if getattr(exc, "code", None) == 85:
                log.warning("[mongo] compatible index already exists with another name target=%s name=%s", keys, name)
                return
            raise

    def _safe_limit(self, limit: int | None, *, default: int = DEFAULT_TRADE_ROWS_LIMIT) -> int:
        try:
            value = int(limit if limit is not None else default)
        except (TypeError, ValueError):
            value = default
        return max(1, min(value, MAX_TRADE_ROWS_LIMIT))

    async def create_trade(self, trade: Trade) -> None:
        try:
            await self.col.insert_one(self._to_doc(trade))
        except DuplicateKeyError as exc:
            raise RuntimeError(
                "duplicate_open_trade:"
                f"user_id={trade.user_id};symbol={str(trade.symbol).upper()};trade_id={trade.trade_id}"
            ) from exc

    async def update_trade(self, trade: Trade) -> None:
        await self.col.update_one(
            {"trade_id": trade.trade_id},
            {"$set": self._to_update_doc(trade)},
            upsert=False,
        )

    async def upsert_trade(self, trade: Trade) -> None:
        try:
            await self.col.update_one(
                {"trade_id": trade.trade_id},
                {"$set": self._to_update_doc(trade)},
                upsert=True,
            )
        except DuplicateKeyError as exc:
            raise RuntimeError(
                "duplicate_open_trade:"
                f"user_id={trade.user_id};symbol={str(trade.symbol).upper()};trade_id={trade.trade_id}"
            ) from exc

    async def mark_entry_notification_sent(self, *, trade_id: str, sent_at: int) -> None:
        await self.col.update_one(
            {"trade_id": str(trade_id)},
            {
                "$set": {
                    "entry_notification_sent": True,
                    "entry_notification_sent_at": int(sent_at),
                    "entry_notification_last_send_attempt_at": int(sent_at),
                    "entry_notification_last_send_exception": None,
                    "updated_at": int(sent_at),
                },
                "$setOnInsert": {"trade_id": str(trade_id)},
            },
            upsert=True,
        )

    async def record_entry_notification_attempt(self, *, trade_id: str, attempted_at: int) -> None:
        await self.col.update_one(
            {"trade_id": str(trade_id)},
            {
                "$set": {
                    "entry_notification_last_send_attempt_at": int(attempted_at),
                    "updated_at": int(attempted_at),
                },
                "$inc": {"entry_notification_send_attempt_count": 1},
                "$setOnInsert": {"trade_id": str(trade_id)},
            },
            upsert=True,
        )

    async def record_entry_notification_failure(self, *, trade_id: str, attempted_at: int, exception: str) -> None:
        await self.col.update_one(
            {"trade_id": str(trade_id)},
            {
                "$set": {
                    "entry_notification_last_send_attempt_at": int(attempted_at),
                    "entry_notification_last_send_exception": str(exception),
                    "updated_at": int(attempted_at),
                },
                "$setOnInsert": {"trade_id": str(trade_id)},
            },
            upsert=True,
        )

    async def mark_risk_warning_sent(self, *, trade_id: str, sent_at: int) -> None:
        await self.col.update_one(
            {"trade_id": str(trade_id)},
            {
                "$set": {
                    "risk_warning_sent": True,
                    "risk_warning_sent_at": int(sent_at),
                    "updated_at": int(sent_at),
                }
            },
        )

    async def bulk_upsert_trades(self, trades: list[Trade]) -> None:
        if not trades:
            return
        operations = [
            UpdateOne(
                {"trade_id": trade.trade_id},
                {"$set": self._to_update_doc(trade)},
                upsert=True,
            )
            for trade in trades
        ]
        await self.col.bulk_write(operations, ordered=False)

    async def get_open_trades(self) -> list[Trade]:
        cursor = (
            self.col.find(
                {
                    "status": self._enum_value(TradeStatus.OPEN),
                    "user_id": {"$ne": "tg:123456789"},
                }
            )
            .sort("opened_at", 1)
            .limit(MAX_TRADE_ROWS_LIMIT)
        )
        docs = await cursor.to_list(length=MAX_TRADE_ROWS_LIMIT)
        return [self._from_doc(doc) for doc in docs]

    async def cleanup_duplicate_open_trades(self, *, now_ms: int | None = None) -> list[dict[str, Any]]:
        now_value = int(now_ms or time.time() * 1000)
        open_status = self._enum_value(TradeStatus.OPEN)
        pipeline = [
            {"$match": {"status": open_status}},
            {
                "$group": {
                    "_id": {"user_id": "$user_id", "symbol": "$symbol"},
                    "count": {"$sum": 1},
                    "trade_ids": {"$push": "$trade_id"},
                }
            },
            {"$match": {"count": {"$gt": 1}}},
        ]
        groups = await self.col.aggregate(pipeline).to_list(length=500)
        summaries: list[dict[str, Any]] = []
        for group in groups:
            key = group.get("_id") or {}
            user_id = str(key.get("user_id") or "")
            symbol = str(key.get("symbol") or "").upper()
            docs = await self.col.find({"user_id": user_id, "symbol": symbol, "status": open_status}).to_list(length=100)
            if not docs:
                continue
            canonical = self._select_canonical_open_trade_doc(docs)
            canonical_trade_id = str(canonical.get("trade_id") or "")
            superseded_ids = [
                str(doc.get("trade_id"))
                for doc in docs
                if str(doc.get("trade_id") or "") != canonical_trade_id
            ]
            if not superseded_ids:
                continue
            await self.col.update_many(
                {"trade_id": {"$in": superseded_ids}},
                {
                    "$set": {
                        "status": self._enum_value(TradeStatus.DUPLICATE_SUPERSEDED),
                        "is_open": False,
                        "closed": True,
                        "closed_at": now_value,
                        "exit_reason": "duplicate_superseded",
                        "duplicate_superseded_by": canonical_trade_id,
                        "duplicate_cleanup_at": now_value,
                        "updated_at": now_value,
                    }
                },
            )
            summary = {
                "user_id": user_id,
                "symbol": symbol,
                "duplicate_count": len(docs),
                "canonical_trade_id": canonical_trade_id,
                "superseded_trade_ids": superseded_ids,
            }
            summaries.append(summary)
            log.error(
                "[DUPLICATE_OPEN_TRADES_FOUND] user_id=%s symbol=%s duplicate_count=%s canonical_trade_id=%s superseded_trade_ids=%s",
                user_id,
                symbol,
                len(docs),
                canonical_trade_id,
                superseded_ids,
            )
        return summaries

    async def suspend_placeholder_open_trades(self, *, now_ms: int | None = None) -> int:
        now_value = int(now_ms or time.time() * 1000)
        result = await self.col.update_many(
            {
                "user_id": "tg:123456789",
                "status": self._enum_value(TradeStatus.OPEN),
            },
            {
                "$set": {
                    "status": self._enum_value(TradeStatus.DUPLICATE_SUPERSEDED),
                    "is_open": False,
                    "closed": True,
                    "closed_at": now_value,
                    "exit_reason": "placeholder_debug_user_suspended",
                    "duplicate_cleanup_at": now_value,
                    "updated_at": now_value,
                }
            },
        )
        count = int(result.modified_count)
        if count:
            log.warning("[startup] suspended placeholder debug open trades user=tg:123456789 count=%s", count)
        return count

    def _select_canonical_open_trade_doc(self, docs: list[dict[str, Any]]) -> dict[str, Any]:
        def score(doc: dict[str, Any]) -> tuple[int, int, int, int]:
            mode = str(doc.get("mode") or "").lower()
            entry_order_id = 1 if doc.get("exchange_entry_order_id") else 0
            position_amt = self._float_or_zero(doc.get("exchange_position_amt"))
            live_confirmed = 1 if mode == "live" and (entry_order_id or abs(position_amt) > 0) else 0
            active_exchange_position = 1 if abs(position_amt) > 0 else 0
            created_or_opened = int(doc.get("created_at") or doc.get("opened_at") or 0)
            opened_at = int(doc.get("opened_at") or 0)
            return (live_confirmed, active_exchange_position, created_or_opened, opened_at)

        return max(docs, key=score)

    @staticmethod
    def _float_or_zero(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    async def get_open_live_trades(self, *, limit: int = MAX_TRADE_ROWS_LIMIT) -> list[Trade]:
        safe_limit = self._safe_limit(limit, default=MAX_TRADE_ROWS_LIMIT)
        cursor = (
            self.col.find(
                {
                    "mode": "live",
                    "status": self._enum_value(TradeStatus.OPEN),
                }
            )
            .sort("opened_at", 1)
            .limit(safe_limit)
        )
        docs = await cursor.to_list(length=safe_limit)
        return [self._from_doc(doc) for doc in docs]

    async def get_open_live_trades_by_user(self, user_id: str, *, limit: int = 200) -> list[Trade]:
        safe_limit = self._safe_limit(limit, default=200)
        cursor = (
            self.col.find(
                {
                    "user_id": user_id,
                    "mode": "live",
                    "status": self._enum_value(TradeStatus.OPEN),
                }
            )
            .sort("opened_at", 1)
            .limit(safe_limit)
        )
        docs = await cursor.to_list(length=safe_limit)
        return [self._from_doc(doc) for doc in docs]

    async def list_open_live_user_ids(self) -> list[str]:
        rows = await self.col.distinct(
            "user_id",
            {
                "mode": "live",
                "status": self._enum_value(TradeStatus.OPEN),
            },
        )
        return [str(row) for row in rows if row]

    async def get_trade_by_trade_id(self, trade_id: str) -> Trade | None:
        doc = await self.col.find_one({"trade_id": trade_id})
        return self._from_doc(doc) if doc else None

    async def get_open_trade_by_exchange_order_id(self, order_id: str) -> Trade | None:
        doc = await self.col.find_one(
            {
                "status": self._enum_value(TradeStatus.OPEN),
                "exchange_order_ids": str(order_id),
            }
        )
        return self._from_doc(doc) if doc else None

    async def get_open_trades_by_user(
        self,
        user_id: str,
        limit: int = 20,
        *,
        mode: str | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = self._safe_limit(limit, default=20)
        query: dict[str, Any] = {
            "user_id": user_id,
            "status": self._enum_value(TradeStatus.OPEN),
        }
        if mode is not None:
            query["mode"] = mode

        cursor = self.col.find(query).sort("opened_at", -1).limit(safe_limit)
        return await cursor.to_list(length=safe_limit)

    async def close_open_trades_by_user_mode(
        self,
        *,
        user_id: str,
        mode: str,
        closed_at: int,
        reason: str,
    ) -> int:
        result = await self.col.update_many(
            {
                "user_id": user_id,
                "mode": mode,
                "status": self._enum_value(TradeStatus.OPEN),
            },
            {
                "$set": {
                    "status": self._enum_value(TradeStatus.CLOSED),
                    "is_open": False,
                    "closed": True,
                    "closed_at": int(closed_at),
                    "exit_reason": reason,
                    "remaining_pct": 0.0,
                    "qty_remaining": 0.0,
                    "updated_at": int(closed_at),
                }
            },
        )
        return int(result.modified_count)

    async def get_open_trade_entity_by_user_symbol(
        self,
        user_id: str,
        symbol: str,
        *,
        mode: str | None = None,
    ) -> Trade | None:
        query: dict[str, Any] = {
            "user_id": user_id,
            "symbol": str(symbol).upper(),
            "status": self._enum_value(TradeStatus.OPEN),
        }
        if mode is not None:
            query["mode"] = mode

        doc = await self.col.find_one(query, sort=[("opened_at", 1)])
        return self._from_doc(doc) if doc else None

    async def get_trade_rows_by_user(
        self,
        user_id: str,
        *,
        mode: str | None = None,
        limit: int = DEFAULT_TRADE_ROWS_LIMIT,
    ) -> list[dict[str, Any]]:
        started = time.perf_counter()
        safe_limit = self._safe_limit(limit)
        query: dict[str, Any] = {"user_id": user_id}
        if mode is not None:
            query["mode"] = mode

        cursor = self.col.find(query).sort("opened_at", -1).limit(safe_limit)
        rows = await cursor.to_list(length=safe_limit)
        duration_ms = int((time.perf_counter() - started) * 1000)
        log.debug(
            "[mongo] trades.get_trade_rows_by_user user=%s mode=%s duration_ms=%s count=%s limit=%s",
            user_id,
            mode,
            duration_ms,
            len(rows),
            safe_limit,
        )
        return rows

    async def get_user_trade_stats(self, user_id: str, *, mode: str | None = None) -> dict[str, Any]:
        query: dict[str, Any] = {"user_id": user_id}
        if mode is not None:
            query["mode"] = mode
        return await self._aggregate_trade_stats(query=query, operation="trades.get_user_trade_stats", user_id=user_id)

    async def get_global_trade_stats(self, *, mode: str | None = None) -> dict[str, Any]:
        query: dict[str, Any] = {}
        if mode is not None:
            query["mode"] = mode
        return await self._aggregate_trade_stats(query=query, operation="trades.get_global_trade_stats")

    async def get_strategy_summary_by_user_ids(
        self,
        *,
        user_ids: list[str],
        mode: str | None = None,
    ) -> list[dict[str, Any]]:
        started = time.perf_counter()
        if not user_ids:
            return []

        match: dict[str, Any] = {"user_id": {"$in": [str(user_id) for user_id in user_ids]}}
        if mode is not None:
            match["mode"] = mode

        open_status = self._enum_value(TradeStatus.OPEN)
        closed_status = self._enum_value(TradeStatus.CLOSED)
        pipeline = [
            {"$match": match},
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
                    "open_trades": {"$sum": {"$cond": [{"$eq": ["$status", open_status]}, 1, 0]}},
                    "closed_trades": {"$sum": {"$cond": [{"$eq": ["$status", closed_status]}, 1, 0]}},
                    "wins": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$eq": ["$status", closed_status]},
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
                                        {"$eq": ["$status", closed_status]},
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
                                {"$eq": ["$status", closed_status]},
                                "$pnl_value",
                                0.0,
                            ]
                        }
                    },
                }
            },
            {"$sort": {"_id.subscription_type": 1, "_id.strategy_version": 1}},
        ]

        rows = await self.col.aggregate(pipeline).to_list(length=100)
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
        duration_ms = int((time.perf_counter() - started) * 1000)
        log.debug(
            "[stats] version split operation=trades.get_strategy_summary_by_user_ids mode=%s duration_ms=%s groups=%s",
            mode,
            duration_ms,
            len(result),
        )
        return result

    async def get_symbol_trade_stats(self, symbol: str, *, mode: str | None = None) -> dict[str, Any]:
        query: dict[str, Any] = {"symbol": symbol}
        if mode is not None:
            query["mode"] = mode
        return await self._aggregate_trade_stats(
            query=query,
            operation="trades.get_symbol_trade_stats",
            symbol=symbol,
        )

    async def _aggregate_trade_stats(
        self,
        *,
        query: dict[str, Any],
        operation: str,
        user_id: str | None = None,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        open_status = self._enum_value(TradeStatus.OPEN)
        closed_status = self._enum_value(TradeStatus.CLOSED)
        pipeline = [
            {"$match": query},
            {
                "$project": {
                    "status": 1,
                    "pnl_value": self._pnl_value_expression(),
                }
            },
            {
                "$group": {
                    "_id": None,
                    "open_trades": {"$sum": {"$cond": [{"$eq": ["$status", open_status]}, 1, 0]}},
                    "closed_trades": {"$sum": {"$cond": [{"$eq": ["$status", closed_status]}, 1, 0]}},
                    "wins": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$eq": ["$status", closed_status]},
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
                                        {"$eq": ["$status", closed_status]},
                                        {"$lte": ["$pnl_value", 0]},
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
                                {"$eq": ["$status", closed_status]},
                                "$pnl_value",
                                0.0,
                            ]
                        }
                    },
                }
            },
        ]

        rows = await self.col.aggregate(pipeline).to_list(length=1)
        row = rows[0] if rows else {}
        closed_count = int(row.get("closed_trades", 0) or 0)
        wins = int(row.get("wins", 0) or 0)
        result = {
            "open_trades": int(row.get("open_trades", 0) or 0),
            "closed_trades": closed_count,
            "wins": wins,
            "losses": int(row.get("losses", 0) or 0),
            "realized_pnl_usd": float(row.get("realized_pnl_usd", 0.0) or 0.0),
            "winrate": (wins / closed_count * 100.0) if closed_count else 0.0,
        }
        duration_ms = int((time.perf_counter() - started) * 1000)
        log.debug(
            "[mongo] %s user=%s symbol=%s duration_ms=%s result_count=%s",
            operation,
            user_id,
            symbol,
            duration_ms,
            1 if rows else 0,
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

    async def get_open_trade_entities_by_user(
        self,
        user_id: str,
        *,
        mode: str | None = None,
        limit: int = 200,
    ) -> list[Trade]:
        safe_limit = self._safe_limit(limit, default=200)
        query: dict[str, Any] = {
            "user_id": user_id,
            "status": self._enum_value(TradeStatus.OPEN),
        }
        if mode is not None:
            query["mode"] = mode

        cursor = self.col.find(query).sort("opened_at", 1).limit(safe_limit)
        docs = await cursor.to_list(length=safe_limit)
        return [self._from_doc(doc) for doc in docs]

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
        safe_limit = self._safe_limit(limit, default=MAX_TRADE_ROWS_LIMIT)
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

        cursor = self.col.find(query).sort("opened_at", -1).limit(safe_limit)
        docs = await cursor.to_list(length=safe_limit)
        return [self._from_doc(doc) for doc in docs]

    async def get_closed_trades(
        self,
        user_id: str,
        *,
        mode: str | None = None,
        limit: int = MAX_TRADE_ROWS_LIMIT,
    ) -> list[dict[str, Any]]:
        safe_limit = self._safe_limit(limit, default=MAX_TRADE_ROWS_LIMIT)
        query: dict[str, Any] = {
            "user_id": user_id,
            "status": self._enum_value(TradeStatus.CLOSED),
        }
        if mode is not None:
            query["mode"] = mode
        cursor = self.col.find(query).sort("opened_at", -1).limit(safe_limit)
        return await cursor.to_list(length=safe_limit)

    async def get_all_closed_trades(
        self,
        *,
        mode: str | None = None,
        limit: int = MAX_TRADE_ROWS_LIMIT,
    ) -> list[dict[str, Any]]:
        safe_limit = self._safe_limit(limit, default=MAX_TRADE_ROWS_LIMIT)
        query: dict[str, Any] = {"status": self._enum_value(TradeStatus.CLOSED)}
        if mode is not None:
            query["mode"] = mode
        cursor = self.col.find(query).sort("opened_at", -1).limit(safe_limit)
        return await cursor.to_list(length=safe_limit)

    async def get_closed_by_symbol(
        self,
        symbol: str,
        *,
        mode: str | None = None,
        limit: int = MAX_TRADE_ROWS_LIMIT,
    ) -> list[dict[str, Any]]:
        safe_limit = self._safe_limit(limit, default=MAX_TRADE_ROWS_LIMIT)
        query: dict[str, Any] = {
            "symbol": symbol,
            "status": self._enum_value(TradeStatus.CLOSED),
        }
        if mode is not None:
            query["mode"] = mode
        cursor = self.col.find(query).sort("opened_at", -1).limit(safe_limit)
        return await cursor.to_list(length=safe_limit)

    async def get_recent_closed_trades(
        self,
        user_id: str | None = None,
        limit: int = 10,
        *,
        mode: str | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = self._safe_limit(limit, default=10)
        query: dict[str, Any] = {
            "status": self._enum_value(TradeStatus.CLOSED),
        }
        if user_id is not None:
            query["user_id"] = user_id
        if mode is not None:
            query["mode"] = mode

        cursor = self.col.find(query).sort("opened_at", -1).limit(safe_limit)
        return await cursor.to_list(length=safe_limit)

    async def delete_trades_by_user(self, *, user_id: str, mode: str | None = None) -> int:
        query: dict[str, Any] = {"user_id": user_id}
        if mode is not None:
            query["mode"] = mode
        result = await self.col.delete_many(query)
        return int(result.deleted_count)

    def _to_doc(self, trade: Trade) -> dict[str, Any]:
        payload = asdict(trade)
        payload["side"] = self._enum_value(trade.side)
        payload["status"] = self._enum_value(trade.status)
        payload["is_open"] = trade.is_open
        payload["tp1_hit"] = trade.tp1_hit
        payload["tp2_hit"] = trade.tp2_hit
        payload["tp3_hit"] = trade.tp3_hit
        payload["closed"] = trade.closed
        return self._compact_trade_doc(payload)

    def _to_update_doc(self, trade: Trade) -> dict[str, Any]:
        payload = self._to_doc(trade)
        # Notification delivery flags are written by Telegram workers. Runtime trade
        # upserts may run from another process with stale in-memory values, so false
        # defaults must not erase a successfully delivered notification state.
        if not bool(getattr(trade, "entry_notification_sent", False)):
            payload.pop("entry_notification_sent", None)
            payload.pop("entry_notification_sent_at", None)
        if getattr(trade, "entry_notification_recovery_queued_at", None) is None:
            payload.pop("entry_notification_recovery_queued_at", None)
        if getattr(trade, "entry_notification_last_send_attempt_at", None) is None:
            payload.pop("entry_notification_last_send_attempt_at", None)
        if int(getattr(trade, "entry_notification_send_attempt_count", 0) or 0) <= 0:
            payload.pop("entry_notification_send_attempt_count", None)
        if getattr(trade, "entry_notification_last_send_exception", None) is None:
            payload.pop("entry_notification_last_send_exception", None)
        if not bool(getattr(trade, "risk_warning_sent", False)):
            payload.pop("risk_warning_sent", None)
            payload.pop("risk_warning_sent_at", None)
        return payload

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
        data["side"] = Side(data["side"]) if not isinstance(data["side"], Side) else data["side"]
        data["status"] = (
            TradeStatus(data["status"]) if not isinstance(data["status"], TradeStatus) else data["status"]
        )
        return Trade(**data)

    def _enum_value(self, value: Any) -> Any:
        return value.value if hasattr(value, "value") else value
