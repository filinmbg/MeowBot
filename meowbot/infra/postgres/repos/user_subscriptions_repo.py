from __future__ import annotations

from typing import Any

import asyncpg


class UserSubscriptionsRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def get_active_by_user_id(self, user_id) -> dict[str, Any] | None:
        query = """
        select
            id,
            user_id,
            plan_id,
            status,
            starts_at,
            ends_at,
            auto_renew,
            is_trial,
            trial_code,
            ended_reason,
            trial_expiry_warning_sent_at,
            created_at,
            updated_at
        from user_subscriptions
        where user_id = $1
          and status = 'active'
        order by created_at desc
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id)
        return dict(row) if row else None

    async def create_free_subscription(self, *, user_id, plan_id) -> dict[str, Any]:
        query = """
        insert into user_subscriptions (
            user_id,
            plan_id,
            status,
            starts_at,
            ends_at,
            auto_renew,
            is_trial
        )
        values (
            $1,
            $2,
            'active',
            now(),
            null,
            false,
            false
        )
        returning
            id,
            user_id,
            plan_id,
            status,
            starts_at,
            ends_at,
            auto_renew,
            is_trial,
            trial_code,
            ended_reason,
            trial_expiry_warning_sent_at,
            created_at,
            updated_at
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id, plan_id)
        return dict(row)