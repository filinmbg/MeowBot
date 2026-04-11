from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg


class TradeEntryCooldownsRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def get_active_cooldown(
        self,
        *,
        runtime_user_id: str,
        symbol: str,
        reason: str = "loss_stop",
    ) -> dict[str, Any] | None:
        query = """
        select
            id,
            user_id,
            runtime_user_id,
            symbol,
            reason,
            cooldown_until,
            created_at,
            updated_at
        from trade_entry_cooldowns
        where runtime_user_id = $1
          and symbol = $2
          and reason = $3
          and cooldown_until > now()
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, runtime_user_id, symbol.upper(), reason)
        return dict(row) if row else None

    async def upsert_loss_stop_cooldown(
        self,
        *,
        runtime_user_id: str,
        symbol: str,
        cooldown_until: datetime,
        user_id: UUID | None = None,
    ) -> None:
        query = """
        insert into trade_entry_cooldowns (
            user_id,
            runtime_user_id,
            symbol,
            reason,
            cooldown_until
        )
        values ($1, $2, $3, 'loss_stop', $4)
        on conflict (runtime_user_id, symbol, reason)
        do update set
            user_id = excluded.user_id,
            cooldown_until = excluded.cooldown_until,
            updated_at = now()
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                user_id,
                runtime_user_id,
                symbol.upper(),
                cooldown_until,
            )

    async def delete_expired(self) -> int:
        query = """
        delete from trade_entry_cooldowns
        where cooldown_until <= now()
        """
        async with self.pool.acquire() as conn:
            result = await conn.execute(query)

        # result format: "DELETE <n>"
        return int(result.split()[-1])

    async def clear_cooldown(
        self,
        *,
        runtime_user_id: str,
        symbol: str,
        reason: str = "loss_stop",
    ) -> None:
        query = """
        delete from trade_entry_cooldowns
        where runtime_user_id = $1
          and symbol = $2
          and reason = $3
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, runtime_user_id, symbol.upper(), reason)

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(timezone.utc)