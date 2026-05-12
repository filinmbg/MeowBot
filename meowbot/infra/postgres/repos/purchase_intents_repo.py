from __future__ import annotations

from typing import Any, Iterable

import asyncpg


class PurchaseIntentsRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def expire_stale(self, *, conn: asyncpg.Connection | None = None) -> int:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            result = await conn.execute(
                """
                update purchase_intents
                set
                    status = 'expired',
                    updated_at = now()
                where status = 'awaiting_payment'
                  and expires_at <= now()
                """
            )
            return int(result.split()[-1])
        finally:
            if own_conn:
                await self.pool.release(conn)

    async def create(
        self,
        *,
        order_code: str,
        user_id,
        plan_id,
        plan_code: str,
        payment_method_code: str,
        base_amount_usd,
        discount_percent,
        discount_amount_usd,
        final_amount_usd,
        promo_code_id,
        status: str,
        expires_at,
        metadata_json: dict[str, Any] | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> dict[str, Any]:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            row = await conn.fetchrow(
                """
                insert into purchase_intents (
                    order_code,
                    user_id,
                    plan_id,
                    plan_code,
                    payment_method_code,
                    base_amount_usd,
                    discount_percent,
                    discount_amount_usd,
                    final_amount_usd,
                    promo_code_id,
                    status,
                    expires_at,
                    metadata_json
                )
                values (
                    $1, $2, $3, $4, $5,
                    $6, $7, $8, $9, $10,
                    $11, $12, coalesce($13::jsonb, '{}'::jsonb)
                )
                returning
                    id,
                    order_code,
                    user_id,
                    plan_id,
                    plan_code,
                    payment_method_code,
                    base_amount_usd,
                    discount_percent,
                    discount_amount_usd,
                    final_amount_usd,
                    promo_code_id,
                    status,
                    created_at,
                    expires_at,
                    updated_at,
                    metadata_json
                """,
                order_code,
                user_id,
                plan_id,
                plan_code,
                payment_method_code,
                base_amount_usd,
                discount_percent,
                discount_amount_usd,
                final_amount_usd,
                promo_code_id,
                status,
                expires_at,
                metadata_json,
            )
            return dict(row)
        finally:
            if own_conn:
                await self.pool.release(conn)

    async def get_latest_open_by_user_and_plan(
        self,
        *,
        user_id,
        plan_code: str,
        statuses: Iterable[str] = ("awaiting_payment", "awaiting_manual_check"),
    ) -> dict[str, Any] | None:
        query = """
        select
            id,
            order_code,
            user_id,
            plan_id,
            plan_code,
            payment_method_code,
            base_amount_usd,
            discount_percent,
            discount_amount_usd,
            final_amount_usd,
            promo_code_id,
            status,
            created_at,
            expires_at,
            updated_at,
            metadata_json
        from purchase_intents
        where user_id = $1
          and lower(plan_code) = lower($2)
          and status = any($3::text[])
        order by created_at desc
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id, plan_code, list(statuses))
        return dict(row) if row else None

    async def get_by_id(self, intent_id, *, conn: asyncpg.Connection | None = None) -> dict[str, Any] | None:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            row = await conn.fetchrow(
                """
                select
                    pi.id,
                    pi.order_code,
                    pi.user_id,
                    pi.plan_id,
                    pi.plan_code,
                    pi.payment_method_code,
                    pi.base_amount_usd,
                    pi.discount_percent,
                    pi.discount_amount_usd,
                    pi.final_amount_usd,
                    pi.promo_code_id,
                    pi.status,
                    pi.created_at,
                    pi.expires_at,
                    pi.updated_at,
                    pi.metadata_json,
                    sp.name as plan_name,
                    sp.duration_days
                from purchase_intents pi
                join subscription_plans sp
                    on sp.id = pi.plan_id
                where pi.id = $1
                limit 1
                """,
                intent_id,
            )
            return dict(row) if row else None
        finally:
            if own_conn:
                await self.pool.release(conn)

    async def get_by_id_for_user(self, *, intent_id, user_id) -> dict[str, Any] | None:
        query = """
        select
            pi.id,
            pi.order_code,
            pi.user_id,
            pi.plan_id,
            pi.plan_code,
            pi.payment_method_code,
            pi.base_amount_usd,
            pi.discount_percent,
            pi.discount_amount_usd,
            pi.final_amount_usd,
            pi.promo_code_id,
            pi.status,
            pi.created_at,
            pi.expires_at,
            pi.updated_at,
            pi.metadata_json,
            sp.name as plan_name,
            sp.duration_days
        from purchase_intents pi
        join subscription_plans sp
            on sp.id = pi.plan_id
        where pi.id = $1
          and pi.user_id = $2
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, intent_id, user_id)
        return dict(row) if row else None

    async def update_status(
        self,
        *,
        intent_id,
        status: str,
        conn: asyncpg.Connection | None = None,
    ) -> dict[str, Any] | None:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            row = await conn.fetchrow(
                """
                update purchase_intents
                set
                    status = $2,
                    updated_at = now()
                where id = $1
                returning
                    id,
                    order_code,
                    user_id,
                    plan_id,
                    plan_code,
                    payment_method_code,
                    base_amount_usd,
                    discount_percent,
                    discount_amount_usd,
                    final_amount_usd,
                    promo_code_id,
                    status,
                    created_at,
                    expires_at,
                    updated_at,
                    metadata_json
                """,
                intent_id,
                status,
            )
            return dict(row) if row else None
        finally:
            if own_conn:
                await self.pool.release(conn)

    async def cancel_for_user(self, *, intent_id, user_id) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                update purchase_intents
                set
                    status = 'cancelled',
                    updated_at = now()
                where id = $1
                  and user_id = $2
                  and status in ('created', 'awaiting_payment')
                returning
                    id,
                    order_code,
                    user_id,
                    plan_id,
                    plan_code,
                    payment_method_code,
                    base_amount_usd,
                    discount_percent,
                    discount_amount_usd,
                    final_amount_usd,
                    promo_code_id,
                    status,
                    created_at,
                    expires_at,
                    updated_at,
                    metadata_json
                """,
                intent_id,
                user_id,
            )
        return dict(row) if row else None

    async def list_for_admin(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        query = """
        select
            pi.id,
            pi.order_code,
            pi.user_id,
            pi.plan_id,
            pi.plan_code,
            pi.base_amount_usd,
            pi.discount_percent,
            pi.discount_amount_usd,
            pi.final_amount_usd,
            pi.status,
            pi.created_at,
            pi.expires_at,
            pi.updated_at,
            sp.name as plan_name,
            u.display_name,
            u.email,
            tp.telegram_id,
            tp.username
        from purchase_intents pi
        join subscription_plans sp
            on sp.id = pi.plan_id
        join users u
            on u.id = pi.user_id
        left join telegram_profiles tp
            on tp.user_id = u.id
        where ($1::text is null or pi.status = $1)
        order by pi.created_at desc
        limit $2
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, status, int(limit))
        return [dict(row) for row in rows]
