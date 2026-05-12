from __future__ import annotations

import os

import asyncpg


async def get_pg_pool() -> asyncpg.Pool:
    dsn = (
        os.getenv("POSTGRES_URL")
        or os.getenv("DATABASE_URL")
        or os.getenv("PG_DSN")
    )
    if not dsn:
        raise RuntimeError("POSTGRES_URL, DATABASE_URL, or PG_DSN is not set")

    min_size = int(os.getenv("PG_POOL_MIN_SIZE", "1"))
    max_size = int(os.getenv("PG_POOL_MAX_SIZE", "2"))
    command_timeout = float(os.getenv("PG_COMMAND_TIMEOUT", "30"))
    max_inactive_connection_lifetime = float(
        os.getenv("PG_MAX_INACTIVE_CONNECTION_LIFETIME", "30")
    )

    return await asyncpg.create_pool(
        dsn=dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=command_timeout,
        max_inactive_connection_lifetime=max_inactive_connection_lifetime,
    )
