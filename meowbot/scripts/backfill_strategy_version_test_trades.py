from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path
from pprint import pprint
from typing import Any

from dotenv import load_dotenv

from meowbot.core.configs.strategy_version_test_users import (
    DEFAULT_V1_TEST_USER_EMAILS,
    DEFAULT_V2_TEST_USER_EMAILS,
    detect_strategy_version,
    expand_strategy_version_test_user_emails,
    normalize_strategy_plan_code,
)
from meowbot.infra.mongo.async_client import AsyncMongoConfig, AsyncMongoConn
from meowbot.infra.postgres.client import get_pg_pool


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_NAME = "backfill_strategy_version_test_trades"


def _parse_csv(raw: str | None) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _parse_telegram_ids(raw: str | None) -> list[int]:
    values: list[int] = []
    for item in _parse_csv(raw):
        try:
            values.append(int(item))
        except ValueError:
            raise SystemExit(f"Invalid telegram id: {item}") from None
    return values


def _default_emails() -> list[str]:
    env_value = os.getenv("TEST_USER_EMAILS", "")
    if env_value.strip():
        return expand_strategy_version_test_user_emails(_parse_csv(env_value))
    return [*DEFAULT_V1_TEST_USER_EMAILS, *DEFAULT_V2_TEST_USER_EMAILS]


async def _load_targets(*, emails: list[str], telegram_ids: list[int]) -> list[dict[str, Any]]:
    pool = await get_pg_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH active_sub AS (
                    SELECT DISTINCT ON (us.user_id)
                        us.user_id,
                        sp.code AS plan_code,
                        COALESCE(
                            us.strategy_version,
                            sp.features_json ->> 'strategy_version',
                            'v1'
                        ) AS strategy_version
                    FROM user_subscriptions us
                    JOIN subscription_plans sp
                        ON sp.id = us.plan_id
                    WHERE us.status = 'active'
                    ORDER BY us.user_id, us.created_at DESC
                )
                SELECT
                    u.id AS user_uuid,
                    u.email,
                    tp.telegram_id,
                    tp.username,
                    ('tg:' || tp.telegram_id::text) AS runtime_user_id,
                    COALESCE(active_sub.plan_code, 'unknown') AS plan_code,
                    COALESCE(active_sub.strategy_version, 'v1') AS strategy_version
                FROM users u
                JOIN telegram_profiles tp
                    ON tp.user_id = u.id
                LEFT JOIN active_sub
                    ON active_sub.user_id = u.id
                WHERE LOWER(u.email) = ANY($1::text[])
                   OR tp.telegram_id = ANY($2::bigint[])
                ORDER BY u.email
                """,
                [email.lower() for email in emails],
                telegram_ids,
            )
    finally:
        await pool.close()

    targets: list[dict[str, Any]] = []
    seen_runtime_ids: set[str] = set()
    for row in rows:
        item = dict(row)
        runtime_user_id = str(item.get("runtime_user_id") or "")
        if not runtime_user_id or runtime_user_id in seen_runtime_ids:
            continue
        plan_code = str(item.get("plan_code") or "unknown").strip().lower()
        strategy_version = detect_strategy_version(
            strategy_version=item.get("strategy_version"),
            plan_code=plan_code,
            email=item.get("email"),
            username=item.get("username"),
        )
        targets.append(
            {
                **item,
                "runtime_user_id": runtime_user_id,
                "plan_code": plan_code,
                "strategy_version": strategy_version,
                "subscription_type": normalize_strategy_plan_code(plan_code),
            }
        )
        seen_runtime_ids.add(runtime_user_id)
    return targets


async def _backfill_mongo(*, targets: list[dict[str, Any]], execute: bool) -> dict[str, Any]:
    mongo = AsyncMongoConn(AsyncMongoConfig(app_name="MeowBot-backfill"))
    await mongo.connect()
    try:
        if mongo.db is None:
            raise RuntimeError("Async Mongo database is not initialized")

        trades_col = mongo.db["trades"]
        events_col = mongo.db["trade_events"]
        now_ms = int(time.time() * 1000)
        result: dict[str, Any] = {
            "execute": execute,
            "users": len(targets),
            "trades_matched": 0,
            "trades_modified": 0,
            "trade_events_matched": 0,
            "trade_events_modified": 0,
            "per_user": [],
        }

        for target in targets:
            runtime_user_id = str(target["runtime_user_id"])
            payload = {
                "strategy_version": target["strategy_version"],
                "subscription_type": target["subscription_type"],
                "plan_code": target["plan_code"],
                "strategy_backfill_source": SCRIPT_NAME,
                "strategy_backfilled_at": now_ms,
            }
            trades_query = {"user_id": runtime_user_id}
            events_query = {"user_id": runtime_user_id}

            trades_matched = await trades_col.count_documents(trades_query)
            events_matched = await events_col.count_documents(events_query)
            trades_modified = 0
            events_modified = 0
            if execute:
                trades_update = await trades_col.update_many(trades_query, {"$set": payload})
                events_update = await events_col.update_many(events_query, {"$set": payload})
                trades_modified = int(trades_update.modified_count)
                events_modified = int(events_update.modified_count)

            result["trades_matched"] += int(trades_matched)
            result["trade_events_matched"] += int(events_matched)
            result["trades_modified"] += int(trades_modified)
            result["trade_events_modified"] += int(events_modified)
            result["per_user"].append(
                {
                    "email": target.get("email"),
                    "telegram_id": target.get("telegram_id"),
                    "runtime_user_id": runtime_user_id,
                    "plan_code": target["plan_code"],
                    "strategy_version": target["strategy_version"],
                    "subscription_type": target["subscription_type"],
                    "trades_matched": int(trades_matched),
                    "trades_modified": int(trades_modified),
                    "trade_events_matched": int(events_matched),
                    "trade_events_modified": int(events_modified),
                }
            )
        return result
    finally:
        await mongo.close()


async def main_async() -> None:
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(
        description="Backfill strategy_version/subscription_type/plan_code for test-user Mongo trades."
    )
    parser.add_argument("--execute", action="store_true", help="Apply updates. Without this flag it is a dry run.")
    parser.add_argument(
        "--emails",
        default="",
        help="Comma-separated user emails. Defaults to V1+V2 test users, or TEST_USER_EMAILS expanded with V2.",
    )
    parser.add_argument(
        "--telegram-ids",
        default="",
        help="Optional comma-separated Telegram IDs to include, for example 853048829,158556807.",
    )
    args = parser.parse_args()

    emails = expand_strategy_version_test_user_emails(_parse_csv(args.emails)) if args.emails else _default_emails()
    telegram_ids = _parse_telegram_ids(args.telegram_ids)
    targets = await _load_targets(emails=emails, telegram_ids=telegram_ids)

    print("\n=== STRATEGY VERSION BACKFILL TARGETS ===")
    pprint(targets)
    if not targets:
        print("\nNo users found. Nothing to backfill.")
        return

    result = await _backfill_mongo(targets=targets, execute=bool(args.execute))
    print("\n=== BACKFILL RESULT ===")
    pprint(result)
    if not args.execute:
        print("\nDry run only. Re-run with --execute to update Mongo trades and trade_events.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
