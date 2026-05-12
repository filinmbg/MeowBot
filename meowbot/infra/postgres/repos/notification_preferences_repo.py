from __future__ import annotations

import asyncpg


class NotificationPreferencesRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    @staticmethod
    def _returning_columns() -> str:
        return """
            id,
            user_id,
            notifications_enabled,
            notify_trade_opened,
            notify_tp_hit,
            notify_trade_closed,
            notify_stop_loss,
            notify_system,
            quiet_hours_from,
            quiet_hours_to,
            created_at,
            updated_at
        """

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

    async def get_by_user_id(self, user_id) -> dict | None:
        query = """
        select
            id,
            user_id,
            notifications_enabled,
            notify_trade_opened,
            notify_tp_hit,
            notify_trade_closed,
            notify_stop_loss,
            notify_system,
            quiet_hours_from,
            quiet_hours_to,
            created_at,
            updated_at
        from notification_preferences
        where user_id = $1
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id)
        return dict(row) if row else None

    async def update_flags_by_user_id(self, user_id, **flags) -> dict | None:
        allowed = {
            "notifications_enabled",
            "notify_trade_opened",
            "notify_tp_hit",
            "notify_trade_closed",
            "notify_stop_loss",
            "notify_system",
        }
        updates = {key: value for key, value in flags.items() if key in allowed}
        if not updates:
            return await self.get_by_user_id(user_id)

        columns = list(updates.keys())
        assignments = ",\n            ".join(
            f"{column} = ${index}"
            for index, column in enumerate(columns, start=2)
        )
        query = f"""
        update notification_preferences
        set
            {assignments},
            updated_at = now()
        where user_id = $1
        returning {self._returning_columns()}
        """
        args = [user_id, *[updates[column] for column in columns]]
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
        if row:
            return dict(row)

        await self.create_default(user_id)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
        return dict(row) if row else None
