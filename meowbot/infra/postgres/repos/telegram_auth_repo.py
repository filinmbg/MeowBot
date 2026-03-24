from __future__ import annotations

from typing import Any

import asyncpg


class TelegramAuthRepo:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def get_user_by_telegram_user_id(self, telegram_user_id: int) -> dict[str, Any] | None:
        query = """
        select
            u.id,
            u.email,
            u.status,
            u.role,
            u.timezone,
            u.language_code,
            u.display_name,
            u.created_at,
            u.updated_at,
            tp.telegram_user_id,
            tp.username,
            tp.first_name,
            tp.last_name,
            tp.last_interaction_at
        from telegram_profiles tp
        join users u on u.id = tp.user_id
        where tp.telegram_user_id = $1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, telegram_user_id)
        return dict(row) if row else None

    async def create_user_with_telegram(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        language_code: str | None,
    ) -> dict[str, Any]:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                user_row = await conn.fetchrow(
                    """
                    insert into users (
                        language_code,
                        display_name
                    )
                    values ($1, $2)
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
                    """,
                    language_code or "uk",
                    first_name or username,
                )
                assert user_row is not None
                user_id = user_row["id"]

                await conn.execute(
                    """
                    insert into auth_identities (
                        user_id,
                        provider,
                        provider_user_id
                    )
                    values ($1, 'telegram', $2)
                    """,
                    user_id,
                    str(telegram_user_id),
                )

                await conn.execute(
                    """
                    insert into telegram_profiles (
                        user_id,
                        telegram_user_id,
                        username,
                        first_name,
                        last_name
                    )
                    values ($1, $2, $3, $4, $5)
                    """,
                    user_id,
                    telegram_user_id,
                    username,
                    first_name,
                    last_name,
                )

                profile_row = await conn.fetchrow(
                    """
                    select
                        u.id,
                        u.email,
                        u.status,
                        u.role,
                        u.timezone,
                        u.language_code,
                        u.display_name,
                        u.created_at,
                        u.updated_at,
                        tp.telegram_user_id,
                        tp.username,
                        tp.first_name,
                        tp.last_name,
                        tp.last_interaction_at
                    from telegram_profiles tp
                    join users u on u.id = tp.user_id
                    where tp.telegram_user_id = $1
                    """,
                    telegram_user_id,
                )
                assert profile_row is not None
                return dict(profile_row)

    async def update_telegram_profile(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> None:
        query = """
        update telegram_profiles
        set
            username = $2,
            first_name = $3,
            last_name = $4,
            last_interaction_at = now(),
            updated_at = now()
        where telegram_user_id = $1
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                telegram_user_id,
                username,
                first_name,
                last_name,
            )