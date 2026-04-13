from __future__ import annotations

from typing import Any

import asyncpg


class TrialNotificationsRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def get_trials_needing_expiry_warning(
        self,
        *,
        hours_before_end: int = 6,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = """
        select
            us.id as subscription_id,
            us.user_id,
            us.plan_id,
            us.status,
            us.starts_at,
            us.ends_at,
            us.is_trial,
            us.trial_code,
            us.trial_expiry_warning_sent_at,

            sp.code as plan_code,
            sp.name as plan_name,

            u.email,
            u.display_name,
            u.preferred_language,

            tp.telegram_id,
            tp.chat_id,
            tp.username,
            tp.first_name,
            tp.last_name,
            tp.is_onboarded

        from user_subscriptions us
        join subscription_plans sp
            on sp.id = us.plan_id
        join users u
            on u.id = us.user_id
        join telegram_profiles tp
            on tp.user_id = u.id
        where
            us.status = 'active'
            and us.is_trial = true
            and us.ends_at is not null
            and us.ends_at > now()
            and us.ends_at <= (now() + make_interval(hours => $1))
            and us.trial_expiry_warning_sent_at is null
            and tp.chat_id is not null
        order by us.ends_at asc
        limit $2
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, int(hours_before_end), int(limit))
        return [dict(row) for row in rows]

    async def mark_trial_expiry_warning_sent(
        self,
        *,
        subscription_id,
    ) -> None:
        query = """
        update user_subscriptions
        set
            trial_expiry_warning_sent_at = now(),
            updated_at = now()
        where id = $1
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, subscription_id)