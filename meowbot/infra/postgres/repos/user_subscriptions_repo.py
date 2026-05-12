from __future__ import annotations

import json
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
            coalesce(strategy_version, 'v1') as strategy_version,
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

    async def create_free_subscription(self, *, user_id, plan_id, strategy_version: str = "v2") -> dict[str, Any]:
        strategy_version = self._normalize_strategy_version(strategy_version)
        query = """
        insert into user_subscriptions (
            user_id,
            plan_id,
            status,
            starts_at,
            ends_at,
            auto_renew,
            is_trial,
            strategy_version
        )
        values (
            $1,
            $2,
            'active',
            now(),
            null,
            false,
            false,
            $3
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
            coalesce(strategy_version, 'v1') as strategy_version,
            trial_code,
            ended_reason,
            trial_expiry_warning_sent_at,
            created_at,
            updated_at
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id, plan_id, strategy_version)
        return dict(row)

    async def get_active_detailed_by_user_id(self, user_id) -> dict[str, Any] | None:
        query = """
        select
            us.id,
            us.user_id,
            us.plan_id,
            us.status,
            us.starts_at,
            us.ends_at,
            us.auto_renew,
            us.is_trial,
            coalesce(us.strategy_version, 'v1') as strategy_version,
            us.trial_code,
            us.ended_reason,
            us.trial_expiry_warning_sent_at,
            us.created_at,
            us.updated_at,
            sp.code as plan_code,
            sp.name as plan_name,
            sp.features_json
        from user_subscriptions us
        join subscription_plans sp
            on sp.id = us.plan_id
        where us.user_id = $1
          and us.status = 'active'
        order by us.created_at desc
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id)
        return self._normalize_row(dict(row)) if row else None

    async def list_active_trials(self, limit: int = 100) -> list[dict[str, Any]]:
        query = """
        select
            us.id as subscription_id,
            us.user_id,
            us.plan_id,
            us.status,
            us.starts_at,
            us.ends_at,
            us.is_trial,
            coalesce(us.strategy_version, 'v1') as strategy_version,
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
            tp.last_name
        from user_subscriptions us
        join subscription_plans sp
            on sp.id = us.plan_id
        join users u
            on u.id = us.user_id
        join telegram_profiles tp
            on tp.user_id = u.id
        where us.status = 'active'
          and us.is_trial = true
        order by us.ends_at asc nulls last
        limit $1
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, int(limit))
        return [dict(row) for row in rows]

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        features = row.get("features_json")
        if isinstance(features, str):
            try:
                parsed = json.loads(features)
                row["features_json"] = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                row["features_json"] = {}
        elif features is None:
            row["features_json"] = {}
        elif not isinstance(features, dict):
            row["features_json"] = {}
        return row

    @staticmethod
    def _normalize_strategy_version(value: str | None) -> str:
        normalized = str(value or "v1").strip().lower()
        return normalized if normalized in {"v1", "v2"} else "v1"
