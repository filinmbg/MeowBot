from __future__ import annotations

from typing import Any

import asyncpg


class PaymentsRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def create_pending(
        self,
        *,
        user_id,
        purchase_intent_id,
        provider: str,
        provider_code: str,
        currency_code: str,
        network_code: str,
        expected_amount,
        wallet_address: str,
        payment_method_code: str,
        raw_payload_json: dict[str, Any] | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> dict[str, Any]:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            row = await conn.fetchrow(
                """
                insert into payments (
                    user_id,
                    purchase_intent_id,
                    provider,
                    provider_code,
                    currency,
                    network_code,
                    amount,
                    expected_amount,
                    paid_amount,
                    payment_method_code,
                    wallet_address,
                    tx_hash,
                    status,
                    raw_payload
                )
                values (
                    $1, $2, $3, $4, $5, $6,
                    $7, $7, null, $8, $9, null,
                    'pending',
                    coalesce($10::jsonb, '{}'::jsonb)
                )
                returning
                    id,
                    user_id,
                    purchase_intent_id,
                    provider,
                    provider_code,
                    currency,
                    network_code,
                    amount,
                    expected_amount,
                    paid_amount,
                    payment_method_code,
                    wallet_address,
                    tx_hash,
                    status,
                    paid_at,
                    raw_payload as raw_payload_json,
                    comment,
                    created_at,
                    updated_at
                """,
                user_id,
                purchase_intent_id,
                provider,
                provider_code,
                currency_code,
                network_code,
                expected_amount,
                payment_method_code,
                wallet_address,
                raw_payload_json,
            )
            return dict(row)
        finally:
            if own_conn:
                await self.pool.release(conn)

    async def get_by_purchase_intent_id(
        self,
        *,
        purchase_intent_id,
        conn: asyncpg.Connection | None = None,
    ) -> dict[str, Any] | None:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            row = await conn.fetchrow(
                """
                select
                    id,
                    user_id,
                    purchase_intent_id,
                    provider,
                    provider_code,
                    currency,
                    network_code,
                    amount,
                    expected_amount,
                    paid_amount,
                    payment_method_code,
                    wallet_address,
                    tx_hash,
                    status,
                    paid_at,
                    raw_payload as raw_payload_json,
                    comment,
                    created_at,
                    updated_at
                from payments
                where purchase_intent_id = $1
                order by created_at desc
                limit 1
                """,
                purchase_intent_id,
            )
            return dict(row) if row else None
        finally:
            if own_conn:
                await self.pool.release(conn)

    async def update_submission(
        self,
        *,
        payment_id,
        tx_hash: str,
        submitted_amount=None,
        submitted_network: str | None = None,
        raw_payload_json: dict[str, Any] | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> dict[str, Any] | None:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            row = await conn.fetchrow(
                """
                update payments
                set
                    tx_hash = $2,
                    paid_amount = coalesce($3, paid_amount),
                    network_code = coalesce($4, network_code),
                    status = 'submitted',
                    raw_payload = coalesce($5::jsonb, raw_payload),
                    updated_at = now()
                where id = $1
                returning
                    id,
                    user_id,
                    purchase_intent_id,
                    provider,
                    provider_code,
                    currency,
                    network_code,
                    amount,
                    expected_amount,
                    paid_amount,
                    payment_method_code,
                    wallet_address,
                    tx_hash,
                    status,
                    paid_at,
                    raw_payload as raw_payload_json,
                    comment,
                    created_at,
                    updated_at
                """,
                payment_id,
                tx_hash,
                submitted_amount,
                submitted_network,
                raw_payload_json,
            )
            return dict(row) if row else None
        finally:
            if own_conn:
                await self.pool.release(conn)

    async def update_status(
        self,
        *,
        payment_id,
        status: str,
        paid_amount=None,
        comment: str | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> dict[str, Any] | None:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            row = await conn.fetchrow(
                """
                update payments
                set
                    status = $2,
                    paid_amount = coalesce($3, paid_amount),
                    comment = coalesce($4, comment),
                    paid_at = case when $2 in ('succeeded', 'success') then coalesce(paid_at, now()) else paid_at end,
                    updated_at = now()
                where id = $1
                returning
                    id,
                    user_id,
                    purchase_intent_id,
                    provider,
                    provider_code,
                    currency,
                    network_code,
                    amount,
                    expected_amount,
                    paid_amount,
                    payment_method_code,
                    wallet_address,
                    tx_hash,
                    status,
                    paid_at,
                    raw_payload as raw_payload_json,
                    comment,
                    created_at,
                    updated_at
                """,
                payment_id,
                status,
                paid_amount,
                comment,
            )
            return dict(row) if row else None
        finally:
            if own_conn:
                await self.pool.release(conn)

    async def expire_by_intent(self, *, purchase_intent_id) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                update payments
                set
                    status = 'expired',
                    updated_at = now()
                where purchase_intent_id = $1
                  and status = 'pending'
                """,
                purchase_intent_id,
            )
