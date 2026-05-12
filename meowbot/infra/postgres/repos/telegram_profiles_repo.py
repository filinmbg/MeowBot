from __future__ import annotations

from typing import Any

import asyncpg


class TelegramProfilesRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def get_by_user_id(self, user_id) -> dict[str, Any] | None:
        query = """
        select
            id,
            user_id,
            telegram_id,
            username,
            first_name,
            last_name,
            chat_id,
            is_onboarded,
            last_seen_at,
            created_at,
            updated_at
        from telegram_profiles
        where user_id = $1
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id)
        return dict(row) if row else None

    async def upsert_profile(
        self,
        *,
        user_id,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        chat_id: int,
        is_onboarded: bool = False,
    ) -> dict[str, Any]:
        query = """
        insert into telegram_profiles (
            user_id,
            telegram_id,
            username,
            first_name,
            last_name,
            chat_id,
            is_onboarded
        )
        values ($1, $2, $3, $4, $5, $6, $7)
        on conflict (telegram_id)
        do update set
            user_id = excluded.user_id,
            username = excluded.username,
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            chat_id = excluded.chat_id,
            updated_at = now()
        returning
            id,
            user_id,
            telegram_id,
            username,
            first_name,
            last_name,
            chat_id,
            is_onboarded,
            last_seen_at,
            created_at,
            updated_at
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                user_id,
                int(telegram_id),
                username,
                first_name,
                last_name,
                int(chat_id),
                bool(is_onboarded),
            )
        return dict(row)

    async def mark_onboarded(self, telegram_id: int) -> dict[str, Any] | None:
        query = """
        update telegram_profiles
        set
            is_onboarded = true,
            last_seen_at = now(),
            updated_at = now()
        where telegram_id = $1
        returning
            id,
            user_id,
            telegram_id,
            username,
            first_name,
            last_name,
            chat_id,
            is_onboarded,
            last_seen_at,
            created_at,
            updated_at
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, int(telegram_id))
        return dict(row) if row else None

    async def touch_last_seen(self, telegram_id: int) -> None:
        query = """
        update telegram_profiles
        set
            last_seen_at = now(),
            updated_at = now()
        where telegram_id = $1
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, int(telegram_id))
