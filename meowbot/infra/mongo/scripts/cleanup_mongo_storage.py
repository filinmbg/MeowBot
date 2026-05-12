from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from meowbot.infra.mongo.client import MongoConfig, MongoConn


logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("meowbot")

DEFAULT_BARS_RETENTION_DAYS = int(os.getenv("MONGO_CLEANUP_BARS_DAYS", "3"))
DEFAULT_DEBUG_RETENTION_DAYS = int(os.getenv("MONGO_CLEANUP_DEBUG_DAYS", "7"))
DEFAULT_TRADE_EVENTS_RETENTION_DAYS = int(os.getenv("MONGO_CLEANUP_TRADE_EVENTS_DAYS", "30"))
DEFAULT_LOGS_RETENTION_DAYS = int(os.getenv("MONGO_CLEANUP_LOGS_DAYS", "7"))

NON_CRITICAL_LOG_COLLECTIONS = {
    "runtime_logs",
    "logs",
    "debug_logs",
    "system_logs",
}


def _cutoffs(days: int) -> tuple[datetime, int]:
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=int(days))
    return cutoff_dt, int(cutoff_dt.timestamp() * 1000)


def _age_query(*, days: int, date_fields: list[str], ms_fields: list[str]) -> dict[str, Any]:
    cutoff_dt, cutoff_ms = _cutoffs(days)
    clauses: list[dict[str, Any]] = []
    clauses.extend({field: {"$lt": cutoff_dt}} for field in date_fields)
    clauses.extend({field: {"$lt": cutoff_ms}} for field in ms_fields)
    return {"$or": clauses} if len(clauses) > 1 else clauses[0]


def _delete_many(col, query: dict[str, Any], *, dry_run: bool) -> int:
    count = int(col.count_documents(query))
    if dry_run or count == 0:
        return count
    result = col.delete_many(query)
    return int(result.deleted_count)


def _cleanup_collection(col, query: dict[str, Any], *, label: str, dry_run: bool) -> int:
    deleted = _delete_many(col, query, dry_run=dry_run)
    action = "would_delete" if dry_run else "deleted"
    print(f"{label}: {action}={deleted}")
    return deleted


def main() -> None:
    parser = argparse.ArgumentParser(description="Trim old non-critical Mongo data for MeowBot.")
    parser.add_argument("--bars-days", type=int, default=DEFAULT_BARS_RETENTION_DAYS)
    parser.add_argument("--debug-days", type=int, default=DEFAULT_DEBUG_RETENTION_DAYS)
    parser.add_argument("--trade-events-days", type=int, default=DEFAULT_TRADE_EVENTS_RETENTION_DAYS)
    parser.add_argument("--logs-days", type=int, default=DEFAULT_LOGS_RETENTION_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    started = time.time()
    conn = MongoConn(MongoConfig(app_name="MeowBot-mongo-cleanup"))
    conn.connect()
    assert conn.db is not None

    names = set(conn.db.list_collection_names())
    total_deleted = 0

    for name in sorted(names):
        if not (name.startswith("bars_") or name.startswith("candles")):
            continue
        query = _age_query(
            days=args.bars_days,
            date_fields=["created_at_dt", "created_at"],
            ms_fields=["close_time", "ts"],
        )
        total_deleted += _cleanup_collection(
            conn.db[name],
            query,
            label=f"{name} older_than={args.bars_days}d",
            dry_run=args.dry_run,
        )

    if "debug_events" in names:
        query = _age_query(
            days=args.debug_days,
            date_fields=["created_at", "created_at_dt"],
            ms_fields=["ts", "created_at_ms"],
        )
        total_deleted += _cleanup_collection(
            conn.db["debug_events"],
            query,
            label=f"debug_events older_than={args.debug_days}d",
            dry_run=args.dry_run,
        )

    if "trade_events" in names:
        query = {
            "$and": [
                _age_query(
                    days=args.trade_events_days,
                    date_fields=["created_at", "created_at_dt"],
                    ms_fields=["ts", "created_at_ms"],
                ),
                {"sent": {"$ne": False}},
            ]
        }
        total_deleted += _cleanup_collection(
            conn.db["trade_events"],
            query,
            label=f"trade_events sent older_than={args.trade_events_days}d",
            dry_run=args.dry_run,
        )

    for name in sorted(names & NON_CRITICAL_LOG_COLLECTIONS):
        query = _age_query(
            days=args.logs_days,
            date_fields=["created_at", "created_at_dt", "updated_at"],
            ms_fields=["ts", "created_at_ms"],
        )
        total_deleted += _cleanup_collection(
            conn.db[name],
            query,
            label=f"{name} older_than={args.logs_days}d",
            dry_run=args.dry_run,
        )

    elapsed_ms = int((time.time() - started) * 1000)
    action = "would_delete_total" if args.dry_run else "deleted_total"
    print(f"{action}={total_deleted} elapsed_ms={elapsed_ms}")
    print("Protected collections were not touched: users, trades, bot_state, subscriptions, active/open trade state.")
    conn.close()


if __name__ == "__main__":
    main()

