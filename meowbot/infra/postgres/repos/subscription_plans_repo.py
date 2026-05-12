from __future__ import annotations

import json
from typing import Any

import asyncpg


class SubscriptionPlansRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def get_by_id(self, plan_id) -> dict[str, Any] | None:
        query = """
        select
            id,
            code,
            name,
            price_usd,
            duration_days,
            features_json,
            is_active,
            sort_order,
            created_at,
            updated_at
        from subscription_plans
        where id = $1
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, plan_id)
        if not row:
            return None
        return self._normalize_row(dict(row))

    async def get_by_code(self, code: str) -> dict[str, Any] | None:
        query = """
        select
            id,
            code,
            name,
            price_usd,
            duration_days,
            features_json,
            is_active,
            sort_order,
            created_at,
            updated_at
        from subscription_plans
        where lower(code) = lower($1)
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, code)
        if not row:
            return None
        return self._normalize_row(dict(row))

    async def list_active(
        self,
        *,
        include_free: bool = False,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        query = """
        select
            id,
            code,
            name,
            price_usd,
            duration_days,
            features_json,
            is_active,
            sort_order,
            created_at,
            updated_at
        from subscription_plans
        where is_active = true
          and ($1::boolean = true or lower(code) <> 'free')
        order by sort_order asc nulls last, price_usd asc nulls first, code asc
        limit $2
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, include_free, limit)
        return [self._normalize_row(dict(row)) for row in rows]

    def _normalize_row(self, data: dict[str, Any]) -> dict[str, Any]:
        features = data.get("features_json")
        if isinstance(features, str):
            try:
                parsed = json.loads(features)
                data["features_json"] = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                data["features_json"] = {}
        elif features is None:
            data["features_json"] = {}
        elif not isinstance(features, dict):
            data["features_json"] = {}
        return data
