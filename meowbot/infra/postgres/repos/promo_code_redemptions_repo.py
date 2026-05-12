from __future__ import annotations

import asyncpg


class PromoCodeRedemptionsRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def get_by_promo_and_user(
        self,
        *,
        promo_code_id,
        user_id,
        conn: asyncpg.Connection | None = None,
    ) -> dict | None:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            row = await conn.fetchrow(
                """
                select
                    id,
                    promo_code_id,
                    user_id,
                    purchase_intent_id,
                    plan_id,
                    discount_percent,
                    discount_amount,
                    redeemed_at
                from promo_code_redemptions
                where promo_code_id = $1
                  and user_id = $2
                limit 1
                """,
                promo_code_id,
                user_id,
            )
            return dict(row) if row else None
        finally:
            if own_conn:
                await self.pool.release(conn)

    async def get_by_purchase_intent_id(
        self,
        *,
        purchase_intent_id,
        conn: asyncpg.Connection | None = None,
    ) -> dict | None:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            row = await conn.fetchrow(
                """
                select
                    id,
                    promo_code_id,
                    user_id,
                    purchase_intent_id,
                    plan_id,
                    discount_percent,
                    discount_amount,
                    redeemed_at
                from promo_code_redemptions
                where purchase_intent_id = $1
                limit 1
                """,
                purchase_intent_id,
            )
            return dict(row) if row else None
        finally:
            if own_conn:
                await self.pool.release(conn)

    async def create(
        self,
        *,
        promo_code_id,
        user_id,
        purchase_intent_id,
        plan_id,
        discount_percent,
        discount_amount,
        conn: asyncpg.Connection | None = None,
    ) -> dict:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            row = await conn.fetchrow(
                """
                insert into promo_code_redemptions (
                    promo_code_id,
                    user_id,
                    purchase_intent_id,
                    plan_id,
                    discount_percent,
                    discount_amount,
                    redeemed_at
                )
                values ($1, $2, $3, $4, $5, $6, now())
                returning
                    id,
                    promo_code_id,
                    user_id,
                    purchase_intent_id,
                    plan_id,
                    discount_percent,
                    discount_amount,
                    redeemed_at
                """,
                promo_code_id,
                user_id,
                purchase_intent_id,
                plan_id,
                discount_percent,
                discount_amount,
            )
            return dict(row)
        finally:
            if own_conn:
                await self.pool.release(conn)

    async def list_recent(self, *, limit: int = 20) -> list[dict]:
        query = """
        select
            pcr.id,
            pcr.user_id,
            pcr.purchase_intent_id,
            pcr.plan_id,
            pcr.discount_percent,
            pcr.discount_amount,
            pcr.redeemed_at,
            pc.code as promo_code,
            sp.code as plan_code,
            u.display_name,
            u.email,
            tp.telegram_id,
            tp.username
        from promo_code_redemptions pcr
        join promo_codes pc
            on pc.id = pcr.promo_code_id
        join subscription_plans sp
            on sp.id = pcr.plan_id
        join users u
            on u.id = pcr.user_id
        left join telegram_profiles tp
            on tp.user_id = u.id
        order by pcr.redeemed_at desc
        limit $1
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, int(limit))
        return [dict(row) for row in rows]
