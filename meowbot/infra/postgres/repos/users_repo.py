from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg


class UsersRepo:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def create_user(
        self,
        *,
        email: str | None,
        display_name: str | None,
        timezone: str,
        language_code: str,
    ) -> dict[str, Any]:
        query = """
        insert into users (
            email,
            display_name,
            timezone,
            language_code
        )
        values ($1, $2, $3, $4)
        returning
            id,
            email,
            status,
            role,
            timezone,
            language_code,
            display_name,
            created_at,
            updated_at
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                email,
                display_name,
                timezone,
                language_code,
            )
        assert row is not None
        return dict(row)

    async def get_user_by_id(self, user_id: UUID) -> dict[str, Any] | None:
        query = """
        select
            id,
            email,
            status,
            role,
            timezone,
            language_code,
            display_name,
            created_at,
            updated_at
        from users
        where id = $1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id)
        return dict(row) if row else None