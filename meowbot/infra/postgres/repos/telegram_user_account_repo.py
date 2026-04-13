from __future__ import annotations

from typing import Any

import asyncpg


class TelegramUserAccountRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def get_user_by_telegram_id(self, telegram_id: int) -> dict[str, Any] | None:
        query = """
        select
            u.id as user_id,
            u.email,
            u.display_name,
            u.status,
            u.role,
            u.preferred_language,
            tp.telegram_id,
            tp.username,
            tp.first_name,
            tp.last_name,
            tp.chat_id,
            tp.is_onboarded
        from telegram_profiles tp
        join users u
            on u.id = tp.user_id
        where tp.telegram_id = $1
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, int(telegram_id))
        return dict(row) if row else None