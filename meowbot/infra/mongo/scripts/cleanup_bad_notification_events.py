from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

from meowbot.infra.mongo.client import MongoConfig, MongoConn


logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("meowbot")


def _update_many(col, query: dict, update: dict, *, dry_run: bool) -> int:
    if dry_run:
        return int(col.count_documents(query))
    result = col.update_many(query, update)
    return int(result.modified_count)


def main() -> None:
    parser = argparse.ArgumentParser(description="Mark bad/stale Telegram notification events as skipped.")
    parser.add_argument("--dry-run", action="store_true", help="Only print matching counts.")
    args = parser.parse_args()

    conn = MongoConn(MongoConfig(app_name="MeowBot-cleanup-bad-notification-events"))
    conn.connect()
    assert conn.db is not None
    col = conn.db["trade_events"]
    now = datetime.now(timezone.utc)

    debug_query = {
        "trade_id": {"$regex": r"^debug:"},
        "notification_status": {"$nin": ["SENT", "FAILED_PERMANENT", "SKIPPED_DEBUG"]},
    }
    debug_update = {
        "$set": {
            "notification_status": "SKIPPED_DEBUG",
            "skip_reason": "debug_event",
            "retryable": False,
            "locked_at": None,
            "locked_by": None,
            "updated_at": now,
        },
        "$addToSet": {"delivered_channels": "telegram_risk"},
    }

    fake_user_query = {
        "$or": [
            {"user_id": "tg:123456789"},
            {"payload.telegram_id": 123456789},
            {"payload.telegram_id": "123456789"},
        ],
        "notification_status": {"$nin": ["SENT", "FAILED_PERMANENT", "SKIPPED_INVALID_CHAT"]},
    }
    fake_user_update = {
        "$set": {
            "notification_status": "SKIPPED_INVALID_CHAT",
            "skip_reason": "invalid_chat",
            "retryable": False,
            "locked_at": None,
            "locked_by": None,
            "updated_at": now,
        },
        "$addToSet": {"delivered_channels": "telegram_risk"},
    }

    debug_count = _update_many(col, debug_query, debug_update, dry_run=args.dry_run)
    fake_count = _update_many(col, fake_user_query, fake_user_update, dry_run=args.dry_run)

    action = "would mark" if args.dry_run else "marked"
    log.info("[cleanup-bad-notifications] %s debug_events=%s invalid_chat_events=%s", action, debug_count, fake_count)
    conn.close()


if __name__ == "__main__":
    main()
