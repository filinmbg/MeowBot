from __future__ import annotations

import asyncpg


class PromoCodesRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def get_by_code(self, code: str, *, conn: asyncpg.Connection | None = None) -> dict | None:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            row = await conn.fetchrow(
                """
                select
                    id,
                    code,
                    discount_percent,
                    max_redemptions,
                    used_redemptions,
                    active_from,
                    active_to,
                    is_active,
                    applies_to_all_paid_plans,
                    specific_plan_id,
                    created_by_user_id,
                    created_at,
                    updated_at
                from promo_codes
                where lower(code) = lower($1)
                limit 1
                """,
                code,
            )
            return dict(row) if row else None
        finally:
            if own_conn:
                await self.pool.release(conn)

    async def create(
        self,
        *,
        code: str,
        discount_percent,
        max_redemptions: int,
        active_from,
        active_to,
        applies_to_all_paid_plans: bool,
        specific_plan_id,
        created_by_user_id,
    ) -> dict:
        query = """
        insert into promo_codes (
            code,
            discount_percent,
            max_redemptions,
            used_redemptions,
            active_from,
            active_to,
            is_active,
            applies_to_all_paid_plans,
            specific_plan_id,
            created_by_user_id
        )
        values ($1, $2, $3, 0, $4, $5, true, $6, $7, $8)
        returning
            id,
            code,
            discount_percent,
            max_redemptions,
            used_redemptions,
            active_from,
            active_to,
            is_active,
            applies_to_all_paid_plans,
            specific_plan_id,
            created_by_user_id,
            created_at,
            updated_at
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                code,
                discount_percent,
                int(max_redemptions),
                active_from,
                active_to,
                applies_to_all_paid_plans,
                specific_plan_id,
                created_by_user_id,
            )
        return dict(row)

    async def disable(self, *, promo_id, conn: asyncpg.Connection | None = None) -> dict | None:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            row = await conn.fetchrow(
                """
                update promo_codes
                set
                    is_active = false,
                    updated_at = now()
                where id = $1
                returning
                    id,
                    code,
                    discount_percent,
                    max_redemptions,
                    used_redemptions,
                    active_from,
                    active_to,
                    is_active,
                    applies_to_all_paid_plans,
                    specific_plan_id,
                    created_by_user_id,
                    created_at,
                    updated_at
                """,
                promo_id,
            )
            return dict(row) if row else None
        finally:
            if own_conn:
                await self.pool.release(conn)

    async def list_recent(self, *, limit: int = 20) -> list[dict]:
        query = """
        select
            pc.id,
            pc.code,
            pc.discount_percent,
            pc.max_redemptions,
            pc.used_redemptions,
            pc.active_from,
            pc.active_to,
            pc.is_active,
            pc.applies_to_all_paid_plans,
            pc.specific_plan_id,
            sp.code as specific_plan_code,
            pc.created_at,
            pc.updated_at
        from promo_codes pc
        left join subscription_plans sp
            on sp.id = pc.specific_plan_id
        order by pc.created_at desc
        limit $1
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, int(limit))
        return [dict(row) for row in rows]

    async def increment_used_redemptions(self, *, promo_id, conn: asyncpg.Connection | None = None) -> None:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            await conn.execute(
                """
                update promo_codes
                set
                    used_redemptions = used_redemptions + 1,
                    updated_at = now()
                where id = $1
                """,
                promo_id,
            )
        finally:
            if own_conn:
                await self.pool.release(conn)
