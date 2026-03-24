from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from meowbot.infra.postgres.client import get_pg_pool


DEFAULT_SQL_FILE = Path(r"D:\Project\MeowBot\meowbot\infra\postgres\sql\001_create_users.sql")


async def main() -> int:
    sql_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SQL_FILE

    if not sql_path.exists():
        print(f"❌ SQL file not found: {sql_path}")
        return 1

    pool = await get_pg_pool()
    try:
        sql = sql_path.read_text(encoding="utf-8")
        async with pool.acquire() as conn:
            await conn.execute(sql)
        print(f"✅ Applied SQL: {sql_path.name}")
        return 0
    except Exception as e:
        print(f"❌ Failed to apply SQL: {sql_path.name}")
        print(f"{type(e).__name__}: {e}")
        return 1
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))