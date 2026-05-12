from __future__ import annotations

from typing import Any

import asyncpg


class AdminTestUsersRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def get_test_users_by_emails(self, emails: list[str]) -> list[dict[str, Any]]:
        if not emails:
            return []

        query = """
        with active_sub as (
            select distinct on (us.user_id)
                us.user_id,
                us.id as subscription_id,
                us.plan_id,
                us.status,
                us.is_trial,
                us.ends_at,
                sp.code as plan_code,
                sp.name as plan_name,
                coalesce(us.strategy_version, 'v1') as strategy_version
            from user_subscriptions us
            join subscription_plans sp
                on sp.id = us.plan_id
            where us.status = 'active'
            order by us.user_id, us.created_at desc
        ),
        requested_emails as (
            select unnest($1::text[]) as email
        )
        select
            u.id as user_id,
            u.email,
            u.display_name,
            u.preferred_language,
            tp.telegram_id,
            tp.chat_id,
            ('tg:' || tp.telegram_id::text) as runtime_user_id,
            ts.trading_mode,
            coalesce(ts.trading_enabled, false) as trading_enabled,
            active_sub.subscription_id,
            active_sub.plan_id,
            active_sub.plan_code,
            active_sub.plan_name,
            coalesce(active_sub.strategy_version, 'v1') as strategy_version,
            active_sub.is_trial,
            active_sub.ends_at
        from users u
        join telegram_profiles tp
            on tp.user_id = u.id
        left join trader_settings ts
            on ts.user_id = u.id
        left join active_sub
            on active_sub.user_id = u.id
        where lower(u.email) in (select email from requested_emails)
           or lower(coalesce(u.email, '')) like '%\\_test\\_v2@example.com'
           or lower(coalesce(tp.username, '')) like '%\\_test\\_v2'
           or (
                (
                    coalesce(active_sub.strategy_version, 'v1') = 'v2'
                    or lower(coalesce(active_sub.plan_code, '')) like '%\\_v2'
                )
                and (
                    lower(coalesce(u.email, '')) like '%test%'
                    or lower(coalesce(tp.username, '')) like '%test%'
                )
           )
        order by
            case
                when coalesce(active_sub.strategy_version, 'v1') = 'v2'
                  or lower(coalesce(active_sub.plan_code, '')) like '%\\_v2'
                  or lower(coalesce(u.email, '')) like '%\\_test\\_v2@example.com'
                  or lower(coalesce(tp.username, '')) like '%\\_test\\_v2'
                    then 2
                else 1
            end,
            case regexp_replace(lower(coalesce(active_sub.plan_code, 'unknown')), '_v2$', '')
                when 'free' then 1
                when 'basic' then 2
                when 'pro' then 3
                when 'vip' then 4
                else 99
            end,
            u.email asc
        """
        normalized = [x.strip().lower() for x in emails if x.strip()]
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, normalized)
        return [dict(row) for row in rows]

    async def count_active_cooldowns_by_runtime_user_ids(
        self,
        runtime_user_ids: list[str],
    ) -> dict[str, int]:
        if not runtime_user_ids:
            return {}

        query = """
        select
            runtime_user_id,
            count(*)::int as cnt
        from trade_entry_cooldowns
        where runtime_user_id = any($1::text[])
          and cooldown_until > now()
        group by runtime_user_id
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, runtime_user_ids)

        return {str(row["runtime_user_id"]): int(row["cnt"]) for row in rows}
