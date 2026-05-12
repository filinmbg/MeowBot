from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]


async def _run(sql_path: Path) -> None:
    load_dotenv(ROOT / ".env")
    dsn = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL") or os.getenv("PG_DSN")
    if not dsn:
        raise RuntimeError("POSTGRES_URL, DATABASE_URL, or PG_DSN is not set")

    sql = sql_path.read_text(encoding="utf-8")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply a Postgres SQL file using project .env settings.")
    parser.add_argument("sql_file", help="Path to the SQL file to execute")
    args = parser.parse_args()

    sql_path = Path(args.sql_file)
    if not sql_path.is_absolute():
        sql_path = ROOT / sql_path
    if not sql_path.exists():
        raise FileNotFoundError(sql_path)

    asyncio.run(_run(sql_path))
    print(f"Applied SQL: {sql_path}")


if __name__ == "__main__":
    main()
