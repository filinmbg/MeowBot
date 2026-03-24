from __future__ import annotations

import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")


async def get_pg_pool():
    dsn = os.getenv("PG_DSN")
    if dsn:
        return await asyncpg.create_pool(
            dsn=dsn,
            min_size=1,
            max_size=10,
        )

    return await asyncpg.create_pool(
        host=os.getenv("PG_HOST", "localhost"),
        port=int(os.getenv("PG_PORT", 5432)),
        user=os.getenv("PG_USER", "postgres"),
        password=os.getenv("PG_PASSWORD", "postgres"),
        database=os.getenv("PG_DB", "meowbot"),
        min_size=1,
        max_size=10,
    )