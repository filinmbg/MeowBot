from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")


async def main() -> int:
    dsn = os.getenv("PG_DSN")

    try:
        if dsn:
            print("Using PG_DSN from .env/environment...")
            conn = await asyncpg.connect(dsn=dsn)
        else:
            print("Using PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/PG_DB from .env/environment...")
            conn = await asyncpg.connect(
                host=os.getenv("PG_HOST", "localhost"),
                port=int(os.getenv("PG_PORT", 5432)),
                user=os.getenv("PG_USER", "postgres"),
                password=os.getenv("PG_PASSWORD", "postgres"),
                database=os.getenv("PG_DB", "meowbot"),
            )

        try:
            value = await conn.fetchval("SELECT 1;")
            current_db = await conn.fetchval("SELECT current_database();")
            current_user = await conn.fetchval("SELECT current_user;")
            version = await conn.fetchval("SELECT version();")

            print("✅ Postgres connection successful")
            print(f"SELECT 1 => {value}")
            print(f"database  => {current_db}")
            print(f"user      => {current_user}")
            print(f"version   => {version}")
            return 0
        finally:
            await conn.close()

    except Exception as e:
        print("❌ Postgres connection failed")
        print(f"{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))