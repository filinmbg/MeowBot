from __future__ import annotations

from typing import Any

import asyncpg


class TrialExpirationRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def expire_trials_and_fallback_to_free(self) -> list[dict[str, Any]]:
        """
        Викликає SQL-функцію:
            expire_trials_and_fallback_to_free()

        Очікує rows типу:
            affected_user_id
            action
        """
        query = """
        select *
        from expire_trials_and_fallback_to_free()
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query)
        return [dict(row) for row in rows]

    async def get_users_for_recent_trial_expirations(
        self,
        *,
        lookback_minutes: int = 10,
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
            us.updated_at,
            us.is_trial,
            us.trial_code,
            us.ended_reason,

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
            us.is_trial = true
            and us.status = 'expired'
            and us.ended_reason = 'trial_expired'
            and us.updated_at >= (now() - make_interval(mins => $1))
            and tp.chat_id is not null
        order by us.updated_at desc
        limit $2
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, int(lookback_minutes), int(limit))
        return [dict(row) for row in rows]