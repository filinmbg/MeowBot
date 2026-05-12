from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pymongo import UpdateOne

from meowbot.infra.mongo.client import MongoConfig, MongoConn


DEFAULT_FAILED_WRITES_PATH = Path(os.getenv("MONGO_FAILED_WRITES_PATH", "data/emergency_mongo_failed_writes.jsonl"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _strip_mongo_id(doc: dict[str, Any]) -> dict[str, Any]:
    result = dict(doc)
    result.pop("_id", None)
    return result


def _apply_trade_upsert(db, payload: dict[str, Any]) -> None:
    trade_id = str(payload.get("trade_id") or "")
    if not trade_id:
        raise ValueError("trade_upsert payload has no trade_id")
    doc = _strip_mongo_id(payload)
    doc["emergency_replayed_at"] = _now()
    db["trades"].replace_one({"trade_id": trade_id}, doc, upsert=True)


def _apply_trade_event_insert(db, payload: dict[str, Any]) -> None:
    doc = _strip_mongo_id(payload)
    now = _now()
    doc.setdefault("created_at", now)
    doc.setdefault("updated_at", now)
    doc.setdefault("sent", False)
    doc.setdefault("delivered_channels", [])
    doc["emergency_replayed_at"] = now
    idempotency_key = doc.get("idempotency_key")
    if idempotency_key:
        db["trade_events"].update_one(
            {"idempotency_key": idempotency_key},
            {"$setOnInsert": doc},
            upsert=True,
        )
        return
    db["trade_events"].insert_one(doc)


def _apply_bot_state_set(db, payload: dict[str, Any]) -> None:
    key = str(payload.get("key") or "")
    if not key:
        raise ValueError("bot_state_set payload has no key")
    db["bot_state"].update_one(
        {"key": key},
        {
            "$set": {
                "key": key,
                "value": int(payload.get("value", 0) or 0),
                "updated_at": _now(),
                "emergency_replayed_at": _now(),
            }
        },
        upsert=True,
    )


def _apply_bars_upsert_many(db, payload: list[dict[str, Any]]) -> None:
    by_tf: dict[str, list[dict[str, Any]]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        tf = str(item.get("tf") or "")
        if not tf:
            continue
        by_tf.setdefault(tf, []).append(_strip_mongo_id(item))

    for tf, bars in by_tf.items():
        ops: list[UpdateOne] = []
        for bar in bars:
            symbol = str(bar.get("symbol") or "").upper()
            close_time = bar.get("close_time")
            features_ver = bar.get("features_ver")
            if not symbol or close_time is None:
                continue
            bar.setdefault("created_at_dt", _now())
            filt = {
                "symbol": symbol,
                "close_time": int(close_time),
                "features_ver": str(features_ver or ""),
            }
            ops.append(UpdateOne(filt, {"$set": bar}, upsert=True))
        if ops:
            db[f"bars_{tf}"].bulk_write(ops, ordered=False)


def _apply_job(db, job: dict[str, Any], *, include_low_priority: bool) -> None:
    operation = str(job.get("operation_type") or "")
    payload = job.get("payload")
    if operation == "trade_upsert" and isinstance(payload, dict):
        _apply_trade_upsert(db, payload)
    elif operation == "trade_event_insert" and isinstance(payload, dict):
        _apply_trade_event_insert(db, payload)
    elif operation == "bot_state_set" and isinstance(payload, dict):
        _apply_bot_state_set(db, payload)
    elif operation == "bars_upsert_many" and include_low_priority and isinstance(payload, list):
        _apply_bars_upsert_many(db, payload)
    elif operation == "bars_upsert_many":
        return
    else:
        raise ValueError(f"unsupported emergency write operation: {operation}")


def _rewrite_remaining(path: Path, remaining_lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for line in remaining_lines:
            fh.write(line.rstrip("\n"))
            fh.write("\n")
    os.replace(temp_name, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay MeowBot emergency Mongo failed writes JSONL.")
    parser.add_argument("--path", default=str(DEFAULT_FAILED_WRITES_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep", action="store_true", help="Do not remove successfully replayed lines from the JSONL file.")
    parser.add_argument("--include-low-priority", action="store_true", help="Also replay bars_upsert_many jobs.")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"no emergency failed writes file found: {path}")
        return

    conn = MongoConn(MongoConfig(app_name="MeowBot-replay-failed-writes"))
    conn.connect()
    assert conn.db is not None

    total = 0
    replayed = 0
    failed = 0
    remaining: list[str] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        total += 1
        try:
            job = json.loads(line)
            if not args.dry_run:
                _apply_job(conn.db, job, include_low_priority=args.include_low_priority)
            replayed += 1
            if args.keep:
                remaining.append(line)
        except Exception as exc:
            failed += 1
            remaining.append(line)
            print(f"failed line={total} error={type(exc).__name__}:{exc}")

    if not args.dry_run and not args.keep:
        _rewrite_remaining(path, remaining)

    print(
        f"total={total} replayed={replayed} failed={failed} "
        f"remaining={len(remaining)} dry_run={args.dry_run} path={path}"
    )
    conn.close()


if __name__ == "__main__":
    main()

