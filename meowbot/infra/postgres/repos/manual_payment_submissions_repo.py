from __future__ import annotations

import asyncpg


class ManualPaymentSubmissionsRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def create_or_get(
        self,
        *,
        purchase_intent_id,
        payment_id,
        user_id,
        submitted_tx_hash: str,
        submitted_amount=None,
        submitted_network: str | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> dict:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            existing = await conn.fetchrow(
                """
                select
                    id,
                    purchase_intent_id,
                    payment_id,
                    user_id,
                    submitted_tx_hash,
                    submitted_amount,
                    submitted_network,
                    status,
                    admin_reviewed_by,
                    admin_reviewed_at,
                    admin_comment,
                    created_at,
                    updated_at
                from manual_payment_submissions
                where purchase_intent_id = $1
                limit 1
                """,
                purchase_intent_id,
            )
            if existing:
                return dict(existing)

            row = await conn.fetchrow(
                """
                insert into manual_payment_submissions (
                    purchase_intent_id,
                    payment_id,
                    user_id,
                    submitted_tx_hash,
                    submitted_amount,
                    submitted_network,
                    status
                )
                values ($1, $2, $3, $4, $5, $6, 'pending')
                returning
                    id,
                    purchase_intent_id,
                    payment_id,
                    user_id,
                    submitted_tx_hash,
                    submitted_amount,
                    submitted_network,
                    status,
                    admin_reviewed_by,
                    admin_reviewed_at,
                    admin_comment,
                    created_at,
                    updated_at
                """,
                purchase_intent_id,
                payment_id,
                user_id,
                submitted_tx_hash,
                submitted_amount,
                submitted_network,
            )
            return dict(row)
        finally:
            if own_conn:
                await self.pool.release(conn)

    async def get_by_id(self, submission_id, *, conn: asyncpg.Connection | None = None) -> dict | None:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            row = await conn.fetchrow(
                """
                select
                    mps.id,
                    mps.purchase_intent_id,
                    mps.payment_id,
                    mps.user_id,
                    mps.submitted_tx_hash,
                    mps.submitted_amount,
                    mps.submitted_network,
                    mps.status,
                    mps.admin_reviewed_by,
                    mps.admin_reviewed_at,
                    mps.admin_comment,
                    mps.created_at,
                    mps.updated_at,
                    pi.order_code,
                    pi.plan_code,
                    pi.final_amount_usd,
                    pi.status as purchase_status,
                    sp.name as plan_name,
                    u.display_name,
                    u.email,
                    u.preferred_language,
                    tp.telegram_id,
                    tp.chat_id,
                    tp.username
                from manual_payment_submissions mps
                join purchase_intents pi
                    on pi.id = mps.purchase_intent_id
                join subscription_plans sp
                    on sp.id = pi.plan_id
                join users u
                    on u.id = mps.user_id
                left join telegram_profiles tp
                    on tp.user_id = u.id
                where mps.id = $1
                limit 1
                """,
                submission_id,
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
                    purchase_intent_id,
                    payment_id,
                    user_id,
                    submitted_tx_hash,
                    submitted_amount,
                    submitted_network,
                    status,
                    admin_reviewed_by,
                    admin_reviewed_at,
                    admin_comment,
                    created_at,
                    updated_at
                from manual_payment_submissions
                where purchase_intent_id = $1
                limit 1
                """,
                purchase_intent_id,
            )
            return dict(row) if row else None
        finally:
            if own_conn:
                await self.pool.release(conn)

    async def list_pending(self, *, limit: int = 20) -> list[dict]:
        query = """
        select
            mps.id,
            mps.purchase_intent_id,
            mps.payment_id,
            mps.user_id,
            mps.submitted_tx_hash,
            mps.status,
            mps.created_at,
            pi.order_code,
            pi.plan_code,
            pi.final_amount_usd,
            sp.name as plan_name,
            u.display_name,
            u.email,
            tp.telegram_id,
            tp.username
        from manual_payment_submissions mps
        join purchase_intents pi
            on pi.id = mps.purchase_intent_id
        join subscription_plans sp
            on sp.id = pi.plan_id
        join users u
            on u.id = mps.user_id
        left join telegram_profiles tp
            on tp.user_id = u.id
        where mps.status = 'pending'
        order by mps.created_at asc
        limit $1
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, int(limit))
        return [dict(row) for row in rows]

    async def review(
        self,
        *,
        submission_id,
        status: str,
        admin_user_id,
        admin_comment: str | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> dict | None:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            row = await conn.fetchrow(
                """
                update manual_payment_submissions
                set
                    status = $2,
                    admin_reviewed_by = $3,
                    admin_reviewed_at = now(),
                    admin_comment = $4,
                    updated_at = now()
                where id = $1
                returning
                    id,
                    purchase_intent_id,
                    payment_id,
                    user_id,
                    submitted_tx_hash,
                    submitted_amount,
                    submitted_network,
                    status,
                    admin_reviewed_by,
                    admin_reviewed_at,
                    admin_comment,
                    created_at,
                    updated_at
                """,
                submission_id,
                status,
                admin_user_id,
                admin_comment,
            )
            return dict(row) if row else None
        finally:
            if own_conn:
                await self.pool.release(conn)
