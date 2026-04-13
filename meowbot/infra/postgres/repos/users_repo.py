from __future__ import annotations

from typing import Any

import asyncpg


class UsersRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def create(
        self,
        *,
        display_name: str | None = None,
        preferred_language: str = "uk",
        role: str = "user",
        status: str = "active",
        email: str | None = None,
    ) -> dict[str, Any]:
        query = """
        insert into users (
            email,
            display_name,
            role,
            status,
            preferred_language
        )
        values ($1, $2, $3, $4, $5)
        returning
            id,
            email,
            display_name,
            role,
            status,
            preferred_language,
            created_at,
            updated_at
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                email,
                display_name,
                role,
                status,
                preferred_language,
            )
        return dict(row)

    async def get_by_telegram_id(self, telegram_id: int) -> dict[str, Any] | None:
        query = """
        select
            u.id,
            u.email,
            u.display_name,
            u.role,
            u.status,
            u.preferred_language,
            u.created_at,
            u.updated_at,

            tp.telegram_id,
            tp.username,
            tp.first_name,
            tp.last_name,
            tp.chat_id,
            tp.is_onboarded,
            tp.last_seen_at
        from users u
        join telegram_profiles tp
            on tp.user_id = u.id
        where tp.telegram_id = $1
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, int(telegram_id))
        return dict(row) if row else None