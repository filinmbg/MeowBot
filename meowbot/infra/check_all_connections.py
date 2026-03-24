from __future__ import annotations

import asyncio

from meowbot.infra.mongo.client import MongoConn, MongoConfig
from meowbot.infra.postgres.client import get_pg_pool


async def check_postgres():
    try:
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval("SELECT 1;")
        await pool.close()
        print("✅ Postgres OK (SELECT 1 =", val, ")")
        return True
    except Exception as e:
        print("❌ Postgres FAILED")
        print(e)
        return False


def check_mongo():
    try:
        mongo = MongoConn(MongoConfig())
        ok = mongo.ping()
        mongo.close()

        if ok:
            print("✅ Mongo OK")
        else:
            print("❌ Mongo FAILED")

        return ok
    except Exception as e:
        print("❌ Mongo FAILED")
        print(e)
        return False


async def main():
    print("=== Checking connections ===")

    pg_ok = await check_postgres()
    mongo_ok = check_mongo()

    print("\n=== Result ===")
    print("Postgres:", "OK" if pg_ok else "FAIL")
    print("Mongo:   ", "OK" if mongo_ok else "FAIL")


if __name__ == "__main__":
    asyncio.run(main())