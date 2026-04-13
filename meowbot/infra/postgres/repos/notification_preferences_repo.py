from __future__ import annotations

import asyncpg


class NotificationPreferencesRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def create_default(self, user_id) -> None:
        query = """
        insert into notification_preferences (
            user_id,
            notifications_enabled,
            notify_trade_opened,
            notify_tp_hit,
            notify_trade_closed,
            notify_stop_loss,
            notify_system
        )
        values (
            $1,
            true,
            true,
            true,
            true,
            true,
            true
        )
        on conflict (user_id) do nothing
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, user_id)