from __future__ import annotations

import asyncio

from meowbot.infra.mongo.async_client import AsyncMongoConfig, AsyncMongoConn


async def main() -> None:
    mongo = AsyncMongoConn(AsyncMongoConfig())
    try:
        await mongo.connect()
        ok = await mongo.ping()
        print("✅ Async Mongo connection successful" if ok else "❌ Async Mongo ping failed")
        print(f"DB name: {mongo.config.db_name}")
        print(f"Max pool size: {mongo.config.max_pool_size}")
        print(f"Min pool size: {mongo.config.min_pool_size}")
    finally:
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(main())