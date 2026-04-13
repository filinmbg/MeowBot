from __future__ import annotations

from typing import Any

import asyncpg


class SubscriptionPlansRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

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
        return dict(row) if row else None