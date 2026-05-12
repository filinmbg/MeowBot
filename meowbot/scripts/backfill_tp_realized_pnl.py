from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from pprint import pprint
from typing import Any

from dotenv import load_dotenv

from meowbot.infra.mongo.async_client import AsyncMongoConfig, AsyncMongoConn


ROOT = Path(__file__).resolve().parents[2]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return int(default)


def _status_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value.value if hasattr(value, "value") else value)


async def _load_latest_tp_events(db, *, user_id: str | None = None) -> dict[str, dict[str, Any]]:
    query: dict[str, Any] = {
        "event_type": "TP_HIT",
        "trade_id": {"$exists": True, "$ne": None},
        "payload.realized_pnl_usd": {"$exists": True},
    }
    if user_id:
        query["user_id"] = user_id

    latest_by_trade: dict[str, dict[str, Any]] = {}
    cursor = db["trade_events"].find(
        query,
        {
            "trade_id": 1,
            "user_id": 1,
            "symbol": 1,
            "ts": 1,
            "payload.realized_pnl_usd": 1,
            "payload.tp_index": 1,
        },
    ).sort("ts", 1)

    async for row in cursor:
        payload = row.get("payload") or {}
        trade_id = str(row.get("trade_id") or "")
        if not trade_id:
            continue

        event_realized = _safe_float(payload.get("realized_pnl_usd"), 0.0)
        tp_index = _safe_int(payload.get("tp_index"), 0)
        ts = _safe_int(row.get("ts"), 0)

        previous = latest_by_trade.get(trade_id)
        previous_ts = _safe_int(previous.get("ts"), 0) if previous else -1
        previous_realized = _safe_float(previous.get("realized_pnl_usd"), 0.0) if previous else 0.0

        # TP events contain cumulative realized PnL at that stage. Keep the
        # latest event by time, but never lose a larger cumulative value.
        if previous is None or ts >= previous_ts or event_realized > previous_realized:
            latest_by_trade[trade_id] = {
                "trade_id": trade_id,
                "user_id": row.get("user_id"),
                "symbol": row.get("symbol"),
                "ts": ts,
                "realized_pnl_usd": max(event_realized, previous_realized),
                "tp_index": max(tp_index, _safe_int(previous.get("tp_index"), 0) if previous else 0),
            }

    return latest_by_trade


async def _backfill(*, execute: bool, user_id: str | None = None) -> dict[str, Any]:
    mongo = AsyncMongoConn(AsyncMongoConfig())
    await mongo.connect()
    try:
        db = mongo.db
        if db is None:
            raise RuntimeError("Mongo database is not initialized")

        latest_by_trade = await _load_latest_tp_events(db, user_id=user_id)
        now = datetime.now(timezone.utc)

        scanned = 0
        matched_trades = 0
        would_update = 0
        updated = 0
        missing_trades = 0
        skipped_not_increasing = 0
        samples: list[dict[str, Any]] = []

        for trade_id, event in latest_by_trade.items():
            scanned += 1
            trade = await db["trades"].find_one({"trade_id": trade_id})
            if not trade:
                missing_trades += 1
                continue

            matched_trades += 1
            current_realized = _safe_float(trade.get("realized_pnl_usd"), 0.0)
            target_realized = _safe_float(event.get("realized_pnl_usd"), 0.0)
            if target_realized <= current_realized:
                skipped_not_increasing += 1
                continue

            tp_hit_count = max(_safe_int(trade.get("tp_hit_count"), 0), _safe_int(event.get("tp_index"), 0))
            status = _status_value(trade.get("status"))
            is_open = status == "OPEN"

            update_doc = {
                "realized_pnl_usd": target_realized,
                "tp_hit_count": tp_hit_count,
                "tp1_hit": tp_hit_count >= 1,
                "tp2_hit": tp_hit_count >= 2,
                "tp3_hit": tp_hit_count >= 3,
                "is_open": is_open,
                "closed": status == "CLOSED",
                "updated_at": now,
            }

            would_update += 1
            if len(samples) < 10:
                samples.append(
                    {
                        "trade_id": trade_id,
                        "user_id": trade.get("user_id"),
                        "symbol": trade.get("symbol"),
                        "current_realized_pnl_usd": current_realized,
                        "target_realized_pnl_usd": target_realized,
                        "tp_hit_count": tp_hit_count,
                    }
                )

            if execute:
                result = await db["trades"].update_one(
                    {
                        "trade_id": trade_id,
                        "$or": [
                            {"realized_pnl_usd": {"$exists": False}},
                            {"realized_pnl_usd": {"$lt": target_realized}},
                        ],
                    },
                    {"$set": update_doc},
                )
                updated += int(result.modified_count)

        return {
            "execute": execute,
            "user_id": user_id,
            "tp_event_trades_scanned": scanned,
            "matched_trades": matched_trades,
            "missing_trades": missing_trades,
            "would_update": would_update,
            "updated": updated,
            "skipped_not_increasing": skipped_not_increasing,
            "samples": samples,
        }
    finally:
        await mongo.close()


async def main_async() -> None:
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(
        description="Backfill trade.realized_pnl_usd from cumulative TP_HIT events."
    )
    parser.add_argument("--execute", action="store_true", help="Apply updates. Without this flag the script is dry-run.")
    parser.add_argument("--user-id", default=None, help="Optional runtime user id, for example tg:853048829.")
    args = parser.parse_args()

    result = await _backfill(execute=bool(args.execute), user_id=args.user_id)
    print("\n=== TP REALIZED PNL BACKFILL ===")
    pprint(result)
    if not args.execute:
        print("\nDry run only. Re-run with --execute to apply updates.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
