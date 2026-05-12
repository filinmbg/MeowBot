from __future__ import annotations

from typing import Any

import asyncpg


class AdminAuditLogsRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def create(
        self,
        *,
        actor_user_id,
        target_user_id,
        action: str,
        entity_type: str,
        entity_id: str | None,
        details_json: dict[str, Any] | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> dict[str, Any]:
        own_conn = conn is None
        if own_conn:
            conn = await self.pool.acquire()
        assert conn is not None
        try:
            row = await conn.fetchrow(
                """
                insert into admin_audit_logs (
                    actor_user_id,
                    target_user_id,
                    action,
                    entity_type,
                    entity_id,
                    details_json
                )
                values ($1, $2, $3, $4, $5, coalesce($6::jsonb, '{}'::jsonb))
                returning
                    id,
                    actor_user_id,
                    target_user_id,
                    action,
                    entity_type,
                    entity_id,
                    details_json,
                    created_at
                """,
                actor_user_id,
                target_user_id,
                action,
                entity_type,
                entity_id,
                details_json,
            )
            return dict(row)
        finally:
            if own_conn:
                await self.pool.release(conn)
