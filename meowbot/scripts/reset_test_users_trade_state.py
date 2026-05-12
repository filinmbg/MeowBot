from __future__ import annotations

import argparse
import asyncio
import os
import re
from pathlib import Path
from pprint import pprint
from typing import Any

from dotenv import load_dotenv

from meowbot.core.configs.strategy_version_test_users import (
    DEFAULT_V1_TEST_USER_EMAILS,
    DEFAULT_V2_TEST_USER_EMAILS,
    expand_strategy_version_test_user_emails,
)
from meowbot.infra.mongo.async_client import AsyncMongoConfig, AsyncMongoConn
from meowbot.infra.postgres.client import get_pg_pool


ROOT = Path(__file__).resolve().parents[2]


def _parse_emails(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _default_test_user_emails(raw: str) -> list[str]:
    parsed = _parse_emails(raw)
    if parsed:
        return expand_strategy_version_test_user_emails(parsed)
    return [*DEFAULT_V1_TEST_USER_EMAILS, *DEFAULT_V2_TEST_USER_EMAILS]


async def _load_test_users(emails: list[str]) -> list[dict[str, Any]]:
    if not emails:
        return []

    pool = await get_pg_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                select
                    u.id as user_uuid,
                    u.email,
                    tp.telegram_id,
                    tp.chat_id,
                    ('tg:' || tp.telegram_id::text) as runtime_user_id
                from users u
                join telegram_profiles tp on tp.user_id = u.id
                where lower(u.email) = any($1::text[])
                order by u.email
                """,
                [email.lower() for email in emails],
            )
        return [dict(row) for row in rows]
    finally:
        await pool.close()


async def _delete_cooldowns(runtime_user_ids: list[str], *, execute: bool) -> int:
    if not runtime_user_ids or not execute:
        return 0

    pool = await get_pg_pool()
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                delete from trade_entry_cooldowns
                where runtime_user_id = any($1::text[])
                """,
                runtime_user_ids,
            )
        return int(result.split()[-1])
    finally:
        await pool.close()


def _trade_id_regex(runtime_user_ids: list[str]) -> str:
    escaped = [re.escape(user_id) for user_id in runtime_user_ids]
    return f"^({'|'.join(escaped)}):"


async def _mongo_counts_and_delete(
    *,
    runtime_user_ids: list[str],
    chat_ids: list[int],
    execute: bool,
) -> dict[str, int]:
    mongo = AsyncMongoConn(AsyncMongoConfig())
    await mongo.connect()
    try:
        db = mongo.db
        trade_id_pattern = _trade_id_regex(runtime_user_ids)

        queries: dict[str, dict[str, Any]] = {
            "trades": {"user_id": {"$in": runtime_user_ids}},
            "trade_events": {
                "$or": [
                    {"user_id": {"$in": runtime_user_ids}},
                    {"trade_id": {"$regex": trade_id_pattern}},
                ]
            },
            "runtime_logs": {
                "$or": [
                    {"user_id": {"$in": runtime_user_ids}},
                    {"runtime_user_id": {"$in": runtime_user_ids}},
                ]
            },
            "telegram_delivery_logs": {
                "$or": [
                    {"user_id": {"$in": runtime_user_ids}},
                    {"runtime_user_id": {"$in": runtime_user_ids}},
                    {"chat_id": {"$in": chat_ids}},
                ]
            },
            "bot_state": {
                "key": {
                    "$regex": "(" + "|".join(re.escape(user_id) for user_id in runtime_user_ids) + ")"
                }
            },
        }

        result: dict[str, int] = {}
        for collection_name, query in queries.items():
            collection = db[collection_name]
            count = await collection.count_documents(query)
            result[f"{collection_name}_matched"] = int(count)
            if execute:
                delete_result = await collection.delete_many(query)
                result[f"{collection_name}_deleted"] = int(delete_result.deleted_count)
        return result
    finally:
        await mongo.close()


async def main_async() -> None:
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(description="Reset trade state for configured test users only.")
    parser.add_argument("--execute", action="store_true", help="Actually delete matching rows/documents.")
    parser.add_argument(
        "--emails",
        default=os.getenv("TEST_USER_EMAILS", ""),
        help="Comma-separated test user emails. Defaults to V1+V2 test users, expanding TEST_USER_EMAILS with V2 matches.",
    )
    args = parser.parse_args()

    emails = _default_test_user_emails(args.emails)
    users = await _load_test_users(emails)
    runtime_user_ids = [str(row["runtime_user_id"]) for row in users if row.get("runtime_user_id")]
    chat_ids = [int(row["chat_id"]) for row in users if row.get("chat_id") is not None]

    print("\n=== TEST USERS ===")
    pprint(
        [
            {
                "email": row.get("email"),
                "telegram_id": row.get("telegram_id"),
                "runtime_user_id": row.get("runtime_user_id"),
            }
            for row in users
        ]
    )

    if not runtime_user_ids:
        print("\nNo runtime users found. Nothing to reset.")
        return

    mongo_result = await _mongo_counts_and_delete(
        runtime_user_ids=runtime_user_ids,
        chat_ids=chat_ids,
        execute=bool(args.execute),
    )
    cooldowns_deleted = await _delete_cooldowns(runtime_user_ids, execute=bool(args.execute))

    print("\n=== RESET SNAPSHOT ===")
    pprint(
        {
            **mongo_result,
            "trade_entry_cooldowns_deleted": cooldowns_deleted,
            "execute": bool(args.execute),
        }
    )

    if not args.execute:
        print("\nDry run only. Re-run with --execute to delete matched data.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
